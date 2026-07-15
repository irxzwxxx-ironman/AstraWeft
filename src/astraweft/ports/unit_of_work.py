"""Transaction boundary used by application commands."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self


class PostCommitDispatchError(RuntimeError):
    """The database commit succeeded but a staged event handler failed."""


class UnitOfWork(Protocol):
    """One explicit transaction with post-commit event staging."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    def publish_after_commit(self, event: object) -> None: ...


class UnitOfWorkFactory(Protocol):
    """Create a fresh transaction boundary per application command."""

    def __call__(self) -> UnitOfWork: ...
