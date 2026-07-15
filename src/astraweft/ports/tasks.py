"""Durable task runtime persistence interfaces."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from astraweft.domain.task import (
    Artifact,
    AttemptStatus,
    RequestLog,
    Task,
    TaskAttempt,
    TaskStatus,
)


class TaskRepository(Protocol):
    async def add(self, task: Task) -> None: ...

    async def get(self, task_id: str) -> Task | None: ...

    async def get_by_idempotency_key(self, idempotency_key: str) -> Task | None: ...

    async def list_recent(self, *, limit: int = 1000) -> tuple[Task, ...]: ...

    async def list_by_status(
        self,
        statuses: frozenset[TaskStatus],
        *,
        limit: int = 1000,
    ) -> tuple[Task, ...]: ...

    async def list_ready(self, at: datetime, *, limit: int) -> tuple[Task, ...]: ...

    async def update(self, task: Task, *, expected_version: int) -> None: ...


class TaskAttemptRepository(Protocol):
    async def add(self, attempt: TaskAttempt) -> None: ...

    async def get(self, attempt_id: str) -> TaskAttempt | None: ...

    async def next_attempt_no(self, task_id: str) -> int: ...

    async def list_for_task(self, task_id: str) -> tuple[TaskAttempt, ...]: ...

    async def update(
        self,
        attempt: TaskAttempt,
        *,
        expected_status: AttemptStatus,
    ) -> None: ...


class RequestLogRepository(Protocol):
    async def add(self, request_log: RequestLog) -> None: ...

    async def list_recent(self, *, limit: int = 1000) -> tuple[RequestLog, ...]: ...

    async def delete_before(self, cutoff: datetime) -> int: ...


class ArtifactRepository(Protocol):
    async def add(self, artifact: Artifact) -> None: ...

    async def get(self, artifact_id: str) -> Artifact | None: ...

    async def list_for_task(self, task_id: str) -> tuple[Artifact, ...]: ...

    async def list_recent(self, *, limit: int = 1000) -> tuple[Artifact, ...]: ...

    async def list_trashed(self, *, limit: int = 1000) -> tuple[Artifact, ...]: ...

    async def workflow_reference_count(self, artifact_id: str) -> int: ...

    async def update_deleted_at(self, artifact: Artifact) -> None: ...

    async def delete(self, artifact_id: str) -> None: ...


class TaskUnitOfWork(Protocol):
    @property
    def tasks(self) -> TaskRepository: ...

    @property
    def attempts(self) -> TaskAttemptRepository: ...

    @property
    def request_logs(self) -> RequestLogRepository: ...

    @property
    def artifacts(self) -> ArtifactRepository: ...

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


class TaskUnitOfWorkFactory(Protocol):
    def __call__(self) -> TaskUnitOfWork: ...
