"""Read-only application query facade."""

from __future__ import annotations

from datetime import tzinfo

from astraweft.domain.task import Artifact, RequestLog, Task
from astraweft.ports.query import (
    ArtifactQuery,
    CostBreakdown,
    CursorPage,
    DashboardSummary,
    QueryPort,
    RequestLogQuery,
    TaskQuery,
)


class QueryService:
    def __init__(self, adapter: QueryPort) -> None:
        self._adapter = adapter

    async def get_dashboard_summary(self, *, timezone: tzinfo | None = None) -> DashboardSummary:
        return await self._adapter.get_dashboard_summary(timezone=timezone)

    async def get_cost_breakdown(self, *, days: int | None = 30) -> CostBreakdown:
        return await self._adapter.get_cost_breakdown(days=days)

    async def search_tasks(
        self,
        query: TaskQuery | None = None,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> CursorPage[Task]:
        return await self._adapter.search_tasks(query or TaskQuery(), cursor=cursor, limit=limit)

    async def search_request_logs(
        self,
        query: RequestLogQuery | None = None,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> CursorPage[RequestLog]:
        return await self._adapter.search_request_logs(
            query or RequestLogQuery(),
            cursor=cursor,
            limit=limit,
        )

    async def search_artifacts(
        self,
        *,
        query: ArtifactQuery | None = None,
        trashed: bool | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> CursorPage[Artifact]:
        if query is not None and trashed is not None:
            raise ValueError("use ArtifactQuery or trashed, not both")
        effective = query or ArtifactQuery(trashed=bool(trashed))
        return await self._adapter.search_artifacts(
            query=effective,
            cursor=cursor,
            limit=limit,
        )


__all__ = ["QueryService"]
