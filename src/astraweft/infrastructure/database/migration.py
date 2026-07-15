"""Programmatic Alembic migration entry point."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import URL


def _alembic_config(database_path: Path) -> Config:
    package_dir = Path(__file__).resolve().parent
    config = Config(str(package_dir / "alembic.ini"))
    config.set_main_option("script_location", str(package_dir / "migrations"))
    url = URL.create("sqlite", database=str(database_path)).render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def run_migrations(database_path: Path) -> None:
    """Upgrade a local database to the latest bundled revision."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(_alembic_config(database_path), "head")


def latest_revision(database_path: Path) -> str:
    """Return the single bundled Alembic head without opening the database."""
    head = ScriptDirectory.from_config(_alembic_config(database_path)).get_current_head()
    if head is None:
        raise RuntimeError("migration graph has no head revision")
    return head


def database_revision(database_path: Path) -> str | None:
    """Read the current revision from an existing database, if initialized."""
    if not database_path.is_file() or database_path.stat().st_size == 0:
        return None
    try:
        with sqlite3.connect(database_path) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
            ).fetchone()
            if table is None:
                return None
            row = connection.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
    except sqlite3.DatabaseError:
        return None
    return None if row is None else str(row[0])
