"""End-to-end Provider configuration, secret, health, and sync tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from astraweft.application.providers import (
    CreateProvider,
    ProviderChanged,
    ProviderInputError,
    ProviderOperationError,
    UpdateModelPreferences,
    UpdateProvider,
)
from astraweft.bootstrap.container import build_app_context
from astraweft.domain.provider import ProviderHealth
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.secrets import SecretNotFoundError, SecretValue
from astraweft.ports.unit_of_work import PostCommitDispatchError

_PLUGIN_ID = "dev.astraweft.mock-provider"
_CANARY = "mock-valid-key"


class PersistentMemorySecrets(SessionSecretStore):
    @property
    def persistent(self) -> bool:
        return True


def _database_text(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        output: list[str] = []
        for query in (
            "SELECT * FROM app_settings",
            "SELECT * FROM models",
            "SELECT * FROM provider_credentials",
            "SELECT * FROM providers",
        ):
            output.extend(str(row) for row in connection.execute(query))
        return "\n".join(output)
    finally:
        connection.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_provider_survives_restart_and_model_sync_preserves_user_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = PersistentMemorySecrets()
    monkeypatch.setattr("astraweft.bootstrap.container.create_secret_store", lambda: secrets)

    first = await build_app_context(tmp_path)
    service = first.provider_service
    command = CreateProvider(
        plugin_id=_PLUGIN_ID,
        name="本地 Mock",
        settings={"catalog_revision": 1},
        credentials={"api_key": SecretValue(_CANARY)},
    )
    assert _CANARY not in repr(command)
    provider = await service.create(command)
    health = await service.test_connection(provider.id)
    initial = await service.sync_models(provider.id)
    text_model = next(model for model in initial if model.remote_model_id == "mock-text-v1")
    await service.update_model_preferences(
        UpdateModelPreferences(
            provider_id=provider.id,
            model_id=text_model.id,
            display_name="我的文本模型",
            default_params={"temperature": 0.25},
            enabled=False,
        )
    )
    await service.update(
        UpdateProvider(
            provider_id=provider.id,
            name=provider.name,
            settings={"catalog_revision": 2},
            endpoint=None,
            enabled=True,
        )
    )
    await service.sync_models(provider.id)

    assert health.status is ProviderHealth.HEALTHY
    assert _CANARY not in _database_text(first.paths.database_path)
    assert _CANARY not in first.log_path.read_text(encoding="utf-8")
    await first.close()

    second = await build_app_context(tmp_path)
    try:
        restarted_provider = (await second.provider_service.list_providers())[0]
        restarted_models = await second.provider_service.list_models(restarted_provider.id)
        by_remote_id = {model.remote_model_id: model for model in restarted_models}

        assert restarted_provider.id == provider.id
        assert (
            await second.provider_service.test_connection(provider.id)
        ).status is ProviderHealth.HEALTHY
        assert by_remote_id["mock-text-v1"].display_name == "我的文本模型"
        assert by_remote_id["mock-text-v1"].default_params["temperature"] == 0.25
        assert by_remote_id["mock-text-v1"].enabled is False
        assert by_remote_id["mock-image-v1"].available is False
        assert by_remote_id["mock-image-v1"].deprecated is True
        assert by_remote_id["mock-video-v1"].available is True
    finally:
        await second.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failed_health_is_persisted_and_delete_removes_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = PersistentMemorySecrets()
    monkeypatch.setattr("astraweft.bootstrap.container.create_secret_store", lambda: secrets)
    context = await build_app_context(tmp_path)
    service = context.provider_service
    try:
        provider = await service.create(
            CreateProvider(
                plugin_id=_PLUGIN_ID,
                name="Fault Mock",
                settings={"mode": "authentication_error"},
                credentials={"api_key": SecretValue(_CANARY)},
            )
        )
        connection = sqlite3.connect(context.paths.database_path)
        try:
            credential_ref = connection.execute(
                "SELECT credential_ref FROM provider_credentials"
            ).fetchone()[0]
        finally:
            connection.close()

        with pytest.raises(ProviderOperationError) as error:
            await service.test_connection(provider.id)
        stored = (await service.list_providers())[0]
        assert error.value.code == "authentication_error"
        assert stored.health_status is ProviderHealth.UNAVAILABLE
        assert _CANARY not in str(error.value)

        await service.delete(provider.id)
        assert await service.list_providers() == ()
        with pytest.raises(SecretNotFoundError):
            await secrets.get(credential_ref, "api_key")
        assert _CANARY not in _database_text(context.paths.database_path)
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_commit_event_failure_keeps_committed_secret_consistent(
    tmp_path: Path,
) -> None:
    secrets = PersistentMemorySecrets()
    context = await build_app_context(tmp_path, secret_store_override=secrets)

    def fail_event(_event: ProviderChanged) -> None:
        raise RuntimeError("refresh failed")

    unsubscribe = context.events.subscribe(ProviderChanged, fail_event)
    try:
        with pytest.raises(PostCommitDispatchError):
            await context.provider_service.create(
                CreateProvider(
                    plugin_id=_PLUGIN_ID,
                    name="Committed Mock",
                    settings={},
                    credentials={"api_key": SecretValue(_CANARY)},
                )
            )
        provider = (await context.provider_service.list_providers())[0]
        connection = sqlite3.connect(context.paths.database_path)
        try:
            credential_ref = connection.execute(
                "SELECT credential_ref FROM provider_credentials"
            ).fetchone()[0]
        finally:
            connection.close()
        assert (await secrets.get(credential_ref, "api_key")).reveal() == _CANARY

        unsubscribe()
        await context.provider_service.delete(provider.id)
        with pytest.raises(SecretNotFoundError):
            await secrets.get(credential_ref, "api_key")
    finally:
        unsubscribe()
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_execution_resolution_enforces_new_task_availability(
    tmp_path: Path,
) -> None:
    context = await build_app_context(
        tmp_path,
        secret_store_override=SessionSecretStore(),
    )
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id=_PLUGIN_ID,
                name="Execution Guard Mock",
                settings={"catalog_revision": 1},
                credentials={"api_key": SecretValue(_CANARY)},
            )
        )
        models = await context.provider_service.sync_models(provider.id)
        text_model = next(item for item in models if item.remote_model_id == "mock-text-v1")
        image_model = next(item for item in models if item.remote_model_id == "mock-image-v1")

        assert await context.provider_service.concurrency_limit(provider.id) == 4
        with pytest.raises(ProviderInputError, match="不支持"):
            await context.provider_service.resolve_execution(
                provider.id, text_model.id, "image.generate"
            )

        await context.provider_service.set_enabled(provider.id, False)
        with pytest.raises(ProviderOperationError, match="已停用"):
            await context.provider_service.resolve_execution(
                provider.id, text_model.id, "text.generate"
            )
        inactive = await context.provider_service.resolve_execution(
            provider.id,
            text_model.id,
            "text.generate",
            allow_inactive=True,
        )
        await inactive.close()

        await context.provider_service.set_enabled(provider.id, True)
        await context.provider_service.update_model_preferences(
            UpdateModelPreferences(
                provider_id=provider.id,
                model_id=text_model.id,
                display_name=text_model.display_name,
                default_params={},
                enabled=False,
            )
        )
        with pytest.raises(ProviderOperationError, match="模型已停用"):
            await context.provider_service.resolve_execution(
                provider.id, text_model.id, "text.generate"
            )

        await context.provider_service.update(
            UpdateProvider(
                provider_id=provider.id,
                name=provider.name,
                settings={"catalog_revision": 2},
                endpoint=None,
                enabled=True,
            )
        )
        await context.provider_service.sync_models(provider.id)
        with pytest.raises(ProviderOperationError, match="不可用"):
            await context.provider_service.resolve_execution(
                provider.id, image_model.id, "image.generate"
            )
    finally:
        await context.close()
