"""SQLAlchemy implementation of the application transaction boundary."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from astraweft.application.events import EventBus
from astraweft.ports.unit_of_work import PostCommitDispatchError


class UnitOfWorkStateError(RuntimeError):
    """Raised when a transaction is used outside its valid lifecycle."""


class PostCommitEventError(PostCommitDispatchError):
    """An event handler failed after the database commit succeeded."""

    def __init__(self, event: object, cause: BaseException) -> None:
        super().__init__("database commit succeeded but post-commit event dispatch failed")
        self.event = event
        self.__cause__ = cause


class SQLiteUnitOfWork:
    """Short-lived AsyncSession with explicit commit and safe rollback."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        events: EventBus,
    ) -> None:
        self._sessions = sessions
        self._events = events
        self._session: AsyncSession | None = None
        self._pending_events: list[object] = []
        self._committed = False

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise UnitOfWorkStateError("unit of work has not been entered")
        return self._session

    async def __aenter__(self) -> SQLiteUnitOfWork:
        if self._session is not None:
            raise UnitOfWorkStateError("unit of work cannot be entered twice")
        self._session = self._sessions()
        self._committed = False
        self._pending_events.clear()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        try:
            if not self._committed:
                await self.rollback()
        finally:
            session = self._session
            self._session = None
            self._pending_events.clear()
            if session is not None:
                await session.close()

    def publish_after_commit(self, event: object) -> None:
        if self._session is None:
            raise UnitOfWorkStateError("unit of work has not been entered")
        if self._committed:
            raise UnitOfWorkStateError("transaction has already been committed")
        self._pending_events.append(event)

    async def commit(self) -> None:
        if self._committed:
            raise UnitOfWorkStateError("transaction has already been committed")
        session = self.session
        await session.commit()
        self._committed = True
        pending = tuple(self._pending_events)
        self._pending_events.clear()
        for event in pending:
            try:
                await self._events.publish(event)
            except Exception as exc:
                raise PostCommitEventError(event, exc) from exc

    async def rollback(self) -> None:
        if self._committed:
            raise UnitOfWorkStateError("transaction has already been committed")
        session = self.session
        await session.rollback()
        self._pending_events.clear()


@dataclass(frozen=True, slots=True)
class SQLiteUnitOfWorkFactory:
    """Build one SQLiteUnitOfWork for each application command."""

    sessions: async_sessionmaker[AsyncSession]
    events: EventBus

    def __call__(self) -> SQLiteUnitOfWork:
        return SQLiteUnitOfWork(self.sessions, self.events)
