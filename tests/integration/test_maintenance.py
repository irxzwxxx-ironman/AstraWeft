"""Data-safety integration tests against real SQLite files and archives."""

from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest

from astraweft.application.settings import AppSettings
from astraweft.infrastructure.config.paths import resolve_app_paths
from astraweft.infrastructure.database import latest_revision, run_migrations
from astraweft.infrastructure.maintenance.local import (
    InvalidRestoreError,
    LocalMaintenanceAdapter,
    MaintenanceError,
)


def _adapter(
    root: Path,
    *,
    backup_retention_count: int = 7,
) -> LocalMaintenanceAdapter:
    paths = resolve_app_paths(root)
    paths.ensure()
    run_migrations(paths.database_path)
    return LocalMaintenanceAdapter(
        paths,
        AppSettings(backup_retention_count=backup_retention_count),
        app_version="test",
        expected_revision=latest_revision(paths.database_path),
    )


def _set_probe(database_path: Path, value: str) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS restore_probe (value TEXT NOT NULL)")
        connection.execute("DELETE FROM restore_probe")
        connection.execute("INSERT INTO restore_probe (value) VALUES (?)", (value,))
        connection.commit()


def _probe(database_path: Path) -> str:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute("SELECT value FROM restore_probe").fetchone()
    assert row is not None
    return str(row[0])


def test_health_backup_manifest_and_retention(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, backup_retention_count=2)
    paths = resolve_app_paths(tmp_path)
    _set_probe(paths.database_path, "source")

    health = adapter.check_database()
    first = adapter.create_backup(reason="Manual / UI")
    second = adapter.create_backup(reason="manual")
    third = adapter.create_backup(reason="manual")

    assert health.healthy
    assert health.revision == latest_revision(paths.database_path)
    assert dict(health.table_counts)["restore_probe"] == 1
    assert first.path.name.endswith("manual-ui.db")
    assert first.sha256 != ""
    assert third.health.healthy
    assert len(tuple(paths.backup_dir.glob("*.db"))) == 2
    assert len(tuple(paths.backup_dir.glob("*.json"))) == 2
    assert not first.path.exists()
    assert second.path.exists()
    manifest = json.loads(third.path.with_suffix(".json").read_text(encoding="utf-8"))
    assert manifest["format"] == "astraweft-backup/v1"
    assert manifest["sha256"] == third.sha256
    assert manifest["table_counts"]["restore_probe"] == 1


