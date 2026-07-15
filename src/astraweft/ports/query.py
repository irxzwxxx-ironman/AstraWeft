"""Read-only aggregate and cursor-pagination contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Protocol

from astraweft.domain.task import Artifact, RequestLog, Task, TaskStatus


@dataclass(frozen=True, slots=True)
class CursorPage[ItemT]:
    items: tuple[ItemT, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class TaskQuery:
    statuses: frozenset[TaskStatus] = frozenset()
    provider_id: str | None = None
    operation: str | None = None


@dataclass(frozen=True, slots=True)
class RequestLogQuery:
    provider_id: str | None = None
    operation: str | None = None
    errors_only: bool = False
    known_cost_only: bool = False


@dataclass(frozen=True, slots=True)
class ArtifactQuery:
    trashed: bool = False
    kinds: frozenset[str] = frozenset()
    provider_id: str | None = None
    model_id: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None

    def __post_init__(self) -> None:
        if any(not kind.strip() for kind in self.kinds):
            raise ValueError("artifact kinds must not be empty")
        for value in (self.created_after, self.created_before):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("artifact query timestamps must be timezone-aware")
        if (
            self.created_after is not None
            and self.created_before is not None
            and self.created_after >= self.created_before
        ):
            raise ValueError("artifact query start must be before end")


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    call_count: int
    terminal_task_count: int
    successful_task_count: int
    running_task_count: int
    known_costs: tuple[tuple[str, int], ...]
    unknown_cost_count: int
    artifact_count: int
    artifact_size_bytes: int
    provider_count: int
    enabled_provider_count: int
    healthy_provider_count: int


@dataclass(frozen=True, slots=True)
class CostBreakdownRow:
    provider_id: str
    provider_name: str
    model_id: str | None
    model_name: str | None
    currency: str
    amount_micros: int
    call_count: int


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    period_days: int | None
    total_calls: int
    known_cost_calls: int
    unknown_cost_calls: int
    rows: tuple[CostBreakdownRow, ...]


class QueryPort(Protocol):
    async def get_dashboard_summary(
        self, *, timezone: tzinfo | None = None
    ) -> DashboardSummary: ...

    async def get_cost_breakdown(self, *, days: int | None = 30) -> CostBreakdown: ...

    async def search_tasks(
        self,
        query: TaskQuery,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> CursorPage[Task]: ...

    async def search_request_logs(
        self,
        query: RequestLogQuery,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> CursorPage[RequestLog]: ...

    async def search_artifacts(
        self,
        *,
        query: ArtifactQuery,
        cursor: str | None = None,
        limit: int = 100,
    ) -> CursorPage[Artifact]: ...
