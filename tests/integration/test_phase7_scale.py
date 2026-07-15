"""Opt-in Phase 7 scale gate: 100k Tasks and 1m Request Logs."""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from astraweft.application.query import QueryService
from astraweft.infrastructure.database import Database, SQLiteQueryAdapter, run_migrations

pytestmark = [
    pytest.mark.integration,
    pytest.mark.performance,
    pytest.mark.skipif(
        os.environ.get("ASTRAWEFT_RUN_PHASE7_SCALE") != "1",
        reason="set ASTRAWEFT_RUN_PHASE7_SCALE=1 for the 100k/1m scale gate",
    ),
]


@pytest.mark.asyncio
async def test_100k_tasks_and_1m_logs_meet_keyset_query_budget(tmp_path: Path) -> None:
    database_path = tmp_path / "scale.db"
    run_migrations(database_path)
    timestamp = datetime.now(UTC).replace(microsecond=0).isoformat()
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA journal_mode=MEMORY")
        connection.execute(
            """
            INSERT INTO providers (
                id, plugin_id, plugin_version, name, endpoint, enabled, config_json,
                credential_id, health_status, last_checked_at, row_version,
                created_at, updated_at, deleted_at
            ) VALUES (
                'provider-scale', 'dev.scale', '1.0', 'Scale', NULL, 1, '{}',
                NULL, 'HEALTHY', NULL, 1, :timestamp, :timestamp, NULL
            )
            """,
            {"timestamp": timestamp},
        )
        connection.execute(
            """
            WITH RECURSIVE seq(x) AS (
                VALUES(1) UNION ALL SELECT x + 1 FROM seq WHERE x < 100000
            )
            INSERT INTO tasks (
                id, provider_id, model_id, status, operation, input_json,
                provider_config_snapshot_json, normalized_output_json, remote_task_id,
                idempotency_key, priority, progress, poll_after_at, timeout_at,
                cancel_requested_at, row_version, created_at, updated_at, started_at,
                completed_at
            )
            SELECT
                printf('task-%07d', x), 'provider-scale', NULL,
                CASE WHEN x % 10 = 0 THEN 'RUNNING' ELSE 'SUCCESS' END,
                'text.generate', '{}', '{}', NULL, NULL, printf('intent-%07d', x),
                100, CASE WHEN x % 10 = 0 THEN 50 ELSE 100 END,
                NULL, NULL, NULL, 1, :timestamp, :timestamp, :timestamp,
                CASE WHEN x % 10 = 0 THEN NULL ELSE :timestamp END
            FROM seq
            """,
            {"timestamp": timestamp},
        )
        connection.execute(
            """
            WITH RECURSIVE seq(x) AS (
                VALUES(1) UNION ALL SELECT x + 1 FROM seq WHERE x < 1000000
            )
            INSERT INTO request_logs (
                id, attempt_id, provider_id, model_id, trace_id, operation, method,
                url_template, http_status, latency_ms, request_summary_json,
                response_summary_json, usage_json, amount_micros, currency,
                error_code, created_at
            )
            SELECT
                printf('log-%07d', x), NULL, 'provider-scale', NULL,
                printf('trace-%07d', x), 'text.generate', 'POST', '/v1/mock',
                200, x % 5000, '{}', '{}', '{}',
                CASE WHEN x % 2 = 0 THEN 1000 ELSE NULL END,
                CASE WHEN x % 2 = 0 THEN 'USD' ELSE NULL END,
                NULL, :timestamp
            FROM seq
            """,
            {"timestamp": timestamp},
        )
        connection.commit()

    database = Database(database_path)
    queries = QueryService(SQLiteQueryAdapter(database.sessions))
    try:
        started = time.perf_counter()
        task_page = await queries.search_tasks(limit=100)
        task_elapsed = time.perf_counter() - started

        started = time.perf_counter()
        log_page = await queries.search_request_logs(limit=100)
        log_elapsed = time.perf_counter() - started

        started = time.perf_counter()
        summary = await queries.get_dashboard_summary()
        dashboard_elapsed = time.perf_counter() - started

        assert len(task_page.items) == 100 and task_page.next_cursor is not None
        assert len(log_page.items) == 100 and log_page.next_cursor is not None
        assert summary.call_count == 1_000_000
        assert summary.terminal_task_count == 90_000
        assert summary.running_task_count == 10_000
        assert task_elapsed <= 0.5
        assert log_elapsed <= 0.5
        assert dashboard_elapsed <= 0.75
    finally:
        await database.close()
