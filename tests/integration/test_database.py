"""SQLite migration and async runtime integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from astraweft.infrastructure.database.engine import Database
from astraweft.infrastructure.database.migration import run_migrations


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_migrates_and_applies_safety_pragmas(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "astraweft.db"
    run_migrations(database_path)

    sync_engine = create_engine(f"sqlite:///{database_path}")
    inspector = inspect(sync_engine)
    assert set(inspector.get_table_names()) == {
        "alembic_version",
        "app_settings",
        "artifacts",
        "comfyui_executions",
        "comfyui_instances",
        "comfyui_templates",
        "models",
        "provider_credentials",
        "providers",
        "request_logs",
        "task_attempts",
        "tasks",
        "artifact_links",
        "node_runs",
        "workflow_current_versions",
        "workflow_edges",
        "workflow_nodes",
        "workflow_runs",
        "workflow_versions",
        "workflows",
    }
    columns = {column["name"] for column in inspector.get_columns("app_settings")}
    assert columns == {"key", "value_json", "updated_at"}
    task_columns = {column["name"] for column in inspector.get_columns("tasks")}
    assert {"idempotency_key", "remote_task_id", "row_version"} <= task_columns
    node_run_columns = {column["name"] for column in inspector.get_columns("node_runs")}
    assert {
        "planned_task_id",
        "task_id",
        "planned_comfyui_execution_id",
        "comfyui_execution_id",
        "row_version",
    } <= node_run_columns
    workflow_indexes = {index["name"] for index in inspector.get_indexes("workflow_versions")}
    assert "uq_workflow_single_draft" in workflow_indexes
    sync_engine.dispose()

    database = Database(database_path)
    try:
        assert await database.ping() is True
        async with database.engine.connect() as connection:
            foreign_keys = (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one()
            journal_mode = (await connection.execute(text("PRAGMA journal_mode"))).scalar_one()
            busy_timeout = (await connection.execute(text("PRAGMA busy_timeout"))).scalar_one()
        assert foreign_keys == 1
        assert journal_mode == "wal"
        assert busy_timeout == 5000
    finally:
        await database.close()


@pytest.mark.integration
def test_migrations_are_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "astraweft.db"

    run_migrations(database_path)
    run_migrations(database_path)

    engine = create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()
