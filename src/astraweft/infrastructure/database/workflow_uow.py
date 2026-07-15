"""Workflow-aware SQLite Unit of Work."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from astraweft.application.events import EventBus
from astraweft.infrastructure.database.uow import SQLiteUnitOfWork, UnitOfWorkStateError
from astraweft.infrastructure.database.workflow_repositories import (
    SQLWorkflowDefinitionRepository,
    SQLWorkflowRunRepository,
)
from astraweft.ports.workflows import WorkflowUnitOfWork


class SQLiteWorkflowUnitOfWork(SQLiteUnitOfWork):
    """Expose workflow repositories only while its transaction is active."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        events: EventBus,
    ) -> None:
        super().__init__(sessions, events)
        self._definitions: SQLWorkflowDefinitionRepository | None = None
        self._runs: SQLWorkflowRunRepository | None = None

    @property
    def definitions(self) -> SQLWorkflowDefinitionRepository:
        if self._definitions is None:
            raise UnitOfWorkStateError("Workflow Unit of Work has not been entered")
        return self._definitions

    @property
    def runs(self) -> SQLWorkflowRunRepository:
        if self._runs is None:
            raise UnitOfWorkStateError("Workflow Unit of Work has not been entered")
        return self._runs

    async def __aenter__(self) -> SQLiteWorkflowUnitOfWork:
        await super().__aenter__()
        self._definitions = SQLWorkflowDefinitionRepository(self.session)
        self._runs = SQLWorkflowRunRepository(self.session)
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
            self._definitions = None
            self._runs = None


@dataclass(frozen=True, slots=True)
class SQLiteWorkflowUnitOfWorkFactory:
    sessions: async_sessionmaker[AsyncSession]
    events: EventBus

    def __call__(self) -> WorkflowUnitOfWork:
        return SQLiteWorkflowUnitOfWork(self.sessions, self.events)
