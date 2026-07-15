"""Application composition root integration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from astraweft.bootstrap.container import build_app_context
from astraweft.infrastructure.secrets.store import SessionSecretStore


@pytest.mark.integration
@pytest.mark.asyncio
async def test_context_initializes_local_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("astraweft.bootstrap.container.create_secret_store", SessionSecretStore)

    context = await build_app_context(tmp_path)
    status = context.presentation_status()
    assert status.database_online is True
    assert status.credential_store_persistent is False
    assert Path(status.data_directory) == tmp_path / "data"
    assert Path(status.log_path).exists()
    assert context.paths.database_path.exists()
    assert context.paths.artifact_dir.is_dir()
    assert context.clock.now().tzinfo is not None
    assert context.ids.new()
    assert context.uow_factory()

    await context.close()
    await context.close()

    log_records = [json.loads(line) for line in Path(status.log_path).read_text().splitlines()]
    assert [record["message"] for record in log_records] == [
        "bootstrap_started",
        "bootstrap_ready",
        "shutdown_started",
        "shutdown_complete",
    ]
    assert log_records[1]["context"]["keyring_backend_persistent"] is False
    assert all(record["trace_id"] for record in log_records)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_context_closes_database_after_failed_ping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = False

    async def fail_ping(_self: object) -> bool:
        raise RuntimeError("database unavailable")

    async def record_close(_self: object) -> None:
        nonlocal closed
        closed = True

    monkeypatch.setattr("astraweft.bootstrap.container.Database.ping", fail_ping)
    monkeypatch.setattr("astraweft.bootstrap.container.Database.close", record_close)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await build_app_context(tmp_path)

    assert closed is True
