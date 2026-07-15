"""SQLAlchemy repositories for the durable task runtime."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from astraweft.domain.task import (
    Artifact,
    AttemptPhase,
    AttemptStatus,
    RequestLog,
    Task,
    TaskAttempt,
    TaskStatus,
)
from astraweft.infrastructure.database.models import (
    ArtifactLinkRecord,
    ArtifactRecord,
    RequestLogRecord,
    TaskAttemptRecord,
    TaskRecord,
)


class TaskOptimisticConcurrencyError(RuntimeError):
    """A stale worker attempted to replace a newer execution fact."""


class SQLTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, task: Task) -> None:
        self._session.add(_task_record(task))
        # Relationships are intentionally absent from persistence records; flush the
        # parent so later Attempt/Artifact inserts in this transaction are ordered.
        await self._session.flush()

    async def get(self, task_id: str) -> Task | None:
        record = await self._session.get(TaskRecord, task_id)
        return None if record is None else _task_entity(record)

    async def get_by_idempotency_key(self, idempotency_key: str) -> Task | None:
        record = await self._session.scalar(
            select(TaskRecord).where(TaskRecord.idempotency_key == idempotency_key)
        )
        return None if record is None else _task_entity(record)

    async def list_recent(self, *, limit: int = 1000) -> tuple[Task, ...]:
        records = (
            await self._session.scalars(
                select(TaskRecord).order_by(TaskRecord.created_at.desc()).limit(limit)
            )
        ).all()
        return tuple(_task_entity(record) for record in records)

    async def list_by_status(
        self,
        statuses: frozenset[TaskStatus],
        *,
        limit: int = 1000,
    ) -> tuple[Task, ...]:
        if not statuses:
            return ()
        records = (
            await self._session.scalars(
                select(TaskRecord)
                .where(TaskRecord.status.in_([status.value for status in statuses]))
                .order_by(TaskRecord.priority, TaskRecord.created_at)
                .limit(limit)
            )
        ).all()
        return tuple(_task_entity(record) for record in records)

    async def list_ready(self, at: datetime, *, limit: int) -> tuple[Task, ...]:
        at_value = _time(at)
        records = (
            await self._session.scalars(
                select(TaskRecord)
                .where(
                    or_(
                        TaskRecord.status == TaskStatus.QUEUED.value,
                        TaskRecord.status.in_(
                            [TaskStatus.POLLING.value, TaskStatus.RETRY_WAIT.value]
                        )
                        & or_(
                            TaskRecord.poll_after_at.is_(None),
                            TaskRecord.poll_after_at <= at_value,
                        ),
                    )
                )
                .order_by(TaskRecord.priority, TaskRecord.created_at)
                .limit(limit)
            )
        ).all()
        return tuple(_task_entity(record) for record in records)

    async def update(self, task: Task, *, expected_version: int) -> None:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(TaskRecord)
                .where(
                    TaskRecord.id == task.id,
                    TaskRecord.row_version == expected_version,
                )
                .values(**_task_values(task))
            ),
        )
        if result.rowcount != 1:
            raise TaskOptimisticConcurrencyError("Task was updated by another worker")


class SQLTaskAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, attempt: TaskAttempt) -> None:
        self._session.add(_attempt_record(attempt))
        # RequestLog can reference this Attempt in the same transaction.
        await self._session.flush()

    async def get(self, attempt_id: str) -> TaskAttempt | None:
        record = await self._session.get(TaskAttemptRecord, attempt_id)
        return None if record is None else _attempt_entity(record)

    async def next_attempt_no(self, task_id: str) -> int:
        current = await self._session.scalar(
            select(func.max(TaskAttemptRecord.attempt_no)).where(
                TaskAttemptRecord.task_id == task_id
            )
        )
        return int(current or 0) + 1

    async def list_for_task(self, task_id: str) -> tuple[TaskAttempt, ...]:
        records = (
            await self._session.scalars(
                select(TaskAttemptRecord)
                .where(TaskAttemptRecord.task_id == task_id)
                .order_by(TaskAttemptRecord.attempt_no, TaskAttemptRecord.started_at)
            )
        ).all()
        return tuple(_attempt_entity(record) for record in records)

    async def update(
        self,
        attempt: TaskAttempt,
        *,
        expected_status: AttemptStatus,
    ) -> None:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(TaskAttemptRecord)
                .where(
                    TaskAttemptRecord.id == attempt.id,
                    TaskAttemptRecord.status == expected_status.value,
                )
                .values(**_attempt_values(attempt))
            ),
        )
        if result.rowcount != 1:
            raise TaskOptimisticConcurrencyError("Attempt was updated by another worker")


class SQLRequestLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, request_log: RequestLog) -> None:
        self._session.add(_request_log_record(request_log))

    async def list_recent(self, *, limit: int = 1000) -> tuple[RequestLog, ...]:
        records = (
            await self._session.scalars(
                select(RequestLogRecord).order_by(RequestLogRecord.created_at.desc()).limit(limit)
            )
        ).all()
        return tuple(_request_log_entity(record) for record in records)

    async def delete_before(self, cutoff: datetime) -> int:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                delete(RequestLogRecord).where(RequestLogRecord.created_at < _time(cutoff))
            ),
        )
        return int(result.rowcount or 0)


class SQLArtifactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, artifact: Artifact) -> None:
        self._session.add(_artifact_record(artifact))

    async def get(self, artifact_id: str) -> Artifact | None:
        record = await self._session.get(ArtifactRecord, artifact_id)
        return None if record is None else _artifact_entity(record)

    async def list_for_task(self, task_id: str) -> tuple[Artifact, ...]:
        records = (
            await self._session.scalars(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.task_id == task_id,
                    ArtifactRecord.deleted_at.is_(None),
                )
                .order_by(ArtifactRecord.created_at)
            )
        ).all()
        return tuple(_artifact_entity(record) for record in records)

    async def list_recent(self, *, limit: int = 1000) -> tuple[Artifact, ...]:
        records = (
            await self._session.scalars(
                select(ArtifactRecord)
                .where(ArtifactRecord.deleted_at.is_(None))
                .order_by(ArtifactRecord.created_at.desc())
                .limit(limit)
            )
        ).all()
        return tuple(_artifact_entity(record) for record in records)

    async def list_trashed(self, *, limit: int = 1000) -> tuple[Artifact, ...]:
        records = (
            await self._session.scalars(
                select(ArtifactRecord)
                .where(ArtifactRecord.deleted_at.is_not(None))
                .order_by(ArtifactRecord.deleted_at.desc())
                .limit(limit)
            )
        ).all()
        return tuple(_artifact_entity(record) for record in records)

    async def workflow_reference_count(self, artifact_id: str) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(ArtifactLinkRecord)
            .where(ArtifactLinkRecord.artifact_id == artifact_id)
        )
        return int(count or 0)

    async def update_deleted_at(self, artifact: Artifact) -> None:
        await self._session.execute(
            update(ArtifactRecord)
            .where(ArtifactRecord.id == artifact.id)
            .values(deleted_at=_optional_time(artifact.deleted_at))
        )

    async def delete(self, artifact_id: str) -> None:
        await self._session.execute(delete(ArtifactRecord).where(ArtifactRecord.id == artifact_id))


def _task_record(task: Task) -> TaskRecord:
    return TaskRecord(**_task_values(task))


def _task_values(task: Task) -> dict[str, object]:
    return {
        "id": task.id,
        "provider_id": task.provider_id,
        "model_id": task.model_id,
        "status": task.status.value,
        "operation": task.operation,
        "input_json": _dump_json(task.input),
        "provider_config_snapshot_json": _dump_json(task.provider_config_snapshot),
        "normalized_output_json": _optional_json(task.normalized_output),
        "remote_task_id": task.remote_task_id,
        "idempotency_key": task.idempotency_key,
        "priority": task.priority,
        "progress": task.progress,
        "poll_after_at": _optional_time(task.poll_after_at),
        "timeout_at": _optional_time(task.timeout_at),
        "cancel_requested_at": _optional_time(task.cancel_requested_at),
        "row_version": task.row_version,
        "created_at": _time(task.created_at),
        "updated_at": _time(task.updated_at),
        "started_at": _optional_time(task.started_at),
        "completed_at": _optional_time(task.completed_at),
    }


def _task_entity(record: TaskRecord) -> Task:
    return Task(
        id=record.id,
        provider_id=record.provider_id,
        model_id=record.model_id,
        status=TaskStatus(record.status),
        operation=record.operation,
        input=_load_mapping(record.input_json),
        provider_config_snapshot=_load_mapping(record.provider_config_snapshot_json),
        normalized_output=_optional_mapping(record.normalized_output_json),
        remote_task_id=record.remote_task_id,
        idempotency_key=record.idempotency_key,
        priority=record.priority,
        progress=record.progress,
        poll_after_at=_optional_parse_time(record.poll_after_at),
        timeout_at=_optional_parse_time(record.timeout_at),
        cancel_requested_at=_optional_parse_time(record.cancel_requested_at),
        row_version=record.row_version,
        created_at=_parse_time(record.created_at),
        updated_at=_parse_time(record.updated_at),
        started_at=_optional_parse_time(record.started_at),
        completed_at=_optional_parse_time(record.completed_at),
    )


def _attempt_record(attempt: TaskAttempt) -> TaskAttemptRecord:
    return TaskAttemptRecord(**_attempt_values(attempt))


def _attempt_values(attempt: TaskAttempt) -> dict[str, object]:
    return {
        "id": attempt.id,
        "task_id": attempt.task_id,
        "attempt_no": attempt.attempt_no,
        "phase": attempt.phase.value,
        "status": attempt.status.value,
        "error_code": attempt.error_code,
        "error_message": attempt.error_message,
        "provider_error_json": _dump_json(attempt.provider_error),
        "retryable": attempt.retryable,
        "retry_after_at": _optional_time(attempt.retry_after_at),
        "started_at": _time(attempt.started_at),
        "ended_at": _optional_time(attempt.ended_at),
    }


def _attempt_entity(record: TaskAttemptRecord) -> TaskAttempt:
    return TaskAttempt(
        id=record.id,
        task_id=record.task_id,
        attempt_no=record.attempt_no,
        phase=AttemptPhase(record.phase),
        status=AttemptStatus(record.status),
        error_code=record.error_code,
        error_message=record.error_message,
        provider_error=_load_mapping(record.provider_error_json),
        retryable=record.retryable,
        retry_after_at=_optional_parse_time(record.retry_after_at),
        started_at=_parse_time(record.started_at),
        ended_at=_optional_parse_time(record.ended_at),
    )


def _request_log_record(request_log: RequestLog) -> RequestLogRecord:
    return RequestLogRecord(
        id=request_log.id,
        attempt_id=request_log.attempt_id,
        provider_id=request_log.provider_id,
        model_id=request_log.model_id,
        trace_id=request_log.trace_id,
        operation=request_log.operation,
        method=request_log.method,
        url_template=request_log.url_template,
        http_status=request_log.http_status,
        latency_ms=request_log.latency_ms,
        request_summary_json=_dump_json(request_log.request_summary),
        response_summary_json=_dump_json(request_log.response_summary),
        usage_json=_dump_json(request_log.usage),
        amount_micros=request_log.amount_micros,
        currency=request_log.currency,
        error_code=request_log.error_code,
        created_at=_time(request_log.created_at),
    )


def _request_log_entity(record: RequestLogRecord) -> RequestLog:
    return RequestLog(
        id=record.id,
        attempt_id=record.attempt_id,
        provider_id=record.provider_id,
        model_id=record.model_id,
        trace_id=record.trace_id,
        operation=record.operation,
        method=record.method,
        url_template=record.url_template,
        http_status=record.http_status,
        latency_ms=record.latency_ms,
        request_summary=_load_mapping(record.request_summary_json),
        response_summary=_load_mapping(record.response_summary_json),
        usage=_load_mapping(record.usage_json),
        amount_micros=record.amount_micros,
        currency=record.currency,
        error_code=record.error_code,
        created_at=_parse_time(record.created_at),
    )


def _artifact_record(artifact: Artifact) -> ArtifactRecord:
    return ArtifactRecord(
        id=artifact.id,
        task_id=artifact.task_id,
        kind=artifact.kind,
        relative_path=artifact.relative_path,
        mime_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        metadata_json=_dump_json(artifact.metadata),
        source_url_redacted=artifact.source_url_redacted,
        created_at=_time(artifact.created_at),
        deleted_at=_optional_time(artifact.deleted_at),
    )


def _artifact_entity(record: ArtifactRecord) -> Artifact:
    return Artifact(
        id=record.id,
        task_id=record.task_id,
        kind=record.kind,
        relative_path=record.relative_path,
        mime_type=record.mime_type,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        metadata=_load_mapping(record.metadata_json),
        source_url_redacted=record.source_url_redacted,
        created_at=_parse_time(record.created_at),
        deleted_at=_optional_parse_time(record.deleted_at),
    )


def _dump_json(value: object) -> str:
    def thaw(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): thaw(child) for key, child in item.items()}
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            return [thaw(child) for child in item]
        return item

    return json.dumps(thaw(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _optional_json(value: Mapping[str, object] | None) -> str | None:
    return None if value is None else _dump_json(value)


def _load_mapping(value: str) -> Mapping[str, object]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ValueError("database JSON value is not an object")
    return cast(dict[str, object], loaded)


def _optional_mapping(value: str | None) -> Mapping[str, object] | None:
    return None if value is None else _load_mapping(value)


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
