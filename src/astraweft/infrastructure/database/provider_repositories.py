"""SQLAlchemy repositories for Provider configuration and model catalogs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from astraweft.domain.provider import (
    CredentialMetadata,
    CredentialStoreType,
    CredentialType,
    Model,
    Provider,
    ProviderHealth,
)
from astraweft.infrastructure.database.models import (
    ModelRecord,
    ProviderCredentialRecord,
    ProviderRecord,
)


class OptimisticConcurrencyError(RuntimeError):
    """A stale write attempted to replace a newer Provider row."""


class SQLProviderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, provider: Provider) -> None:
        self._session.add(_provider_record(provider))

    async def get(self, provider_id: str, *, include_deleted: bool = False) -> Provider | None:
        statement = select(ProviderRecord).where(ProviderRecord.id == provider_id)
        if not include_deleted:
            statement = statement.where(ProviderRecord.deleted_at.is_(None))
        record = await self._session.scalar(statement)
        return None if record is None else _provider_entity(record)

    async def list(self, *, include_deleted: bool = False) -> tuple[Provider, ...]:
        statement = select(ProviderRecord)
        if not include_deleted:
            statement = statement.where(ProviderRecord.deleted_at.is_(None))
        statement = statement.order_by(ProviderRecord.created_at, ProviderRecord.name)
        records = (await self._session.scalars(statement)).all()
        return tuple(_provider_entity(record) for record in records)

    async def update(self, provider: Provider, *, expected_version: int) -> None:
        values = _provider_values(provider)
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(ProviderRecord)
                .where(
                    ProviderRecord.id == provider.id,
                    ProviderRecord.row_version == expected_version,
                )
                .values(**values)
            ),
        )
        if result.rowcount != 1:
            raise OptimisticConcurrencyError("Provider was updated by another operation")


class SQLCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, credential: CredentialMetadata) -> None:
        self._session.add(
            ProviderCredentialRecord(
                id=credential.id,
                store_type=credential.store_type.value,
                credential_ref=credential.credential_ref,
                credential_type=credential.credential_type.value,
                hint=credential.hint,
                metadata_json=_dump_json(credential.metadata),
                created_at=_time(credential.created_at),
                updated_at=_time(credential.updated_at),
            )
        )

    async def get(self, credential_id: str) -> CredentialMetadata | None:
        record = await self._session.get(ProviderCredentialRecord, credential_id)
        if record is None:
            return None
        return CredentialMetadata(
            id=record.id,
            store_type=CredentialStoreType(record.store_type),
            credential_ref=record.credential_ref,
            credential_type=CredentialType(record.credential_type),
            hint=record.hint,
            metadata=_load_mapping(record.metadata_json),
            created_at=_parse_time(record.created_at),
            updated_at=_parse_time(record.updated_at),
        )

    async def delete(self, credential_id: str) -> None:
        await self._session.execute(
            delete(ProviderCredentialRecord).where(ProviderCredentialRecord.id == credential_id)
        )


class SQLModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_provider(self, provider_id: str) -> tuple[Model, ...]:
        records = (
            await self._session.scalars(
                select(ModelRecord)
                .where(ModelRecord.provider_id == provider_id)
                .order_by(ModelRecord.display_name, ModelRecord.remote_model_id)
            )
        ).all()
        return tuple(_model_entity(record) for record in records)

    async def upsert(self, model: Model) -> None:
        record = await self._session.scalar(
            select(ModelRecord).where(
                ModelRecord.provider_id == model.provider_id,
                ModelRecord.remote_model_id == model.remote_model_id,
            )
        )
        if record is None:
            self._session.add(_model_record(model))
            return
        record.modality = model.modality
        record.operations_json = _dump_json(sorted(model.operations))
        record.parameter_schema_json = _dump_json(model.parameter_schema)
        record.parameter_ui_schema_json = _dump_json(model.parameter_ui_schema)
        record.output_schema_json = _dump_json(model.output_schema)
        record.capabilities_json = _dump_json(model.capabilities)
        record.pricing_json = _dump_json(model.pricing)
        record.source_hash = model.source_hash
        record.available = model.available
        record.deprecated = model.deprecated
        record.synced_at = _optional_time(model.synced_at)
        record.updated_at = _time(model.updated_at)

    async def mark_unavailable_except(
        self,
        provider_id: str,
        remote_model_ids: frozenset[str],
        *,
        synced_at: datetime,
    ) -> None:
        statement = update(ModelRecord).where(ModelRecord.provider_id == provider_id)
        if remote_model_ids:
            statement = statement.where(ModelRecord.remote_model_id.not_in(remote_model_ids))
        await self._session.execute(
            statement.values(
                available=False,
                deprecated=True,
                synced_at=_time(synced_at),
                updated_at=_time(synced_at),
            )
        )

    async def update_user_preferences(
        self,
        model_id: str,
        *,
        display_name: str,
        default_params: dict[str, object],
        enabled: bool,
        updated_at: datetime,
    ) -> None:
        await self._session.execute(
            update(ModelRecord)
            .where(ModelRecord.id == model_id)
            .values(
                display_name=display_name.strip(),
                default_params_json=_dump_json(default_params),
                enabled=enabled,
                updated_at=_time(updated_at),
            )
        )


def _provider_record(provider: Provider) -> ProviderRecord:
    return ProviderRecord(
        id=provider.id,
        plugin_id=provider.plugin_id,
        plugin_version=provider.plugin_version,
        name=provider.name,
        endpoint=provider.endpoint,
        enabled=provider.enabled,
        config_json=_dump_json(provider.config),
        credential_id=provider.credential_id,
        health_status=provider.health_status.value,
        last_checked_at=_optional_time(provider.last_checked_at),
        row_version=provider.row_version,
        created_at=_time(provider.created_at),
        updated_at=_time(provider.updated_at),
        deleted_at=_optional_time(provider.deleted_at),
    )


def _provider_values(provider: Provider) -> dict[str, object]:
    return {
        "id": provider.id,
        "plugin_id": provider.plugin_id,
        "plugin_version": provider.plugin_version,
        "name": provider.name,
        "endpoint": provider.endpoint,
        "enabled": provider.enabled,
        "config_json": _dump_json(provider.config),
        "credential_id": provider.credential_id,
        "health_status": provider.health_status.value,
        "last_checked_at": _optional_time(provider.last_checked_at),
        "row_version": provider.row_version,
        "created_at": _time(provider.created_at),
        "updated_at": _time(provider.updated_at),
        "deleted_at": _optional_time(provider.deleted_at),
    }


def _provider_entity(record: ProviderRecord) -> Provider:
    return Provider(
        id=record.id,
        plugin_id=record.plugin_id,
        plugin_version=record.plugin_version,
        name=record.name,
        endpoint=record.endpoint,
        enabled=record.enabled,
        config=_load_mapping(record.config_json),
        credential_id=record.credential_id,
        health_status=ProviderHealth(record.health_status),
        last_checked_at=_optional_parse_time(record.last_checked_at),
        row_version=record.row_version,
        created_at=_parse_time(record.created_at),
        updated_at=_parse_time(record.updated_at),
        deleted_at=_optional_parse_time(record.deleted_at),
    )


def _model_record(model: Model) -> ModelRecord:
    return ModelRecord(
        id=model.id,
        provider_id=model.provider_id,
        remote_model_id=model.remote_model_id,
        display_name=model.display_name,
        modality=model.modality,
        operations_json=_dump_json(sorted(model.operations)),
        parameter_schema_json=_dump_json(model.parameter_schema),
        parameter_ui_schema_json=_dump_json(model.parameter_ui_schema),
        output_schema_json=_dump_json(model.output_schema),
        capabilities_json=_dump_json(model.capabilities),
        pricing_json=_dump_json(model.pricing),
        default_params_json=_dump_json(model.default_params),
        source_hash=model.source_hash,
        enabled=model.enabled,
        available=model.available,
        deprecated=model.deprecated,
        synced_at=_optional_time(model.synced_at),
        created_at=_time(model.created_at),
        updated_at=_time(model.updated_at),
    )


def _model_entity(record: ModelRecord) -> Model:
    return Model(
        id=record.id,
        provider_id=record.provider_id,
        remote_model_id=record.remote_model_id,
        display_name=record.display_name,
        modality=record.modality,
        operations=frozenset(_load_sequence(record.operations_json)),
        parameter_schema=_load_mapping(record.parameter_schema_json),
        parameter_ui_schema=_load_mapping(record.parameter_ui_schema_json),
        output_schema=_load_mapping(record.output_schema_json),
        capabilities=_load_mapping(record.capabilities_json),
        pricing=tuple(
            cast(Mapping[str, object], value) for value in _load_sequence(record.pricing_json)
        ),
        default_params=_load_mapping(record.default_params_json),
        source_hash=record.source_hash,
        enabled=record.enabled,
        available=record.available,
        deprecated=record.deprecated,
        synced_at=_optional_parse_time(record.synced_at),
        created_at=_parse_time(record.created_at),
        updated_at=_parse_time(record.updated_at),
    )


def _dump_json(value: object) -> str:
    def thaw(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): thaw(child) for key, child in item.items()}
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            return [thaw(child) for child in item]
        return item

    return json.dumps(thaw(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_mapping(value: str) -> Mapping[str, object]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ValueError("database JSON value is not an object")
    return cast(dict[str, object], loaded)


def _load_sequence(value: str) -> Sequence[Any]:
    loaded = json.loads(value)
    if not isinstance(loaded, list):
        raise ValueError("database JSON value is not an array")
    return loaded


def _time(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("database timestamps must be timezone-aware")
    return value.isoformat()


def _optional_time(value: datetime | None) -> str | None:
    return None if value is None else _time(value)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("database timestamp has no timezone")
    return parsed


def _optional_parse_time(value: str | None) -> datetime | None:
    return None if value is None else _parse_time(value)