def test_restore_is_previewed_staged_and_applied_with_safety_backup(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    paths = resolve_app_paths(tmp_path)
    _set_probe(paths.database_path, "before")
    backup = adapter.create_backup()
    _set_probe(paths.database_path, "after")

    preview = adapter.inspect_restore(backup.path)
    pending = adapter.stage_restore(backup.path)

    assert preview.can_restore
    assert not preview.warnings
    assert pending.staged_path.exists()
    assert pending.marker_path.exists()
    assert _probe(paths.database_path) == "after"

    safety_backup = adapter.apply_pending_restore()

    assert safety_backup is not None
    assert "pre-restore" in safety_backup.path.name
    assert _probe(paths.database_path) == "before"
    assert not paths.pending_restore_path.exists()
    assert not paths.restore_marker_path.exists()
    assert adapter.apply_pending_restore() is None


def test_tampered_pending_restore_never_replaces_current_database(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    paths = resolve_app_paths(tmp_path)
    _set_probe(paths.database_path, "before")
    backup = adapter.create_backup()
    _set_probe(paths.database_path, "current")
    adapter.stage_restore(backup.path)
    with paths.pending_restore_path.open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(InvalidRestoreError, match="校验失败"):
        adapter.apply_pending_restore()

    assert _probe(paths.database_path) == "current"
    assert paths.restore_marker_path.exists()


@pytest.mark.parametrize(
    "marker",
    ["not-json", json.dumps({"format": "unknown"}), json.dumps({"format": "astraweft-restore/v1"})],
)
def test_invalid_restore_marker_preserves_database(tmp_path: Path, marker: str) -> None:
    adapter = _adapter(tmp_path)
    paths = resolve_app_paths(tmp_path)
    _set_probe(paths.database_path, "safe")
    paths.restore_marker_path.write_text(marker, encoding="utf-8")

    with pytest.raises(InvalidRestoreError):
        adapter.apply_pending_restore()

    assert _probe(paths.database_path) == "safe"


def test_restore_rejects_corrupt_missing_revision_and_future_revision(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("not sqlite", encoding="utf-8")
    with pytest.raises(InvalidRestoreError, match="SQLite"):
        adapter.inspect_restore(corrupt)

    no_revision = tmp_path / "no-revision.db"
    with sqlite3.connect(no_revision) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    no_revision_preview = adapter.inspect_restore(no_revision)
    assert not no_revision_preview.can_restore
    assert "迁移版本" in no_revision_preview.warnings[0]

    future = tmp_path / "future.db"
    source = adapter.create_backup().path
    future.write_bytes(source.read_bytes())
    with sqlite3.connect(future) as connection:
        connection.execute("UPDATE alembic_version SET version_num = '99999999_9999'")
        connection.commit()
    future_preview = adapter.inspect_restore(future)
    assert not future_preview.can_restore
    assert "更新版本" in future_preview.warnings[0]


def test_older_restore_is_accepted_for_startup_migration(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    older = tmp_path / "older.db"
    source = adapter.create_backup().path
    older.write_bytes(source.read_bytes())
    with sqlite3.connect(older) as connection:
        connection.execute("UPDATE alembic_version SET version_num = '20260715_0004'")
        connection.commit()

    preview = adapter.inspect_restore(older)

    assert preview.can_restore
    assert "自动升级" in preview.warnings[0]


def test_diagnostic_export_is_content_free_and_redacts_again(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    paths = resolve_app_paths(tmp_path)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    paths.log_dir.joinpath("astraweft.jsonl").write_text(
        '{"api_key":"do-not-leak","message":"Bearer live-token","safe":"ok"}\n'
        "plain bearer second-token\n",
        encoding="utf-8",
    )

    exported = adapter.export_diagnostics()

    assert exported.path.exists()
    assert exported.size_bytes > 0
    with zipfile.ZipFile(exported.path) as archive:
        assert set(archive.namelist()) == set(exported.included_files)
        combined = b"\n".join(archive.read(name) for name in archive.namelist()).decode()
        health = json.loads(archive.read("database-health.json"))
    assert "do-not-leak" not in combined
    assert "live-token" not in combined
    assert "second-token" not in combined
    assert "[REDACTED]" in combined
    assert "safe" in combined
    assert health["integrity"] == "ok"
    assert "tasks" in health["table_counts"]


def test_missing_and_invalid_database_errors_are_safe(tmp_path: Path) -> None:
    paths = resolve_app_paths(tmp_path)
    paths.ensure()
    adapter = LocalMaintenanceAdapter(
        paths,
        AppSettings(),
        app_version="test",
    )
    with pytest.raises(MaintenanceError, match="does not exist"):
        adapter.check_database()
    with pytest.raises(MaintenanceError, match="尚未创建"):
        adapter.create_backup()

    paths.database_path.write_text("SQLite format 3\0broken", encoding="utf-8")
    with pytest.raises(MaintenanceError, match="validation failed"):
        adapter.check_database()


def test_data_directory_migration_is_verified_before_atomic_publish(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    adapter = _adapter(source_root)
    paths = resolve_app_paths(source_root)
    paths.settings_path.write_text('{"language":"zh_CN"}\n', encoding="utf-8")
    artifact = paths.artifact_dir / "images" / "sample.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("immutable artifact", encoding="utf-8")
    paths.log_dir.joinpath("astraweft.jsonl").write_text("{}\n", encoding="utf-8")
    _set_probe(paths.database_path, "source-stays")
    target = tmp_path / "migrated"

    preview = adapter.inspect_data_migration(target)
    result = adapter.stage_data_migration(target)

    assert preview.can_stage
    assert preview.file_count >= 4
    assert result.target_root == target.resolve()
    assert result.manifest_path.exists()
    assert (target / "config" / "settings.json").read_text(encoding="utf-8").startswith("{")
    assert (target / "data" / "artifacts" / "images" / "sample.txt").read_text() == (
        "immutable artifact"
    )
    assert _probe(target / "data" / "astraweft.db") == "source-stays"
    assert _probe(paths.database_path) == "source-stays"
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["format"] == "astraweft-data-migration/v1"
    assert len(manifest["files"]) == result.file_count
    assert manifest["total_bytes"] == result.total_bytes


def test_data_migration_conflicts_and_interruption_never_publish_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    adapter = _adapter(source_root)
    paths = resolve_app_paths(source_root)
    paths.settings_path.write_text("{}\n", encoding="utf-8")

    nested = adapter.inspect_data_migration(paths.data_dir / "nested")
    assert not nested.can_stage
    assert "当前数据目录内" in nested.conflicts[0]

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    occupied.joinpath("keep.txt").write_text("user data", encoding="utf-8")
    occupied_preview = adapter.inspect_data_migration(occupied)
    assert not occupied_preview.can_stage
    with pytest.raises(MaintenanceError, match="必须不存在或为空"):
        adapter.stage_data_migration(occupied)
    assert occupied.joinpath("keep.txt").read_text() == "user data"

    target = tmp_path / "interrupted"

    def fail_copy(_source: Path, _destination: Path) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(shutil, "copy2", fail_copy)
    with pytest.raises(OSError, match="interruption"):
        adapter.stage_data_migration(target)

    assert not target.exists()
    partials = tuple(tmp_path.glob(".interrupted.astraweft-partial-*"))
    assert len(partials) == 1
    assert partials[0].joinpath("migration.failed.json").exists()
    assert paths.database_path.exists()
