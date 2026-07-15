"""Verify packaged upgrade backup and rollback recovery against an older schema."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from alembic import command as alembic_command
from alembic.config import Config
from smoke_desktop import packaged_executable
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = PROJECT_ROOT / "dist" / "desktop"
SOURCE_REVISION = "20260715_0006"
TARGET_REVISION = "20260715_0007"
PROBE_VALUE = "phase8-upgrade-preserved"


def _alembic_config(database: Path) -> Config:
    package_dir = PROJECT_ROOT / "src" / "astraweft" / "infrastructure" / "database"
    config = Config(str(package_dir / "alembic.ini"))
    config.set_main_option("script_location", str(package_dir / "migrations"))
    url = URL.create("sqlite", database=str(database)).render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _health(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        revision = str(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0])
        probe = str(connection.execute("SELECT value FROM release_probe").fetchone()[0])
    return {
        "revision": revision,
        "integrity": integrity,
        "foreign_key_issues": foreign_keys,
        "probe": probe,
        "sha256": _sha256(path),
    }


def _launch(executable: Path, data_root: Path) -> None:
    environment = os.environ.copy()
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run(  # noqa: S603
        [str(executable), "--data-dir", str(data_root), "--quit-after-ms", "1200"],
        capture_output=True,
        text=True,
        timeout=45,
        env=environment,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "no process output").strip()
        raise RuntimeError(f"packaged upgrade launch failed ({result.returncode}): {details}")


def run_upgrade_smoke(dist_root: Path) -> dict[str, Any]:
    executable = packaged_executable(dist_root.resolve())
    with tempfile.TemporaryDirectory(prefix="astraweft-upgrade-smoke-") as temporary:
        data_root = Path(temporary)
        database = data_root / "data" / "astraweft.db"
        database.parent.mkdir(parents=True)
        alembic_command.upgrade(_alembic_config(database), SOURCE_REVISION)
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE release_probe (value TEXT NOT NULL)")
            connection.execute("INSERT INTO release_probe (value) VALUES (?)", (PROBE_VALUE,))
        before = _health(database)

        _launch(executable, data_root)
        after_upgrade = _health(database)
        if after_upgrade["revision"] != TARGET_REVISION or after_upgrade["probe"] != PROBE_VALUE:
            raise RuntimeError("upgrade did not preserve data at the target revision")

        backups = sorted((data_root / "data" / "backups").glob("*-pre-migration.db"))
        if len(backups) != 1:
            raise RuntimeError(f"expected one pre-migration backup, found {len(backups)}")
        backup = backups[0]
        backup_health = _health(backup)
        if backup_health["revision"] != SOURCE_REVISION or backup_health["integrity"] != "ok":
            raise RuntimeError("pre-migration backup is not a healthy source-revision database")

        safety_copy = database.with_suffix(".post-upgrade.db")
        shutil.copy2(database, safety_copy)
        shutil.copy2(backup, database)
        after_rollback = _health(database)
        if after_rollback["revision"] != SOURCE_REVISION:
            raise RuntimeError("rollback copy did not restore the source revision")

        _launch(executable, data_root)
        after_recovery = _health(database)
        if after_recovery["revision"] != TARGET_REVISION:
            raise RuntimeError("application did not recover forward after rollback")

        return {
            "status": "passed",
            "source_revision": SOURCE_REVISION,
            "target_revision": TARGET_REVISION,
            "before": before,
            "after_upgrade": after_upgrade,
            "pre_migration_backup": {"name": backup.name, **backup_health},
            "after_rollback": after_rollback,
            "after_recovery": after_recovery,
            "data_preserved": all(
                health["probe"] == PROBE_VALUE
                for health in (before, after_upgrade, backup_health, after_rollback, after_recovery)
            ),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_upgrade_smoke(args.dist_dir)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
