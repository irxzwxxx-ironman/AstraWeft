"""Persistence-agnostic state machine and execution records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from astraweft.domain.common import freeze_mapping


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    SUBMITTING = "SUBMITTING"
    RUNNING = "RUNNING"
    POLLING = "POLLING"
    RETRY_WAIT = "RETRY_WAIT"
    CANCELING = "CANCELING"
    RECOVERING = "RECOVERING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    TIMED_OUT = "TIMED_OUT"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"

    @property
    def terminal(self) -> bool:
        return self in {
            self.SUCCESS,
            self.FAILED,
            self.CANCELED,
            self.TIMED_OUT,
            self.NEEDS_ATTENTION,
        }


class AttemptPhase(StrEnum):
    SUBMIT = "SUBMIT"
    POLL = "POLL"
    CANCEL = "CANCEL"
    DOWNLOAD = "DOWNLOAD"


class AttemptStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class TaskTransitionError(ValueError):
    """A state transition would violate durable task semantics."""


_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELED, TaskStatus.TIMED_OUT}),
    TaskStatus.QUEUED: frozenset(
        {TaskStatus.SUBMITTING, TaskStatus.CANCELED, TaskStatus.TIMED_OUT}
    ),
    TaskStatus.SUBMITTING: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.SUCCESS,
            TaskStatus.RETRY_WAIT,
            TaskStatus.FAILED,
            TaskStatus.TIMED_OUT,
            TaskStatus.NEEDS_ATTENTION,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.POLLING,
            TaskStatus.CANCELING,
            TaskStatus.TIMED_OUT,
            TaskStatus.RECOVERING,
            TaskStatus.NEEDS_ATTENTION,
        }
    ),
    TaskStatus.POLLING: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.SUCCESS,
            TaskStatus.RETRY_WAIT,
            TaskStatus.FAILED,
            TaskStatus.CANCELING,
            TaskStatus.CANCELED,
            TaskStatus.TIMED_OUT,
            TaskStatus.RECOVERING,
            TaskStatus.NEEDS_ATTENTION,
        }
    ),
    TaskStatus.RETRY_WAIT: frozenset(
        {
            TaskStatus.QUEUED,
            TaskStatus.POLLING,
            TaskStatus.CANCELED,
            TaskStatus.TIMED_OUT,
        }
    ),
    TaskStatus.CANCELING: frozenset(
        {
            TaskStatus.CANCELED,
            TaskStatus.RUNNING,
            TaskStatus.POLLING,
            TaskStatus.FAILED,
            TaskStatus.NEEDS_ATTENTION,
        }
    ),
    TaskStatus.RECOVERING: frozenset(
        {TaskStatus.POLLING, TaskStatus.QUEUED, TaskStatus.NEEDS_ATTENTION}
    ),
    TaskStatus.SUCCESS: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELED: frozenset(),
    TaskStatus.TIMED_OUT: frozenset(),
    TaskStatus.NEEDS_ATTENTION: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    provider_id: str
    model_id: str | None
    status: TaskStatus
    operation: str
    input: Mapping[str, object]
    provider_config_snapshot: Mapping[str, object]
    normalized_output: Mapping[str, object] | None
    remote_task_id: str | None
    idempotency_key: str
    priority: int
    progress: int | None
    poll_after_at: datetime | None
    timeout_at: datetime | None
    cancel_requested_at: datetime | None
    row_version: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not all((self.id, self.provider_id, self.operation, self.idempotency_key)):
            raise ValueError("task identity fields must not be empty")
        if self.priority < 0:
            raise ValueError("priority must not be negative")
        if self.row_version < 1:
            raise ValueError("row_version must be positive")
        if self.progress is not None and not 0 <= self.progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        object.__setattr__(self, "input", freeze_mapping(self.input))
        object.__setattr__(
            self,
            "provider_config_snapshot",
            freeze_mapping(self.provider_config_snapshot),
        )
        if self.normalized_output is not None:
            object.__setattr__(
                self,
                "normalized_output",
                freeze_mapping(self.normalized_output),
            )
        for timestamp in (
            self.created_at,
            self.updated_at,
            self.poll_after_at,
            self.timeout_at,
            self.cancel_requested_at,
            self.started_at,
            self.completed_at,
        ):
            if timestamp is not None:
                _require_aware(timestamp)
        if self.status.terminal and self.completed_at is None:
            raise ValueError("terminal task requires completed_at")
        if not self.status.terminal and self.completed_at is not None:
            raise ValueError("non-terminal task cannot have completed_at")

    def transition(
        self,
        target: TaskStatus,
        at: datetime,
        *,
        remote_task_id: str | None = None,
        progress: int | None = None,
        normalized_output: Mapping[str, object] | None = None,
        poll_after_at: datetime | None = None,
    ) -> Task:
        _require_aware(at)
        if target not in _TRANSITIONS[self.status]:
            raise TaskTransitionError(f"invalid task transition: {self.status} -> {target}")
        if target is TaskStatus.SUCCESS and normalized_output is None:
            raise TaskTransitionError("SUCCESS requires normalized output")
        if target in {TaskStatus.RUNNING, TaskStatus.POLLING}:
            effective_remote_id = remote_task_id or self.remote_task_id
            if not effective_remote_id:
                raise TaskTransitionError(f"{target} requires remote_task_id")
        completed_at = at if target.terminal else None
        effective_progress = 100 if target is TaskStatus.SUCCESS else progress
        if effective_progress is None:
            effective_progress = self.progress
        effective_output = (
            self.normalized_output if normalized_output is None else normalized_output
        )
        return replace(
            self,
            status=target,
            remote_task_id=remote_task_id or self.remote_task_id,
            progress=effective_progress,
            normalized_output=effective_output,
            poll_after_at=poll_after_at,
            started_at=at
            if target is TaskStatus.SUBMITTING and self.started_at is None
            else self.started_at,
            completed_at=completed_at,
            updated_at=at,
            row_version=self.row_version + 1,
        )

    def request_cancel(self, at: datetime) -> Task:
        _require_aware(at)
        if self.status.terminal:
            raise TaskTransitionError("terminal task cannot be canceled")
        return replace(
            self,
            cancel_requested_at=at,
            updated_at=at,
            row_version=self.row_version + 1,
        )

    def schedule_poll(
        self,
        at: datetime,
        *,
        poll_after_at: datetime,
        progress: int | None = None,
    ) -> Task:
        _require_aware(at)
        _require_aware(poll_after_at)
        if self.status is not TaskStatus.POLLING or self.remote_task_id is None:
            raise TaskTransitionError("only a remote POLLING task can be rescheduled")
        effective_progress = self.progress if progress is None else progress
        return replace(
            self,
            progress=effective_progress,
            poll_after_at=poll_after_at,
            updated_at=at,
            row_version=self.row_version + 1,
        )


@dataclass(frozen=True, slots=True)
class TaskAttempt:
    id: str
    task_id: str
    attempt_no: int
    phase: AttemptPhase
    status: AttemptStatus
    error_code: str | None
    error_message: str | None
    provider_error: Mapping[str, object]
    retryable: bool | None
    retry_after_at: datetime | None
    started_at: datetime
    ended_at: datetime | None

    def __post_init__(self) -> None:
        if not self.id or not self.task_id or self.attempt_no < 1:
            raise ValueError("invalid task attempt identity")
        object.__setattr__(self, "provider_error", freeze_mapping(self.provider_error))
        _require_aware(self.started_at)
        if self.retry_after_at is not None:
            _require_aware(self.retry_after_at)
        if self.ended_at is not None:
            _require_aware(self.ended_at)
        if self.status is AttemptStatus.RUNNING and self.ended_at is not None:
            raise ValueError("running attempt cannot have ended_at")
        if self.status is not AttemptStatus.RUNNING and self.ended_at is None:
            raise ValueError("terminal attempt requires ended_at")


@dataclass(frozen=True, slots=True)
class RequestLog:
    id: str
    attempt_id: str | None
    provider_id: str
    model_id: str | None
    trace_id: str
    operation: str
    method: str | None
    url_template: str | None
    http_status: int | None
    latency_ms: int
    request_summary: Mapping[str, object]
    response_summary: Mapping[str, object]
    usage: Mapping[str, object]
    amount_micros: int | None
    currency: str | None
    error_code: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        if not all((self.id, self.provider_id, self.trace_id, self.operation)):
            raise ValueError("request log identity fields must not be empty")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must not be negative")
        if self.http_status is not None and not 100 <= self.http_status <= 599:
            raise ValueError("http_status must be a valid HTTP status")
        if (self.amount_micros is None) != (self.currency is None):
            raise ValueError("known cost requires amount and currency")
        if self.amount_micros is not None and self.amount_micros < 0:
            raise ValueError("amount_micros must not be negative")
        object.__setattr__(self, "request_summary", freeze_mapping(self.request_summary))
        object.__setattr__(self, "response_summary", freeze_mapping(self.response_summary))
        object.__setattr__(self, "usage", freeze_mapping(self.usage))
        _require_aware(self.created_at)


@dataclass(frozen=True, slots=True)
class Artifact:
    id: str
    task_id: str | None
    kind: str
    relative_path: str
    mime_type: str
    size_bytes: int
    sha256: str
    metadata: Mapping[str, object]
    source_url_redacted: str | None
    created_at: datetime
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        if not all((self.id, self.kind, self.relative_path, self.mime_type, self.sha256)):
            raise ValueError("artifact identity fields must not be empty")
        if self.relative_path.startswith("/") or ".." in self.relative_path.split("/"):
            raise ValueError("artifact path must be relative and contained")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        if len(self.sha256) != 64:
            raise ValueError("sha256 must be a 64-character digest")
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))
        _require_aware(self.created_at)
        if self.deleted_at is not None:
            _require_aware(self.deleted_at)

    def move_to_trash(self, at: datetime) -> Artifact:
        _require_aware(at)
        if self.deleted_at is not None:
            raise ValueError("artifact is already in trash")
        return replace(self, deleted_at=at)

    def restore_from_trash(self) -> Artifact:
        if self.deleted_at is None:
            raise ValueError("artifact is not in trash")
        return replace(self, deleted_at=None)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
