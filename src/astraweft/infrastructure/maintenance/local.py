"""Crash-safe local data maintenance implemented with the SQLite backup API."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from astraweft.application.settings import AppSettings
from astraweft.infrastructure.config.paths import AppPaths
from astraweft.infrastructure.observability.logging import redact
from astraweft.ports.maintenance import (
    BackupResult,
    DatabaseHealth,
    DataMigrationPreview,
    DataMigrationResult,
    DiagnosticExport,
    PendingRestore,
    RestorePreview,
)


class MaintenanceError(RuntimeError):
    """A local maintenance operation could not finish safely."""


class InvalidRestoreError(MaintenanceError):
    """A candidate restore database failed validation."""


class _ManifestEntry(TypedDict):
    path: str
    size_bytes: int
    sha256: str


class LocalMaintenanceAdapter:
    """Own backup, staged restore, and privacy-safe diagnostic export mechanics."""

    def __init__(
        self,
        paths: AppPaths,
        settings: AppSettings,
        *,
        app_version: str,
        expected_revision: str | None = None,
    ) -> None:
        self._paths = paths
        self._settings = settings
        self._app_version = app_version
        self._expected_revision = expected_revision

    def check_database(self, path: Path | None = None) -> DatabaseHealth:
        database_path = (path or self._paths.database_path).expanduser().resolve()
        if not database_path.is_file():
            raise MaintenanceError(f"database does not exist: {database_path}")
        try:
            with closing(_connect_readonly(database_path)) as connection:
                integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
                integrity = str(integrity_row[0]) if integrity_row else "no result"
                foreign_key_issues = len(connection.execute("PRAGMA foreign_key_check").fetchall())
                table_names = tuple(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    ).fetchall()
                )
                counts = tuple((name, _table_count(connection, name)) for name in table_names)
                revision = _revision(connection, table_names)
        except sqlite3.DatabaseError as exc:
            raise MaintenanceError(f"database validation failed: {database_path.name}") from exc
        return DatabaseHealth(
            database_path=database_path,
            size_bytes=database_path.stat().st_size,
            revision=revision,
            integrity=integrity,
            foreign_key_issues=foreign_key_issues,
            table_counts=counts,
        )

    def create_backup(self, *, reason: str = "manual") -> BackupResult:
        safe_reason = _safe_label(reason)
        created_at = datetime.now(UTC)
        stamp = created_at.strftime("%Y%m%dT%H%M%S.%fZ")
        destination = self._paths.backup_dir / f"astraweft-{stamp}-{safe_reason}.db"
        result = self._backup_to(destination, created_at=created_at)
        _write_json_atomic(
            destination.with_suffix(".json"),
            {
                "format": "astraweft-backup/v1",
                "created_at": result.created_at.isoformat(),
                "database_file": result.path.name,
                "size_bytes": result.size_bytes,
                "sha256": result.sha256,
                "revision": result.health.revision,
                "integrity": result.health.integrity,
                "foreign_key_issues": result.health.foreign_key_issues,
                "table_counts": dict(result.health.table_counts),
                "reason": safe_reason,
            },
        )
        self._prune_backups()
        return result

    def inspect_restore(self, source_path: Path) -> RestorePreview:
        source = source_path.expanduser().resolve()
        if not source.is_file() or not _has_sqlite_header(source):
            raise InvalidRestoreError("选择的文件不是可识别的 SQLite 数据库")
        health = self.check_database(source)
        warnings: list[str] = []
        compatible = True
        if health.revision is None:
            warnings.append("备份没有迁移版本标记，无法确认兼容性")
            compatible = False
        elif self._expected_revision is not None:
            if health.revision > self._expected_revision:
                warnings.append("备份由更新版本的 AstraWeft 创建")
                compatible = False
            elif health.revision != self._expected_revision:
                warnings.append("备份将在下次启动时自动升级")
        if not health.healthy:
            warnings.append("数据库完整性检查未通过")
        return RestorePreview(
            source_path=source,
            size_bytes=source.stat().st_size,
            sha256=_sha256(source),
            health=health,
            compatible=compatible,
            warnings=tuple(warnings),
        )

    def stage_restore(self, source_path: Path) -> PendingRestore:
        preview = self.inspect_restore(source_path)
        if not preview.can_restore:
            raise InvalidRestoreError("备份未通过完整性或版本兼容性检查")
        self._paths.pending_restore_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._paths.pending_restore_path.with_suffix(".db.tmp")
        try:
            _online_copy(preview.source_path, temporary)
            staged_health = self.check_database(temporary)
            if not staged_health.healthy:
                raise InvalidRestoreError("暂存备份未通过二次完整性检查")
            temporary.replace(self._paths.pending_restore_path)
        finally:
            temporary.unlink(missing_ok=True)
        sha256 = _sha256(self._paths.pending_restore_path)
        _write_json_atomic(
            self._paths.restore_marker_path,
            {
                "format": "astraweft-restore/v1",
                "source_name": preview.source_path.name,
                "staged_file": self._paths.pending_restore_path.name,
                "sha256": sha256,
                "revision": preview.health.revision,
                "staged_at": datetime.now(UTC).isoformat(),
            },
        )
        return PendingRestore(
            source_path=preview.source_path,
            staged_path=self._paths.pending_restore_path,
            marker_path=self._paths.restore_marker_path,
            sha256=sha256,
        )

    def apply_pending_restore(self) -> BackupResult | None:
        """Atomically apply an already validated restore before any engine opens."""
        marker = self._paths.restore_marker_path
        if not marker.exists():
            return None
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidRestoreError("待恢复标记已损坏；原数据库未更改") from exc
        if not isinstance(payload, dict) or payload.get("format") != "astraweft-restore/v1":
            raise InvalidRestoreError("待恢复标记格式不受支持；原数据库未更改")
        staged_file = payload.get("staged_file")
        expected_hash = payload.get("sha256")
        if staged_file != self._paths.pending_restore_path.name or not isinstance(
            expected_hash, str
        ):
            raise InvalidRestoreError("待恢复文件与标记不匹配；原数据库未更改")
        preview = self.inspect_restore(self._paths.pending_restore_path)
        if not preview.can_restore or preview.sha256 != expected_hash:
            raise InvalidRestoreError("待恢复文件校验失败；原数据库未更改")
        safety_backup: BackupResult | None = None
        if self._paths.database_path.exists():
            safety_backup = self.create_backup(reason="pre-restore")
        for sidecar in (
            self._paths.database_path.with_name(self._paths.database_path.name + "-wal"),
            self._paths.database_path.with_name(self._paths.database_path.name + "-shm"),
        ):
            sidecar.unlink(missing_ok=True)
        self._paths.pending_restore_path.replace(self._paths.database_path)
        marker.unlink()
        return safety_backup

    def export_diagnostics(self) -> DiagnosticExport:
        created_at = datetime.now(UTC)
        stamp = created_at.strftime("%Y%m%dT%H%M%S.%fZ")
        destination = self._paths.diagnostic_dir / f"astraweft-diagnostics-{stamp}.zip"
        destination.parent.mkdir(parents=True, exist_ok=True)
        health = self.check_database()
        content: dict[str, str] = {
            "manifest.json": _json_text(
                {
                    "format": "astraweft-diagnostics/v1",
                    "created_at": created_at.isoformat(),
                    "privacy": "redacted; database content and credentials are excluded",
                    "app_version": self._app_version,
                }
            ),
            "runtime.json": _json_text(
                {
                    "platform": platform.system(),
                    "platform_release": platform.release(),
                    "machine": platform.machine(),
                    "python": platform.python_version(),
                }
            ),
            "settings.redacted.json": _json_text(redact(self._settings.model_dump(mode="json"))),
            "database-health.json": _json_text(
                {
                    "size_bytes": health.size_bytes,
                    "revision": health.revision,
                    "integrity": health.integrity,
                    "foreign_key_issues": health.foreign_key_issues,
                    "table_counts": dict(health.table_counts),
                }
            ),
        }
        for index, log_path in enumerate(sorted(self._paths.log_dir.glob("astraweft.jsonl*"))):
            content[f"logs/astraweft-{index}.redacted.jsonl"] = _redacted_log(log_path)
        temporary = destination.with_suffix(".zip.tmp")
        try:
            with zipfile.ZipFile(
                temporary,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                for name, value in content.items():
                    archive.writestr(name, value)
            temporary.chmod(0o600)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return DiagnosticExport(
            path=destination,
            created_at=created_at,
            size_bytes=destination.stat().st_size,
            included_files=tuple(content),
        )

    def inspect_data_migration(self, target_root: Path) -> DataMigrationPreview:
        target = target_root.expanduser().resolve()
        conflicts: list[str] = []
        source_directories = (
            self._paths.config_dir.resolve(),
            self._paths.data_dir.resolve(),
            self._paths.log_dir.resolve(),
        )
        if any(target == source or target.is_relative_to(source) for source in source_directories):
            conflicts.append("目标目录不能位于当前数据目录内")
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            conflicts.append("目标目录必须不存在或为空")
        files = self._migration_files()
        required = sum(source.stat().st_size for source, _relative in files)
        database_size = (
            self._paths.database_path.stat().st_size if self._paths.database_path.exists() else 0
        )
        required += database_size
        available = shutil.disk_usage(_existing_parent(target)).free
        return DataMigrationPreview(
            source_data_path=self._paths.data_dir,
            target_root=target,
            required_bytes=required,
            available_bytes=available,
            file_count=len(files) + (1 if database_size else 0),
            conflicts=tuple(conflicts),
        )

    def stage_data_migration(self, target_root: Path) -> DataMigrationResult:
        preview = self.inspect_data_migration(target_root)
        if not preview.can_stage:
            detail = "；".join(preview.conflicts) or "可用空间不足"
            raise MaintenanceError(f"无法迁移数据目录：{detail}")
        target = preview.target_root
        target.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        staging = target.parent / f".{target.name}.astraweft-partial-{stamp}"
        staging.mkdir(parents=False, exist_ok=False)
        manifest_files: list[_ManifestEntry] = []
        try:
            database_target = staging / "data" / self._paths.database_path.name
            if self._paths.database_path.exists():
                _online_copy(self._paths.database_path, database_target)
                manifest_files.append(_manifest_entry(staging, database_target))
            for source, relative in self._migration_files():
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                manifest_files.append(_manifest_entry(staging, destination))
            for item in manifest_files:
                relative_path = item["path"]
                expected_hash = item["sha256"]
                if _sha256(staging / relative_path) != expected_hash:
                    raise MaintenanceError(f"迁移文件校验失败：{relative_path}")
            manifest_path = staging / "migration.complete.json"
            _write_json_atomic(
                manifest_path,
                {
                    "format": "astraweft-data-migration/v1",
                    "created_at": datetime.now(UTC).isoformat(),
                    "source_database_revision": self.check_database().revision,
                    "files": manifest_files,
                    "total_bytes": sum(item["size_bytes"] for item in manifest_files),
                },
            )
            if target.exists():
                target.rmdir()
            staging.replace(target)
        except Exception:
            _write_json_atomic(
                staging / "migration.failed.json",
                {
                    "format": "astraweft-data-migration-failure/v1",
                    "failed_at": datetime.now(UTC).isoformat(),
                    "source_preserved": True,
                },
            )
            raise
        final_manifest = target / "migration.complete.json"
        total_bytes = sum(item["size_bytes"] for item in manifest_files)
        return DataMigrationResult(
            target_root=target,
            manifest_path=final_manifest,
            total_bytes=total_bytes,
            file_count=len(manifest_files),
        )

    def _backup_to(self, destination: Path, *, created_at: datetime) -> BackupResult:
        if not self._paths.database_path.is_file():
            raise MaintenanceError("数据库尚未创建")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".db.tmp")
        try:
            _online_copy(self._paths.database_path, temporary)
            health = self.check_database(temporary)
            if not health.healthy:
                raise MaintenanceError("备份完整性检查未通过")
            temporary.chmod(0o600)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        final_health = self.check_database(destination)
        return BackupResult(
            path=destination,
            created_at=created_at,
            size_bytes=destination.stat().st_size,
            sha256=_sha256(destination),
            health=final_health,
        )

    def _prune_backups(self) -> None:
        backups = sorted(
            self._paths.backup_dir.glob("astraweft-*.db"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
        for database_path in backups[self._settings.backup_retention_count :]:
            database_path.unlink(missing_ok=True)
            database_path.with_suffix(".json").unlink(missing_ok=True)

    def _migration_files(self) -> tuple[tuple[Path, Path], ...]:
        files: list[tuple[Path, Path]] = []
        excluded_data = {
            self._paths.database_path.resolve(),
            self._paths.pending_restore_path.resolve(),
            self._paths.restore_marker_path.resolve(),
            self._paths.database_path.with_name(self._paths.database_path.name + "-wal").resolve(),
            self._paths.database_path.with_name(self._paths.database_path.name + "-shm").resolve(),
        }
        for prefix, directory in (
            (Path("config"), self._paths.config_dir),
            (Path("data"), self._paths.data_dir),
            (Path("logs"), self._paths.log_dir),
        ):
            if not directory.exists():
                continue
            for source in sorted(directory.rglob("*")):
                if not source.is_file() or source.is_symlink() or source.resolve() in excluded_data:
                    continue
                files.append((source, prefix / source.relative_to(directory)))
        return tuple(files)


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5.0)


def _online_copy(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.unlink(missing_ok=True)
    try:
        with (
            closing(sqlite3.connect(source_path, timeout=5.0)) as source,
            closing(sqlite3.connect(destination_path, timeout=5.0)) as destination,
        ):
            source.backup(destination)
            destination.commit()
        with destination_path.open("rb") as handle:
            os.fsync(handle.fileno())
    except sqlite3.DatabaseError as exc:
        destination_path.unlink(missing_ok=True)
        raise MaintenanceError("无法创建 SQLite 一致性快照") from exc


def _revision(connection: sqlite3.Connection, table_names: tuple[str, ...]) -> str | None:
    if "alembic_version" not in table_names:
        return None
    row = connection.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
    return None if row is None else str(row[0])


def _table_count(connection: sqlite3.Connection, name: str) -> int:
    quoted = name.replace('"', '""')
    # The identifier originates from sqlite_master and is quoted, never user input.
    row = connection.execute(
        f'SELECT COUNT(*) FROM "{quoted}"'  # noqa: S608
    ).fetchone()
    return 0 if row is None else int(row[0])


def _has_sqlite_header(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_label(value: str) -> str:
    sanitized = "".join(character if character.isalnum() else "-" for character in value.lower())
    return "-".join(part for part in sanitized.split("-") if part)[:32] or "manual"


def _existing_parent(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _manifest_entry(root: Path, path: Path) -> _ManifestEntry:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        temporary_path.chmod(0o600)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _redacted_log(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            start = max(0, handle.tell() - 2 * 1024 * 1024)
            handle.seek(start)
            text = handle.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return _json_text({"level": "ERROR", "message": f"log unreadable: {type(exc).__name__}"})
    lines: list[str] = []
    for line in text.splitlines():
        try:
            value: Any = json.loads(line)
        except json.JSONDecodeError:
            value = {"message": line}
        lines.append(json.dumps(redact(value), ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines) + ("\n" if lines else "")
