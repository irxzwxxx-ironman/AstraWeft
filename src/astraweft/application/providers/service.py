"""Application orchestration for Provider instances and model catalogs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, cast

from jsonschema import Draft202012Validator

from astraweft.application.providers.commands import (
    CreateProvider,
    UpdateModelPreferences,
    UpdateProvider,
)
from astraweft.application.providers.events import ModelsSynced, ProviderChanged
from astraweft.domain.provider import (
    CredentialMetadata,
    CredentialStoreType,
    CredentialType,
    Model,
    Provider,
    ProviderHealth,
)
from astraweft.ports.provider_plugins import (
    PluginLoadState,
    PluginPreferenceStore,
    PluginRecord,
    ProviderContextFactory,
    ProviderPluginCatalog,
)
from astraweft.ports.providers import ProviderUnitOfWorkFactory
from astraweft.ports.runtime import Clock, IdGenerator
from astraweft.ports.secrets import SecretStore, SecretValue
from astraweft.ports.unit_of_work import PostCommitDispatchError
from astraweft_provider_sdk import (
    HealthCheckResult,
    ProviderAuthenticationError,
    ProviderClient,
    ProviderDescriptor,
    ProviderError,
    ProviderModel,
    ProviderRateLimitError,
    ProviderUnavailableError,
    SchemaContractError,
    validate_instance,
    validate_schema,
)


class ProviderNotFoundError(LookupError):
    """A Provider instance is absent or has been deleted."""


class ProviderInputError(ValueError):
    """Provider configuration is invalid and safe to show in the GUI."""


class ProviderOperationError(RuntimeError):
    """A plugin operation failed with a user-safe message."""

    def __init__(self, message: str, *, code: str = "provider_operation_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProviderTestResult:
    provider_id: str
    status: ProviderHealth
    message: str
    latency_ms: int | None


@dataclass(frozen=True, slots=True)
class PluginManagementEntry:
    record: PluginRecord
    provider_count: int
    enabled_provider_count: int
    model_count: int


@dataclass(frozen=True, slots=True)
class ProviderExecution:
    """Validated Provider/model snapshot plus one short-lived client."""

    provider: Provider
    model: Model
    descriptor: ProviderDescriptor
    client: ProviderClient
    allowed_network: tuple[str, ...]

    async def close(self) -> None:
        await self.client.close()


class ProviderService:
    """Keep plugin calls outside database transactions and secrets outside SQLite."""

    def __init__(
        self,
        *,
        plugins: ProviderPluginCatalog,
        uow_factory: ProviderUnitOfWorkFactory,
        secret_store: SecretStore,
        clock: Clock,
        ids: IdGenerator,
        provider_contexts: ProviderContextFactory,
        plugin_preferences: PluginPreferenceStore,
    ) -> None:
        self._plugins = plugins
        self._uow_factory = uow_factory
        self._secret_store = secret_store
        self._clock = clock
        self._ids = ids
        self._provider_contexts = provider_contexts
        self._plugin_preferences = plugin_preferences
        self._logger = logging.getLogger("astraweft.application.providers")

    def plugin_records(self) -> tuple[PluginRecord, ...]:
        return self._plugins.records()

    async def plugin_management(self) -> tuple[PluginManagementEntry, ...]:
        providers = await self.list_providers()
        entries: list[PluginManagementEntry] = []
        for record in self._plugins.records():
            plugin_id = record.manifest.plugin_id if record.manifest is not None else None
            matched = tuple(item for item in providers if item.plugin_id == plugin_id)
            model_count = 0
            for provider in matched:
                model_count += len(await self.list_models(provider.id))
            entries.append(
                PluginManagementEntry(
                    record=record,
                    provider_count=len(matched),
                    enabled_provider_count=sum(item.enabled for item in matched),
                    model_count=model_count,
                )
            )
        return tuple(entries)

    async def set_plugin_enabled(
        self,
        plugin_id: str,
        *,
        enabled: bool,
    ) -> PluginRecord:
        known_ids = {
            record.manifest.plugin_id
            for record in self._plugins.records()
            if record.manifest is not None
        }
        if plugin_id not in known_ids:
            raise ProviderNotFoundError("Provider 插件不存在")
        disabled = set(await asyncio.to_thread(self._plugin_preferences.load_disabled))
        if enabled:
            disabled.discard(plugin_id)
        else:
            disabled.add(plugin_id)
        frozen = frozenset(disabled)
        await asyncio.to_thread(self._plugin_preferences.save_disabled, frozen)
        records = await asyncio.to_thread(self._plugins.set_disabled, frozen)
        record = next(
            item
            for item in records
            if item.manifest is not None and item.manifest.plugin_id == plugin_id
        )
        if enabled and record.state is not PluginLoadState.READY:
            self._logger.warning(
                "provider_plugin_enable_failed",
                extra={"plugin_id": plugin_id, "plugin_state": record.state.value},
            )
        return record

    async def refresh_plugin_catalog(self) -> tuple[PluginRecord, ...]:
        disabled = await asyncio.to_thread(self._plugin_preferences.load_disabled)
        return await asyncio.to_thread(self._plugins.set_disabled, disabled)

    async def list_providers(self) -> tuple[Provider, ...]:
        async with self._uow_factory() as uow:
            return await uow.providers.list()

    async def list_models(self, provider_id: str | None = None) -> tuple[Model, ...]:
        async with self._uow_factory() as uow:
            if provider_id is not None:
                return await uow.models.get_for_provider(provider_id)
            providers = await uow.providers.list()
            models: list[Model] = []
            for provider in providers:
                models.extend(await uow.models.get_for_provider(provider.id))
            return tuple(models)

    async def concurrency_limit(self, provider_id: str) -> int:
        provider, _credential_ref = await self._get_provider_runtime(provider_id)
        return self._plugins.get(provider.plugin_id).descriptor.client_concurrency

    async def resolve_execution(
        self,
        provider_id: str,
        model_id: str,
        operation: str,
        *,
        allow_inactive: bool = False,
    ) -> ProviderExecution:
        """Resolve a safe execution without exposing credentials to callers."""
        provider, credential_ref = await self._get_provider_runtime(provider_id)
        if not allow_inactive and not provider.enabled:
            raise ProviderOperationError("Provider 已停用，不能创建新任务", code="disabled")
        async with self._uow_factory() as uow:
            models = await uow.models.get_for_provider(provider.id)
        model = next((item for item in models if item.id == model_id), None)
        if model is None:
            raise ProviderNotFoundError("模型不存在")
        if not allow_inactive and not model.enabled:
            raise ProviderOperationError("模型已停用，不能创建新任务", code="disabled")
        if not allow_inactive and (not model.available or model.deprecated):
            raise ProviderOperationError("模型当前不可用，请先同步模型目录", code="unavailable")
        if operation not in model.operations:
            raise ProviderInputError("所选模型不支持该操作")
        plugin = self._plugins.get(provider.plugin_id)
        if operation not in plugin.descriptor.operations:
            raise ProviderOperationError("Provider 未声明该操作", code="protocol_error")
        return ProviderExecution(
            provider=provider,
            model=model,
            descriptor=plugin.descriptor,
            client=self._create_client(provider, credential_ref),
            allowed_network=self._allowed_network(provider.plugin_id),
        )

    async def create(self, command: CreateProvider) -> Provider:
        plugin = self._plugins.get(command.plugin_id)
        descriptor = plugin.descriptor
        settings = self._validated_settings(command.settings, descriptor)
        credentials = self._validated_credentials(command.credentials, descriptor)
        name = _required_name(command.name)
        await self._assert_name_available(name)

        now = self._clock.now()
        provider_id = self._ids.new()
        credential = await self._store_new_credential(provider_id, credentials, now)
        provider = Provider(
            id=provider_id,
            plugin_id=descriptor.plugin_id,
            plugin_version=descriptor.version,
            name=name,
            endpoint=_optional_text(command.endpoint),
            enabled=command.enabled,
            config=settings,
            credential_id=credential.id if credential is not None else None,
            health_status=ProviderHealth.UNKNOWN,
            last_checked_at=None,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._uow_factory() as uow:
                if credential is not None:
                    await uow.credentials.add(credential)
                await uow.providers.add(provider)
                uow.publish_after_commit(ProviderChanged(provider.id, "created", now))
                await uow.commit()
        except PostCommitDispatchError:
            raise
        except Exception:
            if credential is not None:
                await self._best_effort_delete_values(credential)
            raise
        return provider

    async def update(self, command: UpdateProvider) -> Provider:
        async with self._uow_factory() as uow:
            current = await uow.providers.get(command.provider_id)
            if current is None:
                raise ProviderNotFoundError("Provider 不存在或已删除")
        plugin = self._plugins.get(current.plugin_id)
        descriptor = plugin.descriptor
        settings = self._validated_settings(command.settings, descriptor)
        name = _required_name(command.name)
        await self._assert_name_available(name, excluding_provider_id=current.id)

        new_credential: CredentialMetadata | None = None
        if command.credentials is not None:
            credentials = self._validated_credentials(command.credentials, descriptor)
            new_credential = await self._store_new_credential(
                current.id, credentials, self._clock.now()
            )

        old_credential: CredentialMetadata | None = None
        now = self._clock.now()
        updated = replace(
            current,
            plugin_version=descriptor.version,
            name=name,
            endpoint=_optional_text(command.endpoint),
            enabled=command.enabled,
            config=settings,
            credential_id=(
                new_credential.id if command.credentials is not None and new_credential else None
            )
            if command.credentials is not None
            else current.credential_id,
            updated_at=now,
            row_version=current.row_version + 1,
        )
        post_commit_error: PostCommitDispatchError | None = None
        try:
            async with self._uow_factory() as uow:
                latest = await uow.providers.get(current.id)
                if latest is None:
                    raise ProviderNotFoundError("Provider 不存在或已删除")
                if latest.credential_id is not None and command.credentials is not None:
                    old_credential = await uow.credentials.get(latest.credential_id)
                if new_credential is not None:
                    await uow.credentials.add(new_credential)
                if latest.row_version != current.row_version:
                    raise ProviderOperationError(
                        "Provider 已被其他操作修改，请刷新后重试",
                        code="concurrency_conflict",
                    )
                await uow.providers.update(updated, expected_version=latest.row_version)
                if old_credential is not None:
                    await uow.credentials.delete(old_credential.id)
                uow.publish_after_commit(ProviderChanged(updated.id, "updated", now))
                await uow.commit()
        except PostCommitDispatchError as exc:
            post_commit_error = exc
        except Exception:
            if new_credential is not None:
                await self._best_effort_delete_values(new_credential)
            raise
        if old_credential is not None:
            await self._best_effort_delete_values(old_credential)
        if post_commit_error is not None:
            raise post_commit_error
        return updated

    async def set_enabled(self, provider_id: str, enabled: bool) -> Provider:
        async with self._uow_factory() as uow:
            provider = await uow.providers.get(provider_id)
            if provider is None:
                raise ProviderNotFoundError("Provider 不存在或已删除")
            now = self._clock.now()
            updated = replace(
                provider,
                enabled=enabled,
                updated_at=now,
                row_version=provider.row_version + 1,
            )
            await uow.providers.update(updated, expected_version=provider.row_version)
            uow.publish_after_commit(ProviderChanged(provider.id, "updated", now))
            await uow.commit()
            return updated

    async def delete(self, provider_id: str) -> None:
        credential: CredentialMetadata | None = None
        post_commit_error: PostCommitDispatchError | None = None
        try:
            async with self._uow_factory() as uow:
                provider = await uow.providers.get(provider_id)
                if provider is None:
                    raise ProviderNotFoundError("Provider 不存在或已删除")
                if provider.credential_id is not None:
                    credential = await uow.credentials.get(provider.credential_id)
                now = self._clock.now()
                deleted = replace(
                    provider,
                    credential_id=None,
                    deleted_at=now,
                    updated_at=now,
                    row_version=provider.row_version + 1,
                )
                await uow.providers.update(deleted, expected_version=provider.row_version)
                if credential is not None:
                    await uow.credentials.delete(credential.id)
                uow.publish_after_commit(ProviderChanged(provider.id, "deleted", now))
                await uow.commit()
        except PostCommitDispatchError as exc:
            post_commit_error = exc
        if credential is not None:
            await self._best_effort_delete_values(credential)
        if post_commit_error is not None:
            raise post_commit_error

    async def test_connection(
        self, provider_id: str, *, timeout_seconds: float = 5.0
    ) -> ProviderTestResult:
        provider, credential_ref = await self._get_provider_runtime(provider_id)
        client = self._create_client(provider, credential_ref)
        health: HealthCheckResult | None = None
        failure: ProviderOperationError | None = None
        try:
            async with asyncio.timeout(timeout_seconds):
                health = await client.health_check()
        except TimeoutError:
            failure = ProviderOperationError("连接测试超时", code="timeout")
        except ProviderError as exc:
            failure = ProviderOperationError(exc.user_message, code=exc.code)
        except Exception:
            self._logger.exception(
                "provider_health_check_failed", extra={"provider_id": provider.id}
            )
            failure = ProviderOperationError("Provider 返回了无法识别的错误")
        finally:
            with suppress(Exception):
                await client.close()

        status = _health_status(health, failure)
        async with self._uow_factory() as uow:
            latest = await uow.providers.get(provider.id)
            if latest is None:
                raise ProviderNotFoundError("Provider 不存在或已删除")
            checked = latest.with_health(status, self._clock.now())
            await uow.providers.update(checked, expected_version=latest.row_version)
            uow.publish_after_commit(
                ProviderChanged(provider.id, "health_checked", checked.updated_at)
            )
            await uow.commit()
        if failure is not None:
            raise failure
        if health is None:  # pragma: no cover - guarded by the branches above
            raise ProviderOperationError("Provider 未返回连接状态")
        return ProviderTestResult(provider.id, status, health.message, health.latency_ms)

    async def sync_models(
        self, provider_id: str, *, timeout_seconds: float = 15.0
    ) -> tuple[Model, ...]:
        provider, credential_ref = await self._get_provider_runtime(provider_id)
        plugin = self._plugins.get(provider.plugin_id)
        if not plugin.descriptor.supports_model_discovery:
            raise ProviderOperationError("该 Provider 不支持模型目录同步", code="unsupported")
        client = self._create_client(provider, credential_ref)
        try:
            async with asyncio.timeout(timeout_seconds):
                remote_models = await client.list_models()
        except TimeoutError as exc:
            raise ProviderOperationError("模型同步超时", code="timeout") from exc
        except ProviderError as exc:
            raise ProviderOperationError(exc.user_message, code=exc.code) from None
        except Exception:
            self._logger.exception("provider_model_sync_failed", extra={"provider_id": provider.id})
            raise ProviderOperationError("Provider 返回了无法识别的模型目录") from None
        finally:
            with suppress(Exception):
                await client.close()
        self._validate_remote_models(remote_models, plugin.descriptor)

        synced_at = self._clock.now()
        async with self._uow_factory() as uow:
            existing = {
                model.remote_model_id: model
                for model in await uow.models.get_for_provider(provider.id)
            }
            synced: list[Model] = []
            for remote in remote_models:
                current = existing.get(remote.remote_model_id)
                model = self._to_model(provider.id, remote, current, synced_at)
                await uow.models.upsert(model)
                synced.append(model)
            await uow.models.mark_unavailable_except(
                provider.id,
                frozenset(model.remote_model_id for model in remote_models),
                synced_at=synced_at,
            )
            uow.publish_after_commit(ModelsSynced(provider.id, len(synced), synced_at))
            await uow.commit()
        return tuple(synced)

    async def update_model_preferences(self, command: UpdateModelPreferences) -> None:
        name = _required_name(command.display_name)
        async with self._uow_factory() as uow:
            models = await uow.models.get_for_provider(command.provider_id)
            if not any(model.id == command.model_id for model in models):
                raise ProviderNotFoundError("模型不存在")
            await uow.models.update_user_preferences(
                command.model_id,
                display_name=name,
                default_params=dict(command.default_params),
                enabled=command.enabled,
                updated_at=self._clock.now(),
            )
            await uow.commit()

    async def _get_provider_runtime(self, provider_id: str) -> tuple[Provider, str | None]:
        async with self._uow_factory() as uow:
            provider = await uow.providers.get(provider_id)
            if provider is None:
                raise ProviderNotFoundError("Provider 不存在或已删除")
            if provider.credential_id is None:
                return provider, None
            credential = await uow.credentials.get(provider.credential_id)
            if credential is None:
                raise ProviderOperationError("Provider 凭据元数据缺失", code="credential_missing")
            return provider, credential.credential_ref

    def _create_client(self, provider: Provider, credential_ref: str | None) -> ProviderClient:
        plugin = self._plugins.get(provider.plugin_id)
        allowed_network = self._allowed_network(provider.plugin_id)
        context = self._provider_contexts(provider.plugin_id, allowed_network)
        return plugin.create_client(context, provider.config, credential_ref)

    def _allowed_network(self, plugin_id: str) -> tuple[str, ...]:
        record = next(
            (
                item
                for item in self._plugins.records()
                if item.manifest is not None and item.manifest.plugin_id == plugin_id
            ),
            None,
        )
        return (
            () if record is None or record.manifest is None else record.manifest.permissions.network
        )

    async def _assert_name_available(
        self, name: str, *, excluding_provider_id: str | None = None
    ) -> None:
        providers = await self.list_providers()
        if any(item.name == name and item.id != excluding_provider_id for item in providers):
            raise ProviderInputError("Provider 名称已存在")

    def _validated_settings(
        self, settings: Mapping[str, object], descriptor: ProviderDescriptor
    ) -> dict[str, object]:
        if _contains_secret_marker(descriptor.settings_schema):
            raise ProviderInputError("插件错误地把密钥字段放进了普通设置")
        try:
            validate_instance(settings, descriptor.settings_schema)
        except SchemaContractError as exc:
            raise ProviderInputError(f"Provider 设置无效：{exc}") from None
        return dict(settings)

    def _validated_credentials(
        self,
        credentials: Mapping[str, SecretValue],
        descriptor: ProviderDescriptor,
    ) -> dict[str, SecretValue]:
        _assert_credential_schema_is_secret(descriptor.credential_schema)
        plain = {name: value.reveal() for name, value in credentials.items()}
        try:
            validator = Draft202012Validator(cast(Mapping[str, Any], descriptor.credential_schema))
            if next(validator.iter_errors(plain), None) is not None:
                raise ProviderInputError("凭据字段不完整或格式不正确")
        finally:
            plain.clear()
        return dict(credentials)

    async def _store_new_credential(
        self,
        provider_id: str,
        credentials: Mapping[str, SecretValue],
        now: datetime,
    ) -> CredentialMetadata | None:
        if not credentials:
            return None
        credential_id = self._ids.new()
        credential_ref = _credential_ref(provider_id, credential_id)
        stored_fields: list[str] = []
        try:
            for field_name, value in credentials.items():
                await self._secret_store.set(credential_ref, field_name, value)
                stored_fields.append(field_name)
        except Exception:
            for field_name in stored_fields:
                with suppress(Exception):
                    await self._secret_store.delete(credential_ref, field_name)
            raise
        first = next(iter(credentials.values()))
        revealed = first.reveal()
        hint = f"••••{revealed[-4:]}" if revealed else None
        return CredentialMetadata(
            id=credential_id,
            store_type=(
                CredentialStoreType.KEYRING
                if self._secret_store.persistent
                else CredentialStoreType.SESSION
            ),
            credential_ref=credential_ref,
            credential_type=(
                CredentialType.API_KEY
                if "api_key" in credentials
                else CredentialType.SERVICE_ACCOUNT
            ),
            hint=hint,
            metadata={"fields": sorted(credentials)},
            created_at=now,
            updated_at=now,
        )

    async def _best_effort_delete_values(self, credential: CredentialMetadata) -> None:
        fields = credential.metadata.get("fields", ())
        if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes, bytearray)):
            fields = ()
        for field_name in fields:
            if not isinstance(field_name, str):
                continue
            try:
                await self._secret_store.delete(credential.credential_ref, field_name)
            except Exception:
                self._logger.exception(
                    "credential_cleanup_failed",
                    extra={"credential_id": credential.id, "field_name": field_name},
                )

    @staticmethod
    def _validate_remote_models(
        remote_models: tuple[ProviderModel, ...], descriptor: ProviderDescriptor
    ) -> None:
        ids = [model.remote_model_id for model in remote_models]
        if len(ids) != len(set(ids)):
            raise ProviderOperationError("Provider 返回了重复的模型 ID", code="protocol_error")
        for model in remote_models:
            if not model.operations <= descriptor.operations:
                raise ProviderOperationError(
                    "Provider 模型声明了未注册的能力", code="protocol_error"
                )
            for schema in (
                model.parameter_schema,
                model.parameter_ui_schema,
                model.output_schema,
            ):
                try:
                    validate_schema(schema)
                except SchemaContractError as exc:
                    raise ProviderOperationError(
                        "Provider 模型包含无效 Schema", code="protocol_error"
                    ) from exc

    def _to_model(
        self,
        provider_id: str,
        remote: ProviderModel,
        current: Model | None,
        synced_at: datetime,
    ) -> Model:
        created_at = current.created_at if current is not None else synced_at
        return Model(
            id=current.id if current is not None else self._ids.new(),
            provider_id=provider_id,
            remote_model_id=remote.remote_model_id,
            display_name=current.display_name if current is not None else remote.display_name,
            modality=remote.modality,
            operations=remote.operations,
            parameter_schema=remote.parameter_schema,
            parameter_ui_schema=remote.parameter_ui_schema,
            output_schema=remote.output_schema,
            capabilities=remote.capabilities,
            pricing=tuple(
                {
                    "unit": rule.unit,
                    "price_micros": rule.price_micros,
                    "currency": rule.currency,
                    "effective_at": rule.effective_at,
                }
                for rule in remote.pricing
            ),
            default_params=current.default_params if current is not None else {},
            source_hash=_model_source_hash(remote),
            enabled=current.enabled if current is not None else True,
            available=True,
            deprecated=remote.deprecated,
            synced_at=synced_at,
            created_at=created_at,
            updated_at=synced_at,
        )


def _health_status(
    result: HealthCheckResult | None, failure: ProviderOperationError | None
) -> ProviderHealth:
    if result is not None:
        return {
            "healthy": ProviderHealth.HEALTHY,
            "degraded": ProviderHealth.DEGRADED,
            "unavailable": ProviderHealth.UNAVAILABLE,
        }[result.status]
    if failure is not None and failure.code in {
        ProviderAuthenticationError.default_code,
        ProviderRateLimitError.default_code,
        ProviderUnavailableError.default_code,
        "timeout",
    }:
        return ProviderHealth.UNAVAILABLE
    return ProviderHealth.DEGRADED


def _required_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ProviderInputError("名称不能为空")
    if len(normalized) > 160:
        raise ProviderInputError("名称不能超过 160 个字符")
    return normalized


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _credential_ref(provider_id: str, credential_id: str) -> str:
    return f"providers/{provider_id}/{credential_id}"


def _contains_secret_marker(value: object) -> bool:
    if isinstance(value, Mapping):
        if value.get("x-astraweft-secret") is True:
            return True
        return any(_contains_secret_marker(child) for child in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_secret_marker(child) for child in value)
    return False


def _assert_credential_schema_is_secret(schema: Mapping[str, object]) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        raise ProviderInputError("插件凭据 Schema 缺少 properties")
    for name, value in properties.items():
        if not isinstance(name, str) or not isinstance(value, Mapping):
            raise ProviderInputError("插件凭据 Schema 无效")
        if value.get("x-astraweft-secret") is not True:
            raise ProviderInputError("插件凭据字段未标记为密钥")


def _model_source_hash(model: ProviderModel) -> str:
    payload = {
        "remote_model_id": model.remote_model_id,
        "display_name": model.display_name,
        "modality": model.modality,
        "operations": sorted(model.operations),
        "parameter_schema": _plain_json(model.parameter_schema),
        "parameter_ui_schema": _plain_json(model.parameter_ui_schema),
        "output_schema": _plain_json(model.output_schema),
        "capabilities": _plain_json(model.capabilities),
        "pricing": [
            {
                "unit": rule.unit,
                "price_micros": rule.price_micros,
                "currency": rule.currency,
                "effective_at": rule.effective_at,
            }
            for rule in model.pricing
        ],
        "deprecated": model.deprecated,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(child) for child in value]
    return value
