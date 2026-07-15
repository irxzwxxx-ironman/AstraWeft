"""Phase 3 local scale gate for queue and history presentation."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from sqlalchemy import text

from astraweft.application.providers import CreateProvider
from astraweft.bootstrap.container import build_app_context
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.secrets import SecretValue
from astraweft.presentation.pages.logs import RequestLogsPage
from astraweft.presentation.pages.tasks import TaskCenterPage


@pytest.mark.gui
@pytest.mark.asyncio
async def test_thousand_waiting_tasks_and_hundred_thousand_logs_stay_responsive(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    context = await build_app_context(
        tmp_path,
        secret_store_override=SessionSecretStore(),
    )
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Scale Gate Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = next(
            item
            for item in await context.provider_service.sync_models(provider.id)
            if item.remote_model_id == "mock-text-v1"
        )
        timestamp = "2026-07-15T00:00:00+00:00"
        async with context.database.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    WITH RECURSIVE seq(x) AS (
                        VALUES(1) UNION ALL SELECT x + 1 FROM seq WHERE x < 1000
                    )
                    INSERT INTO tasks (
                        id, provider_id, model_id, status, operation, input_json,
                        provider_config_snapshot_json, normalized_output_json,
                        remote_task_id, idempotency_key, priority, progress,
                        poll_after_at, timeout_at, cancel_requested_at, row_version,
                        created_at, updated_at, started_at, completed_at
                    )
                    SELECT
                        printf('scale-task-%06d', x), :provider_id, :model_id,
                        'QUEUED', 'text.generate', '{}', '{}', NULL, NULL,
                        printf('scale-key-%06d', x), x % 100, 0,
                        NULL, NULL, NULL, 1, :timestamp, :timestamp, NULL, NULL
                    FROM seq
                    """
                ),
                {
                    "provider_id": provider.id,
                    "model_id": model.id,
                    "timestamp": timestamp,
                },
            )
            await connection.execute(
                text(
                    """
                    WITH RECURSIVE seq(x) AS (
                        VALUES(1) UNION ALL SELECT x + 1 FROM seq WHERE x < 100000
                    )
                    INSERT INTO request_logs (
                        id, attempt_id, provider_id, model_id, trace_id, operation,
                        method, url_template, http_status, latency_ms,
                        request_summary_json, response_summary_json, usage_json,
                        amount_micros, currency, error_code, created_at
                    )
                    SELECT
                        printf('scale-log-%06d', x), NULL, :provider_id, :model_id,
                        printf('trace-%06d', x), 'text.generate', NULL, NULL, NULL,
                        x % 500, '{}', '{}', '{}', NULL, NULL, NULL, :timestamp
                    FROM seq
                    """
                ),
                {
                    "provider_id": provider.id,
                    "model_id": model.id,
                    "timestamp": timestamp,
                },
            )

        task_page = TaskCenterPage(context.task_service)
        logs_page = RequestLogsPage(context.task_service)
        qtbot.addWidget(task_page)
        qtbot.addWidget(logs_page)

        task_started = time.perf_counter()
        await task_page._refresh()
        task_elapsed = time.perf_counter() - task_started
        log_started = time.perf_counter()
        await logs_page._refresh()
        log_elapsed = time.perf_counter() - log_started

        assert task_page._table.model() is not None
        assert task_page._table.model().rowCount() == 1000
        assert logs_page._table.model() is not None
        assert logs_page._table.model().rowCount() == 1000
        assert task_elapsed < 3.0
        assert log_elapsed < 3.0
        async with context.database.engine.connect() as connection:
            count = (
                await connection.execute(text("SELECT COUNT(*) FROM request_logs"))
            ).scalar_one()
        assert count == 100_000
    finally:
        await context.close()
