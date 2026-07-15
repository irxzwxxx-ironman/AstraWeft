"""Durable task repository round-trip and concurrency tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from astraweft.application.providers import CreateProvider
from astraweft.bootstrap.container import build_app_context
from astraweft.domain.task import (
    Artifact,
    AttemptPhase,
    AttemptStatus,
    RequestLog,
    Task,
    TaskAttempt,
    TaskStatus,
)
from astraweft.infrastructure.database import SQLiteTaskUnitOfWorkFactory
from astraweft.infrastructure.database.task_repositories import (
    TaskOptimisticConcurrencyError,
)
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.secrets import SecretValue


@pytest.mark.integration
@pytest.mark.asyncio
async def test_task_records_round_trip_and_reject_stale_worker(
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Runtime Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = (await context.provider_service.sync_models(provider.id))[0]
        now = context.clock.now()
        task = Task(
            id=context.ids.new(),
            provider_id=provider.id,
            model_id=model.id,
            status=TaskStatus.CREATED,
            operation=next(iter(model.operations)),
            input={"prompt": "hello"},
            provider_config_snapshot=provider.config,
            normalized_output=None,
            remote_task_id=None,
            idempotency_key="task-stable-key",
            priority=50,
            progress=0,
            poll_after_at=None,
            timeout_at=now + timedelta(minutes=5),
            cancel_requested_at=None,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        attempt = TaskAttempt(
            id=context.ids.new(),
            task_id=task.id,
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
        request_log = RequestLog(
            id=context.ids.new(),
            attempt_id=attempt.id,
            provider_id=provider.id,
            model_id=model.id,
            trace_id="trace-1",
            operation=task.operation,
            method=None,
            url_template=None,
            http_status=None,
            latency_ms=3,
            request_summary={"field_names": ["prompt"]},
            response_summary={},
            usage={},
            amount_micros=None,
            currency=None,
            error_code=None,
            created_at=now,
        )
        artifact = Artifact(
            id=context.ids.new(),
            task_id=task.id,
            kind="TEXT",
            relative_path=f"2026/07/{task.id}/result.txt",
            mime_type="text/plain",
            size_bytes=5,
            sha256="a" * 64,
            metadata={"source": "mock"},
            source_url_redacted=None,
            created_at=now,
        )
        factory = SQLiteTaskUnitOfWorkFactory(context.database.sessions, context.events)
        async with factory() as uow:
            await uow.tasks.add(task)
            await uow.attempts.add(attempt)
            await uow.request_logs.add(request_log)
            await uow.artifacts.add(artifact)
            await uow.commit()

        queued = task.transition(TaskStatus.QUEUED, now)
        async with factory() as uow:
            await uow.tasks.update(queued, expected_version=task.row_version)
            await uow.commit()

        async with factory() as uow:
            loaded = await uow.tasks.get(task.id)
            ready = await uow.tasks.list_ready(now, limit=10)
            attempts = await uow.attempts.list_for_task(task.id)
            logs = await uow.request_logs.list_recent()
            artifacts = await uow.artifacts.list_for_task(task.id)

        assert loaded == queued
        assert ready == (queued,)
        assert attempts == (attempt,)
        assert logs == (request_log,)
        assert artifacts == (artifact,)

        stale = task.transition(TaskStatus.CANCELED, now)
        async with factory() as uow:
            with pytest.raises(TaskOptimisticConcurrencyError):
                await uow.tasks.update(stale, expected_version=task.row_version)
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_attempt_completion_is_compare_and_swap(tmp_path: Path) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Attempt Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = (await context.provider_service.sync_models(provider.id))[0]
        now = context.clock.now()
        task = Task(
            id=context.ids.new(),
            provider_id=provider.id,
            model_id=model.id,
            status=TaskStatus.CREATED,
            operation=next(iter(model.operations)),
            input={},
            provider_config_snapshot={},
            normalized_output=None,
            remote_task_id=None,
            idempotency_key="attempt-cas-key",
            priority=100,
            progress=None,
            poll_after_at=None,
            timeout_at=None,
            cancel_requested_at=None,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        running = TaskAttempt(
            id=context.ids.new(),
            task_id=task.id,
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
        factory = SQLiteTaskUnitOfWorkFactory(context.database.sessions, context.events)
        async with factory() as uow:
            await uow.tasks.add(task)
            await uow.attempts.add(running)
            await uow.commit()

        completed = replace(running, status=AttemptStatus.SUCCESS, ended_at=now)
        async with factory() as uow:
            await uow.attempts.update(completed, expected_status=AttemptStatus.RUNNING)
            await uow.commit()
        async with factory() as uow:
            assert await uow.attempts.next_attempt_no(task.id) == 2
            with pytest.raises(TaskOptimisticConcurrencyError):
                await uow.attempts.update(completed, expected_status=AttemptStatus.RUNNING)
    finally:
        await context.close()
