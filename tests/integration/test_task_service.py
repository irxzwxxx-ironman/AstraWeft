"""End-to-end durable task runtime tests through the public Mock Provider."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from astraweft.application.providers import CreateProvider, UpdateProvider
from astraweft.application.tasks import (
    CreateTask,
    TaskExecutionError,
    TaskInputError,
    TaskNotFoundError,
)
from astraweft.bootstrap.container import build_app_context
from astraweft.domain.task import AttemptPhase, AttemptStatus, TaskStatus
from astraweft.infrastructure.network import CoreHttpClient
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.secrets import SecretValue


@pytest.mark.integration
@pytest.mark.asyncio
async def test_explicit_task_identity_is_idempotent_and_rejects_reuse(tmp_path: Path) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Intent Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = next(
            item
            for item in await context.provider_service.sync_models(provider.id)
            if item.remote_model_id == "mock-text-v1"
        )
        command = CreateTask(
            provider_id=provider.id,
            model_id=model.id,
            operation="text.generate",
            inputs={"prompt": "stable intent"},
            task_id="planned-workflow-task",
        )

        first = await context.task_service.create(command)
        second = await context.task_service.create(command)

        assert first == second
        assert first.id == "planned-workflow-task"
        assert len(await context.task_service.list_tasks()) == 1
        with pytest.raises(TaskInputError, match="不同"):
            await context.task_service.create(
                CreateTask(
                    provider_id=provider.id,
                    model_id=model.id,
                    operation="text.generate",
                    inputs={"prompt": "changed intent"},
                    task_id=first.id,
                )
            )
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_offline_flow_persists_safe_http_metadata_without_paid_call(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                headers={"x-request-id": "req_models"},
                json={"object": "list", "data": [{"id": "gpt-5-mini"}]},
            )
        assert request.url.path == "/v1/responses"
        return httpx.Response(
            200,
            headers={"x-request-id": "req_response"},
            json={
                "id": "resp_offline",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "offline response"}],
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            },
        )

    http_client = CoreHttpClient(
        user_agent="AstraWeft/integration-test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    context = await build_app_context(
        tmp_path,
        secret_store_override=SessionSecretStore(),
        http_client_override=http_client,
    )
    try:
        assert requests == []
        assert any(
            record.descriptor is not None
            and record.descriptor.plugin_id == "com.openai.api-provider"
            for record in context.provider_service.plugin_records()
        )
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="com.openai.api-provider",
                name="Offline OpenAI",
                settings={"request_timeout_seconds": 30},
                credentials={"api_key": SecretValue("OPENAI_INTEGRATION_SECRET")},
                endpoint="https://api.openai.com/v1",
            )
        )
        model = (await context.provider_service.sync_models(provider.id))[0]
        task = await context.task_service.create_and_run(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="text.generate",
                inputs={"prompt": "PRIVATE_PROMPT_CANARY"},
            )
        )
        logs = await context.task_service.list_request_logs()

        assert task.status is TaskStatus.SUCCESS
        assert task.normalized_output is not None
        assert task.normalized_output["data"] == {"text": "offline response"}
        assert len(logs) == 1
        assert (logs[0].method, logs[0].url_template, logs[0].http_status) == (
            "POST",
            "/v1/responses",
            200,
        )
        assert logs[0].usage == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
        assert logs[0].amount_micros is None
        assert "PRIVATE_PROMPT_CANARY" not in repr(logs[0])
        assert "OPENAI_INTEGRATION_SECRET" not in repr(logs[0])

        submitted = requests[-1]
        body = json.loads(submitted.content)
        assert body["store"] is False
        assert submitted.headers["authorization"] == "Bearer OPENAI_INTEGRATION_SECRET"
        assert submitted.headers["user-agent"] == "AstraWeft/integration-test"
        assert "idempotency-key" not in submitted.headers

        uncertain = await context.task_service.create(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="text.generate",
                inputs={"prompt": "do not submit twice"},
            )
        )
        submitting, _attempt = await context.task_service._begin_attempt(
            uncertain.id,
            AttemptPhase.SUBMIT,
        )
        request_count = len(requests)
        recovered = await context.task_service.recover_pending()
        recovered_uncertain = next(item for item in recovered if item.id == uncertain.id)
        attempts = await context.task_service.list_attempts(uncertain.id)

        assert submitting.status is TaskStatus.SUBMITTING
        assert recovered_uncertain.status is TaskStatus.NEEDS_ATTENTION
        assert len(requests) == request_count
        assert attempts[0].status is AttemptStatus.FAILED
        assert attempts[0].error_code == "process_interrupted"
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runway_offline_async_flow_download_recovery_cancel_and_submit_safety(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    poll_counts: dict[str, int] = {}
    video_payload = b"offline-runway-video"

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "deliverable.cloudfront.net":
            return httpx.Response(
                200,
                headers={
                    "content-type": "video/mp4",
                    "content-length": str(len(video_payload)),
                },
                content=video_payload,
            )
        if request.method == "POST" and request.url.path == "/v1/text_to_video":
            body = json.loads(request.content)
            prompt = body["promptText"]
            remote_id = {
                "RUNWAY_LIVE_PROMPT_CANARY": "task_live",
                "RUNWAY_RECOVER_PROMPT_CANARY": "task_recover",
                "RUNWAY_CANCEL_PROMPT_CANARY": "task_cancel",
            }[prompt]
            return httpx.Response(
                200,
                headers={"x-request-id": f"req_submit_{remote_id}"},
                json={"id": remote_id},
            )
        if request.method == "GET" and request.url.path.startswith("/v1/tasks/"):
            remote_id = request.url.path.rsplit("/", 1)[-1]
            poll_counts[remote_id] = poll_counts.get(remote_id, 0) + 1
            if remote_id == "task_live" and poll_counts[remote_id] == 1:
                return httpx.Response(
                    200,
                    headers={"x-request-id": "req_poll_running"},
                    json={"id": remote_id, "status": "RUNNING", "progress": 0.5},
                )
            return httpx.Response(
                200,
                headers={"x-request-id": f"req_poll_{remote_id}"},
                json={
                    "id": remote_id,
                    "status": "SUCCEEDED",
                    "output": ["https://deliverable.cloudfront.net/runway.mp4?token=SIGNED_SECRET"],
                },
            )
        if request.method == "DELETE" and request.url.path == "/v1/tasks/task_cancel":
            return httpx.Response(204, headers={"x-request-id": "req_cancel"})
        raise AssertionError(f"unexpected offline request: {request.method} {request.url}")

    http_client = CoreHttpClient(
        user_agent="AstraWeft/integration-test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    context = await build_app_context(
        tmp_path,
        secret_store_override=SessionSecretStore(),
        http_client_override=http_client,
    )
    try:
        assert any(
            record.descriptor is not None
            and record.descriptor.plugin_id == "com.runwayml.api-provider"
            for record in context.provider_service.plugin_records()
        )
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="com.runwayml.api-provider",
                name="Offline Runway",
                settings={"request_timeout_seconds": 30, "poll_interval_seconds": 5},
                credentials={"api_key": SecretValue("RUNWAY_INTEGRATION_SECRET")},
                endpoint="https://api.dev.runwayml.com/v1",
            )
        )
        model = (await context.provider_service.sync_models(provider.id))[0]

        live = await context.task_service.create(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="video.generate",
                inputs={
                    "prompt": "RUNWAY_LIVE_PROMPT_CANARY",
                    "duration": 5,
                    "ratio": "1280:720",
                },
            )
        )
        live = await context.task_service.run_once(live.id)
        live = await context.task_service.run_once(live.id)
        live = await context.task_service.run_once(live.id)
        live_logs = await context.task_service.list_request_logs()
        live_artifacts = await context.task_service.list_artifacts(live.id)

        assert live.status is TaskStatus.SUCCESS
        assert live.remote_task_id == "task_live"
        assert len(live_artifacts) == 1
        artifact = live_artifacts[0]
        assert (context.paths.artifact_dir / artifact.relative_path).read_bytes() == video_payload
        assert artifact.sha256 == hashlib.sha256(video_payload).hexdigest()
        assert artifact.source_url_redacted == "https://deliverable.cloudfront.net/<redacted>"
        assert "SIGNED_SECRET" not in repr(artifact)
        assert [(item.method, item.url_template, item.http_status) for item in live_logs] == [
            ("GET", "/v1/tasks/{task_id}", 200),
            ("GET", "/v1/tasks/{task_id}", 200),
            ("POST", "/v1/text_to_video", 200),
        ]
        assert "RUNWAY_LIVE_PROMPT_CANARY" not in repr(live_logs)
        assert "RUNWAY_INTEGRATION_SECRET" not in repr(live_logs)

        recovering = await context.task_service.create(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="video.generate",
                inputs={
                    "prompt": "RUNWAY_RECOVER_PROMPT_CANARY",
                    "duration": 5,
                    "ratio": "1280:720",
                },
            )
        )
        recovering = await context.task_service.run_once(recovering.id)
        assert recovering.status is TaskStatus.POLLING
        async with context.database.engine.begin() as connection:
            await connection.execute(
                text("UPDATE tasks SET poll_after_at = :past WHERE id = :task_id"),
                {"past": "2000-01-01T00:00:00+00:00", "task_id": recovering.id},
            )
        recovered = await context.task_service.recover_pending()
        recovered_task = next(item for item in recovered if item.id == recovering.id)
        recovered_attempts = await context.task_service.list_attempts(recovering.id)

        assert recovered_task.status is TaskStatus.SUCCESS
        assert sum(item.phase is AttemptPhase.SUBMIT for item in recovered_attempts) == 1
        assert sum(item.phase is AttemptPhase.POLL for item in recovered_attempts) == 1
        assert poll_counts["task_recover"] == 1

        canceling = await context.task_service.create(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="video.generate",
                inputs={
                    "prompt": "RUNWAY_CANCEL_PROMPT_CANARY",
                    "duration": 5,
                    "ratio": "1280:720",
                },
            )
        )
        canceling = await context.task_service.run_once(canceling.id)
        canceled = await context.task_service.cancel(canceling.id)

        assert canceled.status is TaskStatus.CANCELED
        assert any(
            request.method == "DELETE" and request.url.path == "/v1/tasks/task_cancel"
            for request in requests
        )

        uncertain = await context.task_service.create(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="video.generate",
                inputs={
                    "prompt": "DO_NOT_SUBMIT_RUNWAY_TWICE",
                    "duration": 5,
                    "ratio": "1280:720",
                },
            )
        )
        uncertain, _attempt = await context.task_service._begin_attempt(
            uncertain.id,
            AttemptPhase.SUBMIT,
        )
        request_count = len(requests)
        recovered = await context.task_service.recover_pending()
        recovered_uncertain = next(item for item in recovered if item.id == uncertain.id)

        assert uncertain.status is TaskStatus.SUBMITTING
        assert recovered_uncertain.status is TaskStatus.NEEDS_ATTENTION
        assert len(requests) == request_count
        assert not tuple(context.paths.artifact_dir.rglob("*.partial"))
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_mock_task_persists_result_usage_and_redacted_log(
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Sync Runtime",
                settings={"response_mode": "completed"},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        models = await context.provider_service.sync_models(provider.id)
        model = next(item for item in models if item.remote_model_id == "mock-text-v1")

        task = await context.task_service.create_and_run(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="text.generate",
                inputs={"prompt": "never persist this in request logs"},
            )
        )
        attempts = await context.task_service.list_attempts(task.id)
        logs = await context.task_service.list_request_logs()

        assert task.status is TaskStatus.SUCCESS
        assert task.normalized_output is not None
        assert task.normalized_output["data"] == {"text": "Mock response"}
        assert [(item.phase, item.status) for item in attempts] == [
            (AttemptPhase.SUBMIT, AttemptStatus.SUCCESS)
        ]
        assert logs[0].amount_micros == 1_000
        assert logs[0].currency == "USD"
        assert logs[0].request_summary["field_names"] == ("prompt",)
        assert "never persist" not in repr(logs[0])
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_async_mock_task_polls_and_materializes_verified_artifact(
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Async Runtime",
                settings={"response_mode": "accepted", "catalog_revision": 2},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        models = await context.provider_service.sync_models(provider.id)
        model = next(item for item in models if item.remote_model_id == "mock-video-v1")

        task = await context.task_service.create_and_run(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="video.generate",
                inputs={"prompt": "a local deterministic scene"},
            )
        )
        attempts = await context.task_service.list_attempts(task.id)
        artifacts = await context.task_service.list_artifacts(task.id)
        logs = await context.task_service.list_request_logs()

        assert task.status is TaskStatus.SUCCESS
        assert task.remote_task_id is not None
        assert [item.phase for item in attempts] == [
            AttemptPhase.SUBMIT,
            AttemptPhase.POLL,
            AttemptPhase.POLL,
        ]
        assert all(item.status is AttemptStatus.SUCCESS for item in attempts)
        assert len(logs) == 3
        assert len(artifacts) == 1
        artifact = artifacts[0]
        artifact_path = context.paths.artifact_dir / artifact.relative_path
        payload = artifact_path.read_bytes()
        assert payload == b"mock-video-artifact"
        assert artifact.sha256 == hashlib.sha256(payload).hexdigest()
        assert not tuple(context.paths.artifact_dir.rglob("*.partial"))
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_task_input_is_rejected_before_task_persistence(
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Validation Runtime",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = next(
            item
            for item in await context.provider_service.sync_models(provider.id)
            if item.remote_model_id == "mock-text-v1"
        )
        with pytest.raises(TaskInputError, match="任务参数无效"):
            await context.task_service.create(
                CreateTask(
                    provider_id=provider.id,
                    model_id=model.id,
                    operation="text.generate",
                    inputs={"prompt": ""},
                )
            )
        assert await context.task_service.list_tasks() == ()
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_restart_recovers_remote_task_without_duplicate_submit(
    tmp_path: Path,
) -> None:
    secrets = SessionSecretStore()
    first = await build_app_context(tmp_path, secret_store_override=secrets)
    provider = await first.provider_service.create(
        CreateProvider(
            plugin_id="dev.astraweft.mock-provider",
            name="Restart Runtime",
            settings={"response_mode": "accepted", "catalog_revision": 2},
            credentials={"api_key": SecretValue("mock-valid-key")},
        )
    )
    model = next(
        item
        for item in await first.provider_service.sync_models(provider.id)
        if item.remote_model_id == "mock-video-v1"
    )
    created = await first.task_service.create(
        CreateTask(
            provider_id=provider.id,
            model_id=model.id,
            operation="video.generate",
            inputs={"prompt": "survive restart"},
        )
    )
    pending = await first.task_service.run_once(created.id)
    assert pending.status is TaskStatus.POLLING
    assert pending.remote_task_id is not None
    await first.close()

    second = await build_app_context(tmp_path, secret_store_override=secrets)
    try:
        recovered = await second.task_service.recover_pending()
        attempts = await second.task_service.list_attempts(created.id)

        assert len(recovered) == 1
        assert recovered[0].status is TaskStatus.SUCCESS
        assert sum(item.phase is AttemptPhase.SUBMIT for item in attempts) == 1
        assert sum(item.phase is AttemptPhase.POLL for item in attempts) == 2
    finally:
        await second.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_remote_cancel_persists_intent_before_provider_call(tmp_path: Path) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Cancel Runtime",
                settings={"response_mode": "accepted", "catalog_revision": 2},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = next(
            item
            for item in await context.provider_service.sync_models(provider.id)
            if item.remote_model_id == "mock-video-v1"
        )
        created = await context.task_service.create(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="video.generate",
                inputs={"prompt": "cancel me"},
            )
        )
        pending = await context.task_service.run_once(created.id)
        canceled = await context.task_service.cancel(pending.id)
        attempts = await context.task_service.list_attempts(canceled.id)

        assert canceled.status is TaskStatus.CANCELED
        assert canceled.cancel_requested_at is not None
        assert [item.phase for item in attempts] == [
            AttemptPhase.SUBMIT,
            AttemptPhase.CANCEL,
        ]
        assert all(item.status is AttemptStatus.SUCCESS for item in attempts)
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_background_runtime_recovers_then_dispatches_new_priority_work(
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Background Runtime",
                settings={"response_mode": "completed"},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = next(
            item
            for item in await context.provider_service.sync_models(provider.id)
            if item.remote_model_id == "mock-text-v1"
        )
        recovered_task = await context.task_service.create(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="text.generate",
                inputs={"prompt": "recover on runtime start"},
                priority=10,
            )
        )
        context.task_runtime.start()
        for _ in range(100):
            if (await context.task_service.get(recovered_task.id)).status.terminal:
                break
            await asyncio.sleep(0.01)
        assert (await context.task_service.get(recovered_task.id)).status is TaskStatus.SUCCESS

        dispatched = await context.task_service.create(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="text.generate",
                inputs={"prompt": "dispatch after wake"},
                priority=5,
            )
        )
        context.task_runtime.wake()
        for _ in range(100):
            if (await context.task_service.get(dispatched.id)).status.terminal:
                break
            await asyncio.sleep(0.01)
        assert (await context.task_service.get(dispatched.id)).status is TaskStatus.SUCCESS
        assert context.task_runtime.running
        await context.task_runtime.stop()
        assert not context.task_runtime.running
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retryable_provider_failure_reuses_task_idempotency_and_stops_at_limit(
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Retry Runtime",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = next(
            item
            for item in await context.provider_service.sync_models(provider.id)
            if item.remote_model_id == "mock-text-v1"
        )
        await context.provider_service.update(
            UpdateProvider(
                provider_id=provider.id,
                name=provider.name,
                settings={"mode": "rate_limit"},
                endpoint=None,
                enabled=True,
            )
        )
        context.task_service._max_attempts = 2
        task = await context.task_service.create(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="text.generate",
                inputs={"prompt": "retry safely"},
            )
        )
        waiting = await context.task_service.run_once(task.id)
        failed = await context.task_service.run_once(task.id)
        attempts = await context.task_service.list_attempts(task.id)
        logs = await context.task_service.list_request_logs()

        assert waiting.status is TaskStatus.RETRY_WAIT
        assert failed.status is TaskStatus.FAILED
        assert task.idempotency_key == failed.idempotency_key
        assert len(attempts) == 2
        assert all(attempt.error_code == "rate_limit" for attempt in attempts)
        assert all(log.amount_micros is None for log in logs)
        assert all(log.error_code == "rate_limit" for log in logs)
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_uncertain_submit_closes_interrupted_attempt_before_safe_resubmit(
    tmp_path: Path,
) -> None:
    secrets = SessionSecretStore()
    first = await build_app_context(tmp_path, secret_store_override=secrets)
    provider = await first.provider_service.create(
        CreateProvider(
            plugin_id="dev.astraweft.mock-provider",
            name="Uncertain Runtime",
            settings={"response_mode": "completed"},
            credentials={"api_key": SecretValue("mock-valid-key")},
        )
    )
    model = next(
        item
        for item in await first.provider_service.sync_models(provider.id)
        if item.remote_model_id == "mock-text-v1"
    )
    task = await first.task_service.create(
        CreateTask(
            provider_id=provider.id,
            model_id=model.id,
            operation="text.generate",
            inputs={"prompt": "safe resubmit"},
        )
    )
    submitting, interrupted = await first.task_service._begin_attempt(task.id, AttemptPhase.SUBMIT)
    assert submitting.status is TaskStatus.SUBMITTING
    assert interrupted.status is AttemptStatus.RUNNING
    await first.close()

    second = await build_app_context(tmp_path, secret_store_override=secrets)
    try:
        recovered = await second.task_service.recover_pending()
        attempts = await second.task_service.list_attempts(task.id)

        assert recovered[0].status is TaskStatus.SUCCESS
        assert attempts[0].status is AttemptStatus.FAILED
        assert attempts[0].error_code == "process_interrupted"
        assert attempts[1].status is AttemptStatus.SUCCESS
        assert attempts[0].task_id == attempts[1].task_id == task.id
    finally:
        await second.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_recovery_marks_remote_task_without_identity_for_manual_attention(
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Attention Runtime",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = next(
            item
            for item in await context.provider_service.sync_models(provider.id)
            if item.remote_model_id == "mock-text-v1"
        )
        task = await context.task_service.create(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="text.generate",
                inputs={"prompt": "malformed remote state"},
            )
        )
        async with context.database.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE tasks SET status = 'POLLING', row_version = row_version + 1 "
                    "WHERE id = :task_id"
                ),
                {"task_id": task.id},
            )
        recovered_task = await context.task_service.run_once(task.id)
        logs = await context.task_service.list_request_logs()

        assert recovered_task.status is TaskStatus.NEEDS_ATTENTION
        assert logs[0].error_code == "remote_identity_missing"
        assert logs[0].response_summary["failed"] is True
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_local_cancel_timeout_and_invalid_runtime_requests_are_explicit(
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Local State Runtime",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = next(
            item
            for item in await context.provider_service.sync_models(provider.id)
            if item.remote_model_id == "mock-text-v1"
        )
        queued = await context.task_service.create(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="text.generate",
                inputs={"prompt": "cancel locally"},
            )
        )
        canceled = await context.task_service.cancel(queued.id)
        assert canceled.status is TaskStatus.CANCELED
        assert await context.task_service.cancel(canceled.id) == canceled
        assert await context.task_service.run_once(canceled.id) == canceled

        expiring = await context.task_service.create(
            CreateTask(
                provider_id=provider.id,
                model_id=model.id,
                operation="text.generate",
                inputs={"prompt": "expire locally"},
            )
        )
        async with context.database.engine.begin() as connection:
            await connection.execute(
                text("UPDATE tasks SET timeout_at = :past WHERE id = :task_id"),
                {"past": "2000-01-01T00:00:00+00:00", "task_id": expiring.id},
            )
        assert (await context.task_service.run_once(expiring.id)).status is TaskStatus.TIMED_OUT

        with pytest.raises(TaskNotFoundError):
            await context.task_service.get("missing-task")
        with pytest.raises(TaskExecutionError):
            await context.task_service._begin_attempt(canceled.id, AttemptPhase.SUBMIT)
        with pytest.raises(ValueError, match="max_cycles"):
            await context.task_service.run_until_terminal(canceled.id, max_cycles=0)
    finally:
        await context.close()
