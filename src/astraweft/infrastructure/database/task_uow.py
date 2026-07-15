"""Task-aware SQLite Unit of Work."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from astraweft.application.events import EventBus
from astraweft.infrastructure.database.task_repositories import (
    SQLArtifactRepository,
    SQLRequestLogRepository,
    SQLTaskAttemptRepository,
    SQLTaskRepository,
)
from astraweft.infrastructure.database.uow import SQLiteUnitOfWork, UnitOfWorkStateError
from astraweft.ports.tasks import TaskUnitOfWork


class SQLiteTaskUnitOfWork(SQLiteUnitOfWork):
    """Expose task repositories only while its session is active."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        events: EventBus,
    ) -> None:
        super().__init__(sessions, events)
        self._tasks: SQLTaskRepository | None = None
        self._attempts: SQLTaskAttemptRepository | None = None
        self._request_logs: SQLRequestLogRepository | None = None
        self._artifacts: SQLArtifactRepository | None = None

    @property
    def tasks(self) -> SQLTaskRepository:
        if self._tasks is None:
            raise UnitOfWorkStateError("Task Unit of Work has not been entered")
        return self._tasks

    @property
    def attempts(self) -> SQLTaskAttemptRepository:
        if self._attempts is None:
            raise UnitOfWorkStateError("Task Unit of Work has not been entered")
        return self._attempts

    @property
    def request_logs(self) -> SQLRequestLogRepository:
        if self._request_logs is None:
            raise UnitOfWorkStateError("Task Unit of Work has not been entered")
        return self._request_logs

    @property
    def artifacts(self) -> SQLArtifactRepository:
        if self._artifacts is None:
            raise UnitOfWorkStateError("Task Unit of Work has not been entered")
        return self._artifacts

    async def __aenter__(self) -> SQLiteTaskUnitOfWork:
        await super().__aenter__()
        self._tasks = SQLTaskRepository(self.session)
        self._attempts = SQLTaskAttemptRepository(self.session)
        self._request_logs = SQLRequestLogRepository(self.session)
        self._artifacts = SQLArtifactRepository(self.session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            await super().__aexit__(exc_type, exc_value, traceback)
        finally:
            self._tasks = None
            self._attempts = None
            self._request_logs = None
            self._artifacts = None


@dataclass(frozen=True, slots=True)
class SQLiteTaskUnitOfWorkFactory:
    sessions: async_sessionmaker[AsyncSession]
    events: EventBus

    def __call__(self) -> TaskUnitOfWork:
        return SQLiteTaskUnitOfWork(self.sessions, self.events)
