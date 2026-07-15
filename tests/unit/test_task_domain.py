"""Task state machine and execution record invariants."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from astraweft.domain.task import (
    Artifact,
    AttemptPhase,
    AttemptStatus,
    RequestLog,
    Task,
    TaskAttempt,
    TaskStatus,
    TaskTransitionError,
)


def _task() -> Task:
    now = datetime(2026, 7, 15, tzinfo=UTC)
    return Task(
        id="task-1",
        provider_id="provider-1",
        model_id="model-1",
        status=TaskStatus.CREATED,
        operation="text.generate",
        input={"prompt": "hello"},
        provider_config_snapshot={"region": "local"},
        normalized_output=None,
        remote_task_id=None,
        idempotency_key="stable-key",
        priority=100,
        progress=0,
        poll_after_at=None,
        timeout_at=now + timedelta(minutes=5),
        cancel_requested_at=None,
        row_version=1,
        created_at=now,
        updated_at=now,
    )


def test_sync_success_follows_guarded_transitions() -> None:
    task = _task()
    queued = task.transition(TaskStatus.QUEUED, task.created_at)
    submitting = queued.transition(TaskStatus.SUBMITTING, task.created_at)
    success = submitting.transition(
        TaskStatus.SUCCESS,
        task.created_at,
        normalized_output={"data": {"text": "done"}},
    )

    assert success.status is TaskStatus.SUCCESS
    assert success.progress == 100
    assert success.completed_at == task.created_at
    assert success.row_version == 4
    assert success.normalized_output is not None
    assert success.normalized_output["data"] == {"text": "done"}


def test_async_poll_and_recovery_require_remote_identity() -> None:
    now = _task().created_at
    submitting = _task().transition(TaskStatus.QUEUED, now).transition(TaskStatus.SUBMITTING, now)
    with pytest.raises(TaskTransitionError, match="remote_task_id"):
        submitting.transition(TaskStatus.RUNNING, now)

    running = submitting.transition(TaskStatus.RUNNING, now, remote_task_id="remote-1")
    polling = running.transition(TaskStatus.POLLING, now, progress=50)
    recovering = polling.transition(TaskStatus.RECOVERING, now)
    resumed = recovering.transition(TaskStatus.POLLING, now)
    assert resumed.remote_task_id == "remote-1"
    assert resumed.progress == 50


def test_invalid_terminal_and_cancel_transitions_are_rejected() -> None:
    now = _task().created_at
    with pytest.raises(TaskTransitionError, match="invalid task transition"):
        _task().transition(TaskStatus.SUCCESS, now, normalized_output={})

    canceled = _task().transition(TaskStatus.CANCELED, now)
    assert canceled.status.terminal
    with pytest.raises(TaskTransitionError, match="terminal task"):
        canceled.request_cancel(now)
    with pytest.raises(ValueError, match="terminal task requires"):
        replace(canceled, completed_at=None)


def test_empty_normalized_output_is_preserved_on_success() -> None:
    now = _task().created_at
    success = (
        _task()
        .transition(TaskStatus.QUEUED, now)
        .transition(TaskStatus.SUBMITTING, now)
        .transition(TaskStatus.SUCCESS, now, normalized_output={})
    )

    assert success.normalized_output == {}


def test_attempt_and_request_log_preserve_unknown_cost() -> None:
    now = _task().created_at
    attempt = TaskAttempt(
        id="attempt-1",
        task_id="task-1",
        attempt_no=1,
        phase=AttemptPhase.SUBMIT,
        status=AttemptStatus.SUCCESS,
        error_code=None,
        error_message=None,
        provider_error={},
        retryable=None,
        retry_after_at=None,
        started_at=now,
        ended_at=now,
    )
    request_log = RequestLog(
        id="log-1",
        attempt_id=attempt.id,
        provider_id="provider-1",
        model_id="model-1",
        trace_id="trace-1",
        operation="text.generate",
        method=None,
        url_template=None,
        http_status=None,
        latency_ms=12,
        request_summary={"field_names": ["prompt"]},
        response_summary={"status": "completed"},
        usage={"input_tokens": 1},
        amount_micros=None,
        currency=None,
        error_code=None,
        created_at=now,
    )

    assert request_log.amount_micros is None
    with pytest.raises(ValueError, match="amount and currency"):
        replace(request_log, amount_micros=0)


def test_artifact_requires_safe_relative_path_and_digest() -> None:
    artifact = Artifact(
        id="artifact-1",
        task_id="task-1",
        kind="text",
        relative_path="task-1/result.txt",
        mime_type="text/plain",
        size_bytes=4,
        sha256="a" * 64,
        metadata={"lineage": "provider-output"},
        source_url_redacted=None,
        created_at=_task().created_at,
    )

    assert artifact.relative_path == "task-1/result.txt"
    with pytest.raises(ValueError, match="relative and contained"):
        replace(artifact, relative_path="../secret.txt")


def test_task_execution_records_reject_corrupt_persisted_values() -> None:
    with pytest.raises(ValueError):
        replace(_task(), id="")
    with pytest.raises(ValueError):
        replace(_task(), priority=-1)
    with pytest.raises(ValueError):
        replace(_task(), row_version=0)
    with pytest.raises(ValueError):
        replace(_task(), progress=101)


def test_attempt_log_and_artifact_boundary_errors_are_explicit() -> None:
    now = _task().created_at
    with pytest.raises(TaskTransitionError, match="normalized output"):
        (
            _task()
            .transition(TaskStatus.QUEUED, now)
            .transition(TaskStatus.SUBMITTING, now)
            .transition(TaskStatus.SUCCESS, now)
        )
    with pytest.raises(TaskTransitionError, match="only a remote"):
        _task().schedule_poll(now, poll_after_at=now)
    with pytest.raises(ValueError, match="timestamps"):
        replace(_task(), created_at=datetime(2026, 7, 15))

    running = TaskAttempt(
        id="attempt-1",
        task_id="task-1",
        attempt_no=1,
        phase=AttemptPhase.SUBMIT,
        status=AttemptStatus.RUNNING,
        error_code=None,
        error_message=None,
        provider_error={},
        retryable=None,
        retry_after_at=None,
        started_at=now,
        ended_at=None,
    )
    with pytest.raises(ValueError, match="identity"):
        replace(running, attempt_no=0)
    with pytest.raises(ValueError, match="running attempt"):
        replace(running, ended_at=now)
    with pytest.raises(ValueError, match="terminal attempt"):
        replace(running, status=AttemptStatus.SUCCESS)

    log = RequestLog(
        id="log-1",
        attempt_id=None,
        provider_id="provider-1",
        model_id=None,
        trace_id="trace-1",
        operation="text.generate",
        method="POST",
        url_template="/v1/generate",
        http_status=200,
        latency_ms=1,
        request_summary={},
        response_summary={},
        usage={},
        amount_micros=0,
        currency="USD",
        error_code=None,
        created_at=now,
    )
    with pytest.raises(ValueError, match="identity"):
        replace(log, trace_id="")
    with pytest.raises(ValueError, match="latency"):
        replace(log, latency_ms=-1)
    with pytest.raises(ValueError, match="HTTP status"):
        replace(log, http_status=99)
    with pytest.raises(ValueError, match="negative"):
        replace(log, amount_micros=-1)

    artifact = Artifact(
        id="artifact-1",
        task_id=None,
        kind="TEXT",
        relative_path="result.txt",
        mime_type="text/plain",
        size_bytes=1,
        sha256="a" * 64,
        metadata={},
        source_url_redacted=None,
        created_at=now,
    )
    with pytest.raises(ValueError, match="identity"):
        replace(artifact, mime_type="")
    with pytest.raises(ValueError, match="size_bytes"):
        replace(artifact, size_bytes=-1)
    with pytest.raises(ValueError, match="64-character"):
        replace(artifact, sha256="short")
