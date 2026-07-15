"""ComfyUI-aware SQLite Unit of Work."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from astraweft.application.events import EventBus
from astraweft.infrastructure.database.comfyui_repositories import (
    SQLComfyUIExecutionRepository,
    SQLComfyUIInstanceRepository,
    SQLComfyUITemplateRepository,
)
from astraweft.infrastructure.database.task_repositories import SQLArtifactRepository
from astraweft.infrastructure.database.uow import SQLiteUnitOfWork, UnitOfWorkStateError
from astraweft.ports.comfyui import ComfyUIUnitOfWork


class SQLiteComfyUIUnitOfWork(SQLiteUnitOfWork):
    """Expose ComfyUI repositories only while its transaction is active."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        events: EventBus,
    ) -> None:
        super().__init__(sessions, events)
        self._instances: SQLComfyUIInstanceRepository | None = None
        self._templates: SQLComfyUITemplateRepository | None = None
        self._executions: SQLComfyUIExecutionRepository | None = None
        self._artifacts: SQLArtifactRepository | None = None

    @property
    def instances(self) -> SQLComfyUIInstanceRepository:
        if self._instances is None:
            raise UnitOfWorkStateError("ComfyUI Unit of Work has not been entered")
        return self._instances

    @property
    def templates(self) -> SQLComfyUITemplateRepository:
        if self._templates is None:
            raise UnitOfWorkStateError("ComfyUI Unit of Work has not been entered")
        return self._templates

    @property
    def executions(self) -> SQLComfyUIExecutionRepository:
        if self._executions is None:
            raise UnitOfWorkStateError("ComfyUI Unit of Work has not been entered")
        return self._executions

    @property
    def artifacts(self) -> SQLArtifactRepository:
        if self._artifacts is None:
            raise UnitOfWorkStateError("ComfyUI Unit of Work has not been entered")
        return self._artifacts

    async def __aenter__(self) -> SQLiteComfyUIUnitOfWork:
        await super().__aenter__()
        self._instances = SQLComfyUIInstanceRepository(self.session)
        self._templates = SQLComfyUITemplateRepository(self.session)
        self._executions = SQLComfyUIExecutionRepository(self.session)
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
            self._instances = None
            self._templates = None
            self._executions = None
            self._artifacts = None


@dataclass(frozen=True, slots=True)
class SQLiteComfyUIUnitOfWorkFactory:
    sessions: async_sessionmaker[AsyncSession]
    events: EventBus

    def __call__(self) -> ComfyUIUnitOfWork:
        return SQLiteComfyUIUnitOfWork(self.sessions, self.events)
