"""Async SQLite runtime with product safety pragmas."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from sqlalchemy import URL, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _configure_sqlite_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


class Database:
    """Own the async engine and short-lived Session factory."""

    def __init__(self, path: Path) -> None:
        url = URL.create("sqlite+aiosqlite", database=str(path))
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        event.listen(self.engine.sync_engine, "connect", _configure_sqlite_connection)
        self.sessions = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def ping(self) -> bool:
        """Verify that SQLite accepts a short read."""
        async with self.engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            return cast(int, result.scalar_one()) == 1

    async def close(self) -> None:
        """Release pooled connections."""
        await self.engine.dispose()
