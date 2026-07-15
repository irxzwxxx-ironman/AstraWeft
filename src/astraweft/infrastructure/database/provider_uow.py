"""Provider-aware SQLite Unit of Work."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from astraweft.application.events import EventBus
from astraweft.infrastructure.database.provider_repositories import (
    SQLCredentialRepository,
    SQLModelRepository,
    SQLProviderRepository,
)
from astraweft.infrastructure.database.uow import SQLiteUnitOfWork, UnitOfWorkStateError
from astraweft.ports.providers import ProviderUnitOfWork


class SQLiteProviderUnitOfWork(SQLiteUnitOfWork):
    """Expose Provider repositories only while its session is active."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        events: EventBus,
    ) -> None:
        super().__init__(sessions, events)
        self._providers: SQLProviderRepository | None = None
        self._credentials: SQLCredentialRepository | None = None
        self._models: SQLModelRepository | None = None

    @property
    def providers(self) -> SQLProviderRepository:
        if self._providers is None:
            raise UnitOfWorkStateError("Provider Unit of Work has not been entered")
        return self._providers

    @property
    def credentials(self) -> SQLCredentialRepository:
        if self._credentials is None:
            raise UnitOfWorkStateError("Provider Unit of Work has not been entered")
        return self._credentials

    @property
    def models(self) -> SQLModelRepository:
        if self._models is None:
            raise UnitOfWorkStateError("Provider Unit of Work has not been entered")
        return self._models

    async def __aenter__(self) -> SQLiteProviderUnitOfWork:
        await super().__aenter__()
        self._providers = SQLProviderRepository(self.session)
        self._credentials = SQLCredentialRepository(self.session)
        self._models = SQLModelRepository(self.session)
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
            self._providers = None
            self._credentials = None
            self._models = None


@dataclass(frozen=True, slots=True)
class SQLiteProviderUnitOfWorkFactory:
    sessions: async_sessionmaker[AsyncSession]
    events: EventBus

    def __call__(self) -> ProviderUnitOfWork:
        return SQLiteProviderUnitOfWork(self.sessions, self.events)
