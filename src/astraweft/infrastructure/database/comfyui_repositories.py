"""SQLAlchemy repositories for ComfyUI configuration and execution facts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from astraweft.domain.comfyui import (
    ComfyUIExecution,
    ComfyUIExecutionStatus,
    ComfyUIHealth,
    ComfyUIInstance,
    ComfyUITemplate,
)
from astraweft.infrastructure.database.models import (
    ComfyUIExecutionRecord,
    ComfyUIInstanceRecord,
    ComfyUITemplateRecord,
)


class ComfyUIOptimisticConcurrencyError(RuntimeError):
    """A stale ComfyUI write attempted to replace a newer fact."""


class SQLComfyUIInstanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, instance: ComfyUIInstance) -> None:
        self._session.add(ComfyUIInstanceRecord(**_instance_values(instance)))

    async def get(
        self,
        instance_id: str,
        *,
        include_deleted: bool = False,
    ) -> ComfyUIInstance | None:
        statement = select(ComfyUIInstanceRecord).where(ComfyUIInstanceRecord.id == instance_id)
        if not include_deleted:
            statement = statement.where(ComfyUIInstanceRecord.deleted_at.is_(None))
        record = await self._session.scalar(statement)
        return None if record is None else _instance_entity(record)

    async def list(self, *, include_deleted: bool = False) -> tuple[ComfyUIInstance, ...]:
        statement = select(ComfyUIInstanceRecord)
        if not include_deleted:
            statement = statement.where(ComfyUIInstanceRecord.deleted_at.is_(None))
        records = (
            await self._session.scalars(
                statement.order_by(
                    ComfyUIInstanceRecord.created_at,
                    ComfyUIInstanceRecord.name,
                )
            )
        ).all()
        return tuple(_instance_entity(record) for record in records)

    async def update(self, instance: ComfyUIInstance, *, expected_version: int) -> None:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(ComfyUIInstanceRecord)
                .where(
                    ComfyUIInstanceRecord.id == instance.id,
                    ComfyUIInstanceRecord.row_version == expected_version,
                )
                .values(**_instance_values(instance))
            ),
        )
        if result.rowcount != 1:
            raise ComfyUIOptimisticConcurrencyError(
                "ComfyUI instance was updated by another operation"
            )


class SQLComfyUITemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, template: ComfyUITemplate) -> None:
        self._session.add(ComfyUITemplateRecord(**_template_values(template)))

    async def get(self, template_id: str) -> ComfyUITemplate | None:
        record = await self._session.get(ComfyUITemplateRecord, template_id)
        return None if record is None else _template_entity(record)

    async def list_for_instance(self, instance_id: str) -> tuple[ComfyUITemplate, ...]:
        records = (
            await self._session.scalars(
                select(ComfyUITemplateRecord)
                .where(ComfyUITemplateRecord.instance_id == instance_id)
                .order_by(ComfyUITemplateRecord.created_at, ComfyUITemplateRecord.name)
            )
        ).all()
        return tuple(_template_entity(record) for record in records)

    async def update(self, template: ComfyUITemplate, *, expected_version: int) -> None:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(ComfyUITemplateRecord)
                .where(
                    ComfyUITemplateRecord.id == template.id,
                    ComfyUITemplateRecord.row_version == expected_version,
                )
                .values(**_template_values(template))
            ),
        )
        if result.rowcount != 1:
            raise ComfyUIOptimisticConcurrencyError(
                "ComfyUI template was updated by another operation"
            )


class SQLComfyUIExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, execution: ComfyUIExecution) -> None:
        self._session.add(ComfyUIExecutionRecord(**_execution_values(execution)))

    async def get(self, execution_id: str) -> ComfyUIExecution | None:
        record = await self._session.get(ComfyUIExecutionRecord, execution_id)
        return None if record is None else _execution_entity(record)

    async def get_for_node_run(self, node_run_id: str) -> ComfyUIExecution | None:
        record = await self._session.scalar(
            select(ComfyUIExecutionRecord).where(ComfyUIExecutionRecord.node_run_id == node_run_id)
        )
        return None if record is None else _execution_entity(record)

    async def list_by_status(
        self,
        statuses: frozenset[ComfyUIExecutionStatus],
        *,
        limit: int = 1000,
    ) -> tuple[ComfyUIExecution, ...]:
        if not statuses:
            return ()
        records = (
            await self._session.scalars(
                select(ComfyUIExecutionRecord)
                .where(ComfyUIExecutionRecord.status.in_([status.value for status in statuses]))
                .order_by(
                    ComfyUIExecutionRecord.poll_after_at,
                    ComfyUIExecutionRecord.created_at,
                )
                .limit(limit)
            )
        ).all()
        return tuple(_execution_entity(record) for record in records)

    async def list_recent(self, *, limit: int = 1000) -> tuple[ComfyUIExecution, ...]:
        records = (
            await self._session.scalars(
                select(ComfyUIExecutionRecord)
                .order_by(ComfyUIExecutionRecord.created_at.desc())
                .limit(limit)
            )
        ).all()
        return tuple(_execution_entity(record) for record in records)

    async def update(self, execution: ComfyUIExecution, *, expected_version: int) -> None:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(ComfyUIExecutionRecord)
                .where(
                    ComfyUIExecutionRecord.id == execution.id,
                    ComfyUIExecutionRecord.row_version == expected_version,
                )
                .values(**_execution_values(execution))
            ),
        )
        if result.rowcount != 1:
            raise ComfyUIOptimisticConcurrencyError(
                "ComfyUI execution was updated by another worker"
            )


def _instance_values(instance: ComfyUIInstance) -> dict[str, object]:
    return {
        "id": instance.id,
        "name": instance.name,
        "base_url": instance.base_url,
        "enabled": instance.enabled,
        "health": instance.health.value,
        "version": instance.version,
        "python_version": instance.python_version,
        "capabilities_json": _dump_json(instance.capabilities),
        "node_catalog_hash": instance.node_catalog_hash,
        "last_error_code": instance.last_error_code,
        "last_checked_at": _optional_time(instance.last_checked_at),
        "row_version": instance.row_version,
        "created_at": _time(instance.created_at),
        "updated_at": _time(instance.updated_at),
        "deleted_at": _optional_time(instance.deleted_at),
    }


def _instance_entity(record: ComfyUIInstanceRecord) -> ComfyUIInstance:
    return ComfyUIInstance(
        id=record.id,
        name=record.name,
        base_url=record.base_url,
        enabled=record.enabled,
        health=ComfyUIHealth(record.health),
        version=record.version,
        python_version=record.python_version,
        capabilities=_load_mapping(record.capabilities_json),
        node_catalog_hash=record.node_catalog_hash,
        last_error_code=record.last_error_code,
        last_checked_at=_optional_parse_time(record.last_checked_at),
        row_version=record.row_version,
        created_at=_parse_time(record.created_at),
        updated_at=_parse_time(record.updated_at),
        deleted_at=_optional_parse_time(record.deleted_at),
    )


def _template_values(template: ComfyUITemplate) -> dict[str, object]:
    return {
        "id": template.id,
        "instance_id": template.instance_id,
        "name": template.name,
        "prompt_json": _dump_json(template.prompt),
        "checksum": template.checksum,
        "input_schema_json": _dump_json(template.input_schema),
        "input_targets_json": _dump_json(template.input_targets),
        "output_nodes_json": _dump_json(template.output_nodes),
        "row_version": template.row_version,
        "created_at": _time(template.created_at),
        "updated_at": _time(template.updated_at),
    }


def _template_entity(record: ComfyUITemplateRecord) -> ComfyUITemplate:
    return ComfyUITemplate(
        id=record.id,
        instance_id=record.instance_id,
        name=record.name,
        prompt=_load_mapping(record.prompt_json),
        checksum=record.checksum,
        input_schema=_load_mapping(record.input_schema_json),
        input_targets=_load_mapping(record.input_targets_json),
        output_nodes=tuple(str(value) for value in _load_sequence(record.output_nodes_json)),
        row_version=record.row_version,
        created_at=_parse_time(record.created_at),
        updated_at=_parse_time(record.updated_at),
    )


def _execution_values(execution: ComfyUIExecution) -> dict[str, object]:
    return {
        "id": execution.id,
        "node_run_id": execution.node_run_id,
        "instance_id": execution.instance_id,
        "template_id": execution.template_id,
        "template_checksum": execution.template_checksum,
        "workflow_checksum": execution.workflow_checksum,
        "prompt_json": _dump_json(execution.prompt),
        "output_nodes_json": _dump_json(execution.output_nodes),
        "client_id": execution.client_id,
        "status": execution.status.value,
        "remote_prompt_id": execution.remote_prompt_id,
        "progress": execution.progress,
        "output_json": _optional_json(execution.output),
        "artifact_ids_json": _dump_json(execution.artifact_ids),
        "error_code": execution.error_code,
        "error_message": execution.error_message,
        "poll_after_at": _optional_time(execution.poll_after_at),
        "timeout_at": _time(execution.timeout_at),
        "row_version": execution.row_version,
        "created_at": _time(execution.created_at),
        "updated_at": _time(execution.updated_at),
        "started_at": _optional_time(execution.started_at),
        "completed_at": _optional_time(execution.completed_at),
    }


def _execution_entity(record: ComfyUIExecutionRecord) -> ComfyUIExecution:
    return ComfyUIExecution(
        id=record.id,
        node_run_id=record.node_run_id,
        instance_id=record.instance_id,
        template_id=record.template_id,
        template_checksum=record.template_checksum,
        workflow_checksum=record.workflow_checksum,
        prompt=_load_mapping(record.prompt_json),
        output_nodes=tuple(str(value) for value in _load_sequence(record.output_nodes_json)),
        client_id=record.client_id,
        status=ComfyUIExecutionStatus(record.status),
        remote_prompt_id=record.remote_prompt_id,
        progress=record.progress,
        output=_optional_mapping(record.output_json),
        artifact_ids=tuple(str(value) for value in _load_sequence(record.artifact_ids_json)),
        error_code=record.error_code,
        error_message=record.error_message,
        poll_after_at=_optional_parse_time(record.poll_after_at),
        timeout_at=_parse_time(record.timeout_at),
        row_version=record.row_version,
        created_at=_parse_time(record.created_at),
        updated_at=_parse_time(record.updated_at),
        started_at=_optional_parse_time(record.started_at),
        completed_at=_optional_parse_time(record.completed_at),
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


def _optional_mapping(value: str | None) -> Mapping[str, object] | None:
    return None if value is None else _load_mapping(value)


def _load_sequence(value: str) -> Sequence[Any]:
    loaded = json.loads(value)
    if not isinstance(loaded, list):
        raise ValueError("database JSON value is not an array")
    return loaded


def _optional_json(value: object | None) -> str | None:
    return None if value is None else _dump_json(value)


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
