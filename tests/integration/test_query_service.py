"""Aggregate read models and stable keyset pagination tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from astraweft.application.providers import CreateProvider
from astraweft.application.tasks import CreateTask
from astraweft.bootstrap.container import build_app_context
from astraweft.domain.task import TaskStatus
from astraweft.infrastructure.database.query import _local_day_bounds
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.query import ArtifactQuery, RequestLogQuery, TaskQuery
from astraweft.ports.secrets import SecretValue


def test_dashboard_day_bounds_follow_timezone_and_dst() -> None:
    zone = ZoneInfo("America/New_York")
    start, end = _local_day_bounds(
        zone,
        now=datetime(2025, 3, 9, 12, tzinfo=zone),
    )

    assert datetime.fromisoformat(start).hour == 5
    assert datetime.fromisoformat(end).hour == 4
    assert (
        datetime.fromisoformat(end) - datetime.fromisoformat(start)
    ).total_seconds() == 23 * 3600


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dashboard_aggregates_and_cursor_pages_do_not_load_all_rows(tmp_path: Path) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Query Mock",
                settings={"response_mode": "accepted", "catalog_revision": 2},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        models = await context.provider_service.sync_models(provider.id)
        text_model = next(item for item in models if item.remote_model_id == "mock-text-v1")
        video_model = next(item for item in models if item.remote_model_id == "mock-video-v1")
        for index in range(3):
            await context.task_service.create_and_run(
                CreateTask(
                    provider_id=provider.id,
                    model_id=text_model.id,
                    operation="text.generate",
                    inputs={"prompt": f"query {index}"},
                )
            )
        video_task = await context.task_service.create_and_run(
            CreateTask(
                provider_id=provider.id,
                model_id=video_model.id,
                operation="video.generate",
                inputs={"prompt": "query artifact"},
            )
        )

        summary = await context.query_service.get_dashboard_summary()
        assert summary.call_count == 12
        assert summary.terminal_task_count == 4
        assert summary.successful_task_count == 4
        assert summary.running_task_count == 0
        assert summary.known_costs == (("USD", 4000),)
        assert summary.unknown_cost_count == 8
        assert summary.artifact_count == 1
        assert summary.artifact_size_bytes == len(b"mock-video-artifact")
        assert summary.provider_count == 1
        assert summary.enabled_provider_count == 1

        costs = await context.query_service.get_cost_breakdown(days=30)
        assert costs.period_days == 30
        assert costs.total_calls == 12
        assert costs.known_cost_calls == 4
        assert costs.unknown_cost_calls == 8
        assert sum(row.amount_micros for row in costs.rows) == 4000
        assert sum(row.call_count for row in costs.rows) == 4
        assert {row.provider_name for row in costs.rows} == {"Query Mock"}
        assert {row.currency for row in costs.rows} == {"USD"}

        first = await context.query_service.search_tasks(limit=2)
        second = await context.query_service.search_tasks(cursor=first.next_cursor, limit=2)
        assert len(first.items) == len(second.items) == 2
        assert first.next_cursor is not None
        assert second.next_cursor is None
        assert {item.id for item in first.items}.isdisjoint(item.id for item in second.items)
        filtered_tasks = await context.query_service.search_tasks(
            TaskQuery(
                statuses=frozenset({TaskStatus.SUCCESS}),
                provider_id=provider.id,
                operation="video.generate",
            ),
            limit=10,
        )
        assert [item.id for item in filtered_tasks.items] == [video_task.id]

        log_ids: list[str] = []
        cursor: str | None = None
        while True:
            page = await context.query_service.search_request_logs(cursor=cursor, limit=2)
            log_ids.extend(item.id for item in page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        assert len(log_ids) == len(set(log_ids)) == 12
        known = await context.query_service.search_request_logs(
            RequestLogQuery(
                provider_id=provider.id,
                operation="video.generate",
                known_cost_only=True,
            ),
            limit=10,
        )
        assert len(known.items) == 1
        errors = await context.query_service.search_request_logs(
            RequestLogQuery(errors_only=True), limit=10
        )
        assert errors.items == ()

        active = await context.query_service.search_artifacts(limit=10)
        assert len(active.items) == 1
        video_artifacts = await context.query_service.search_artifacts(
            query=ArtifactQuery(
                kinds=frozenset({"VIDEO"}),
                provider_id=provider.id,
                model_id=video_model.id,
                created_after=datetime.now(UTC) - timedelta(days=1),
            ),
            limit=10,
        )
        assert [item.id for item in video_artifacts.items] == [active.items[0].id]
        assert (
            await context.query_service.search_artifacts(
                query=ArtifactQuery(kinds=frozenset({"IMAGE"})),
                limit=10,
            )
        ).items == ()
        await context.task_service.trash_artifact(active.items[0].id)
        assert (await context.query_service.search_artifacts(limit=10)).items == ()
        trashed = await context.query_service.search_artifacts(trashed=True, limit=10)
        assert len(trashed.items) == 1

        async with context.database.engine.connect() as connection:
            task_indexes = {
                str(row[1])
                for row in (await connection.execute(text("PRAGMA index_list('tasks')"))).all()
            }
            log_indexes = {
                str(row[1])
                for row in (
                    await connection.execute(text("PRAGMA index_list('request_logs')"))
                ).all()
            }
            artifact_indexes = {
                str(row[1])
                for row in (await connection.execute(text("PRAGMA index_list('artifacts')"))).all()
            }
        assert "ix_tasks_created_id" in task_indexes
        assert "ix_request_logs_created_id" in log_indexes
        assert "ix_artifacts_kind_deleted_created_id" in artifact_indexes
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_query_rejects_invalid_cursor_and_unbounded_page_size(tmp_path: Path) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        with pytest.raises(ValueError, match="cursor"):
            await context.query_service.search_tasks(cursor="not-a-cursor")
        with pytest.raises(ValueError, match="between"):
            await context.query_service.search_request_logs(limit=0)
        with pytest.raises(ValueError, match="between"):
            await context.query_service.search_artifacts(limit=501)
        with pytest.raises(ValueError, match="cost period"):
            await context.query_service.get_cost_breakdown(days=14)
        with pytest.raises(ValueError, match="timezone-aware"):
            ArtifactQuery(created_after=datetime.now())
        with pytest.raises(ValueError, match="must not be empty"):
            ArtifactQuery(kinds=frozenset({""}))
        with pytest.raises(ValueError, match="before end"):
            ArtifactQuery(
                created_after=datetime.now(UTC),
                created_before=datetime.now(UTC) - timedelta(days=1),
            )
        with pytest.raises(ValueError, match="not both"):
            await context.query_service.search_artifacts(
                query=ArtifactQuery(),
                trashed=True,
            )
    finally:
        await context.close()
