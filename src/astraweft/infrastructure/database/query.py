"""Read-only SQL aggregates and keyset pagination."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Protocol

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from astraweft.domain.task import Artifact, RequestLog, Task, TaskStatus
from astraweft.infrastructure.database.models import (
    ArtifactRecord,
    ModelRecord,
    ProviderRecord,
    RequestLogRecord,
    TaskRecord,
)
from astraweft.infrastructure.database.task_repositories import (
    _artifact_entity,
    _request_log_entity,
    _task_entity,
)
from astraweft.ports.query import (
    ArtifactQuery,
    CostBreakdown,
    CostBreakdownRow,
    CursorPage,
    DashboardSummary,
    RequestLogQuery,
    TaskQuery,
)

_TERMINAL = tuple(status.value for status in TaskStatus if status.terminal)


class _CursorItem(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def created_at(self) -> datetime: ...


class SQLiteQueryAdapter:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_dashboard_summary(self, *, timezone: tzinfo | None = None) -> DashboardSummary:
        utc_start, utc_end = _local_day_bounds(timezone)
        async with self._sessions() as session:
            task_row = (
                await session.execute(
                    select(
                        func.count().filter(TaskRecord.status.in_(_TERMINAL)),
                        func.count().filter(TaskRecord.status == TaskStatus.SUCCESS.value),
                        func.count().filter(TaskRecord.status.not_in(_TERMINAL)),
                    ).where(
                        TaskRecord.created_at >= utc_start,
                        TaskRecord.created_at < utc_end,
                    )
                )
            ).one()
            call_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(RequestLogRecord)
                    .where(
                        RequestLogRecord.created_at >= utc_start,
                        RequestLogRecord.created_at < utc_end,
                    )
                )
                or 0
            )
            cost_rows = (
                await session.execute(
                    select(RequestLogRecord.currency, func.sum(RequestLogRecord.amount_micros))
                    .where(
                        RequestLogRecord.created_at >= utc_start,
                        RequestLogRecord.created_at < utc_end,
                        RequestLogRecord.amount_micros.is_not(None),
                        RequestLogRecord.currency.is_not(None),
                    )
                    .group_by(RequestLogRecord.currency)
                    .order_by(RequestLogRecord.currency)
                )
            ).all()
            unknown_cost = int(
                await session.scalar(
                    select(func.count())
                    .select_from(RequestLogRecord)
                    .where(
                        RequestLogRecord.created_at >= utc_start,
                        RequestLogRecord.created_at < utc_end,
                        RequestLogRecord.amount_micros.is_(None),
                    )
                )
                or 0
            )
            artifact_row = (
                await session.execute(
                    select(func.count(), func.coalesce(func.sum(ArtifactRecord.size_bytes), 0))
                    .select_from(ArtifactRecord)
                    .where(ArtifactRecord.deleted_at.is_(None))
                )
            ).one()
            provider_row = (
                await session.execute(
                    select(
                        func.count(),
                        func.count().filter(ProviderRecord.enabled.is_(True)),
                        func.count().filter(ProviderRecord.health_status == "HEALTHY"),
                    ).where(ProviderRecord.deleted_at.is_(None))
                )
            ).one()
        return DashboardSummary(
            call_count=call_count,
            terminal_task_count=int(task_row[0]),
            successful_task_count=int(task_row[1]),
            running_task_count=int(task_row[2]),
            known_costs=tuple((str(currency), int(amount)) for currency, amount in cost_rows),
            unknown_cost_count=unknown_cost,
            artifact_count=int(artifact_row[0]),
            artifact_size_bytes=int(artifact_row[1]),
            provider_count=int(provider_row[0]),
            enabled_provider_count=int(provider_row[1]),
            healthy_provider_count=int(provider_row[2]),
        )

    async def get_cost_breakdown(self, *, days: int | None = 30) -> CostBreakdown:
        if days is not None and days not in {7, 30, 90}:
            raise ValueError("cost period must be 7, 30, 90 days, or all time")
        since = None
        if days is not None:
            since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        filters = () if since is None else (RequestLogRecord.created_at >= since,)
        async with self._sessions() as session:
            counts = (
                await session.execute(
                    select(
                        func.count(),
                        func.count().filter(RequestLogRecord.amount_micros.is_not(None)),
                        func.count().filter(RequestLogRecord.amount_micros.is_(None)),
                    ).where(*filters)
                )
            ).one()
            rows = (
                await session.execute(
                    select(
                        RequestLogRecord.provider_id,
                        ProviderRecord.name,
                        RequestLogRecord.model_id,
                        ModelRecord.display_name,
                        RequestLogRecord.currency,
                        func.sum(RequestLogRecord.amount_micros),
                        func.count(),
                    )
                    .join(ProviderRecord, ProviderRecord.id == RequestLogRecord.provider_id)
                    .outerjoin(ModelRecord, ModelRecord.id == RequestLogRecord.model_id)
                    .where(
                        *filters,
                        RequestLogRecord.amount_micros.is_not(None),
                        RequestLogRecord.currency.is_not(None),
                    )
                    .group_by(
                        RequestLogRecord.provider_id,
                        ProviderRecord.name,
                        RequestLogRecord.model_id,
                        ModelRecord.display_name,
                        RequestLogRecord.currency,
                    )
                    .order_by(
                        func.sum(RequestLogRecord.amount_micros).desc(),
                        ProviderRecord.name,
                        ModelRecord.display_name,
                    )
                )
            ).all()
        return CostBreakdown(
            period_days=days,
            total_calls=int(counts[0]),
            known_cost_calls=int(counts[1]),
            unknown_cost_calls=int(counts[2]),
            rows=tuple(
                CostBreakdownRow(
                    provider_id=str(provider_id),
                    provider_name=str(provider_name),
                    model_id=None if model_id is None else str(model_id),
                    model_name=None if model_name is None else str(model_name),
                    currency=str(currency),
                    amount_micros=int(amount),
                    call_count=int(call_count),
                )
                for (
                    provider_id,
                    provider_name,
                    model_id,
                    model_name,
                    currency,
                    amount,
                    call_count,
                ) in rows
            ),
        )

    async def search_tasks(
        self,
        query: TaskQuery,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> CursorPage[Task]:
        _validate_limit(limit)
        statement = select(TaskRecord)
        if query.statuses:
            statement = statement.where(
                TaskRecord.status.in_(tuple(status.value for status in query.statuses))
            )
        if query.provider_id is not None:
            statement = statement.where(TaskRecord.provider_id == query.provider_id)
        if query.operation is not None:
            statement = statement.where(TaskRecord.operation == query.operation)
        if cursor is not None:
            created_at, item_id = _decode_cursor(cursor)
            statement = statement.where(
                or_(
                    TaskRecord.created_at < created_at,
                    and_(TaskRecord.created_at == created_at, TaskRecord.id < item_id),
                )
            )
        async with self._sessions() as session:
            records = (
                await session.scalars(
                    statement.order_by(TaskRecord.created_at.desc(), TaskRecord.id.desc()).limit(
                        limit + 1
                    )
                )
            ).all()
        return _page(tuple(_task_entity(record) for record in records), limit)

    async def search_request_logs(
        self,
        query: RequestLogQuery,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> CursorPage[RequestLog]:
        _validate_limit(limit)
        statement = select(RequestLogRecord)
        if query.provider_id is not None:
            statement = statement.where(RequestLogRecord.provider_id == query.provider_id)
        if query.operation is not None:
            statement = statement.where(RequestLogRecord.operation == query.operation)
        if query.errors_only:
            statement = statement.where(RequestLogRecord.error_code.is_not(None))
        if query.known_cost_only:
            statement = statement.where(RequestLogRecord.amount_micros.is_not(None))
        if cursor is not None:
            created_at, item_id = _decode_cursor(cursor)
            statement = statement.where(
                or_(
                    RequestLogRecord.created_at < created_at,
                    and_(
                        RequestLogRecord.created_at == created_at,
                        RequestLogRecord.id < item_id,
                    ),
                )
            )
        async with self._sessions() as session:
            records = (
                await session.scalars(
                    statement.order_by(
                        RequestLogRecord.created_at.desc(), RequestLogRecord.id.desc()
                    ).limit(limit + 1)
                )
            ).all()
        return _page(tuple(_request_log_entity(record) for record in records), limit)

    async def search_artifacts(
        self,
        *,
        query: ArtifactQuery,
        cursor: str | None = None,
        limit: int = 100,
    ) -> CursorPage[Artifact]:
        _validate_limit(limit)
        deleted = (
            ArtifactRecord.deleted_at.is_not(None)
            if query.trashed
            else ArtifactRecord.deleted_at.is_(None)
        )
        statement = select(ArtifactRecord).where(deleted)
        if query.kinds:
            statement = statement.where(ArtifactRecord.kind.in_(tuple(query.kinds)))
        if query.provider_id is not None or query.model_id is not None:
            statement = statement.join(TaskRecord, TaskRecord.id == ArtifactRecord.task_id)
        if query.provider_id is not None:
            statement = statement.where(TaskRecord.provider_id == query.provider_id)
        if query.model_id is not None:
            statement = statement.where(TaskRecord.model_id == query.model_id)
        if query.created_after is not None:
            statement = statement.where(
                ArtifactRecord.created_at >= query.created_after.isoformat()
            )
        if query.created_before is not None:
            statement = statement.where(
                ArtifactRecord.created_at < query.created_before.isoformat()
            )
        if cursor is not None:
            created_at, item_id = _decode_cursor(cursor)
            statement = statement.where(
                or_(
                    ArtifactRecord.created_at < created_at,
                    and_(ArtifactRecord.created_at == created_at, ArtifactRecord.id < item_id),
                )
            )
        async with self._sessions() as session:
            records = (
                await session.scalars(
                    statement.order_by(
                        ArtifactRecord.created_at.desc(), ArtifactRecord.id.desc()
                    ).limit(limit + 1)
                )
            ).all()
        return _page(tuple(_artifact_entity(record) for record in records), limit)


def _page[ItemT: _CursorItem](items: tuple[ItemT, ...], limit: int) -> CursorPage[ItemT]:
    visible = items[:limit]
    next_cursor = None
    if len(items) > limit and visible:
        last = visible[-1]
        next_cursor = _encode_cursor(last.created_at.isoformat(), last.id)
    return CursorPage(visible, next_cursor)


def _local_day_bounds(
    timezone: tzinfo | None,
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    if timezone is None:
        local_now = datetime.now().astimezone() if now is None else now.astimezone()
        local_start = datetime.combine(local_now.date(), time.min).astimezone()
        local_end = datetime.combine(local_now.date() + timedelta(days=1), time.min).astimezone()
    else:
        local_now = datetime.now(timezone) if now is None else now.astimezone(timezone)
        local_start = datetime.combine(local_now.date(), time.min, tzinfo=timezone)
        local_end = datetime.combine(
            local_now.date() + timedelta(days=1),
            time.min,
            tzinfo=timezone,
        )
    return local_start.astimezone(UTC).isoformat(), local_end.astimezone(UTC).isoformat()


def _encode_cursor(created_at: str, item_id: str) -> str:
    raw = json.dumps([created_at, item_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(cursor + padding))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid query cursor") from exc
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise ValueError("invalid query cursor")
    return value[0], value[1]


def _validate_limit(limit: int) -> None:
    if not 1 <= limit <= 500:
        raise ValueError("query page limit must be between 1 and 500")
