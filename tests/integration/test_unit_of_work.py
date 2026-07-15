"""SQLite Unit of Work transaction and post-commit event tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import select

from astraweft.application.events import EventBus
from astraweft.infrastructure.database import Database, SQLiteUnitOfWorkFactory, run_migrations
from astraweft.infrastructure.database.models import AppSetting
from astraweft.infrastructure.database.uow import (
    PostCommitEventError,
    SQLiteUnitOfWork,
    UnitOfWorkStateError,
)


@dataclass(frozen=True)
class SettingChanged:
    key: str


async def _stored_keys(database: Database) -> list[str]:
    async with database.sessions() as session:
        result = await session.execute(select(AppSetting.key).order_by(AppSetting.key))
        return list(result.scalars())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_commit_persists_before_event_dispatch(tmp_path: Path) -> None:
    database_path = tmp_path / "astraweft.db"
    run_migrations(database_path)
    database = Database(database_path)
    events = EventBus()
    observed: list[tuple[str, list[str]]] = []

    async def handle(event: SettingChanged) -> None:
        observed.append((event.key, await _stored_keys(database)))

    events.subscribe(SettingChanged, handle)
    factory = SQLiteUnitOfWorkFactory(database.sessions, events)
    try:
        async with factory() as uow:
            uow.session.add(AppSetting(key="theme", value_json='"dark"', updated_at="now"))
            uow.publish_after_commit(SettingChanged("theme"))
            await uow.commit()
        assert observed == [("theme", ["theme"])]
        assert await _stored_keys(database) == ["theme"]
    finally:
        await database.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_uncommitted_and_exceptional_work_rolls_back(tmp_path: Path) -> None:
    database_path = tmp_path / "astraweft.db"
    run_migrations(database_path)
    database = Database(database_path)
    factory = SQLiteUnitOfWorkFactory(database.sessions, EventBus())
    try:
        async with factory() as uow:
            uow.session.add(AppSetting(key="one", value_json="1", updated_at="now"))

        with pytest.raises(RuntimeError, match="stop"):
            async with factory() as uow:
                uow.session.add(AppSetting(key="two", value_json="2", updated_at="now"))
                raise RuntimeError("stop")

        assert await _stored_keys(database) == []
    finally:
        await database.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unit_of_work_rejects_invalid_lifecycle(tmp_path: Path) -> None:
    database_path = tmp_path / "astraweft.db"
    run_migrations(database_path)
    database = Database(database_path)
    uow = SQLiteUnitOfWork(database.sessions, EventBus())
    try:
        with pytest.raises(UnitOfWorkStateError):
            _ = uow.session
        with pytest.raises(UnitOfWorkStateError):
            uow.publish_after_commit(object())
        async with uow:
            with pytest.raises(UnitOfWorkStateError):
                await uow.__aenter__()
            await uow.commit()
            with pytest.raises(UnitOfWorkStateError):
                await uow.commit()
            with pytest.raises(UnitOfWorkStateError):
                await uow.rollback()
            with pytest.raises(UnitOfWorkStateError):
                uow.publish_after_commit(object())
    finally:
        await database.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_commit_dispatch_failure_is_explicit_and_data_stays_committed(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "astraweft.db"
    run_migrations(database_path)
    database = Database(database_path)
    events = EventBus()

    def fail(_event: SettingChanged) -> None:
        raise RuntimeError("subscriber failed")

    events.subscribe(SettingChanged, fail)
    factory = SQLiteUnitOfWorkFactory(database.sessions, events)
    try:
        with pytest.raises(PostCommitEventError) as error:
            async with factory() as uow:
                uow.session.add(AppSetting(key="saved", value_json="1", updated_at="now"))
                uow.publish_after_commit(SettingChanged("saved"))
                await uow.commit()

        assert isinstance(error.value.__cause__, RuntimeError)
        assert await _stored_keys(database) == ["saved"]
    finally:
        await database.close()
