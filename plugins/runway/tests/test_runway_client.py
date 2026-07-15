"""Offline Runway submit, poll, cancel, error, and privacy mapping tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from astraweft_provider_sdk import (
    PluginConfigurationError,
    ProviderAuthenticationError,
    ProviderClient,
    ProviderContext,
    ProviderError,
    ProviderPermissionError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderRequest,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    UnsupportedOperationError,
)
from astraweft_runway_provider import RunwayProviderPlugin


def _request(
    *,
    inputs: Mapping[str, object] | None = None,
    operation: str = "video.generate",
    model: str = "gen4.5",
) -> ProviderRequest:
    return ProviderRequest(
        operation=operation,
        remote_model_id=model,
        inputs={"prompt": "a quiet ocean", "duration": 5, "ratio": "1280:720"}
        if inputs is None
        else inputs,
        idempotency_key="must-not-be-sent",
        trace_id="trace-runway",
        timeout_seconds=90,
    )


def _client(
    context: ProviderContext,
    settings: Mapping[str, object] | None = None,
    credential_ref: str | None = "credential-ref",
) -> ProviderClient:
    return RunwayProviderPlugin().create_client(context, settings or {}, credential_ref)


@pytest.mark.asyncio
async def test_health_and_static_model_catalog(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    fake_http.queue(200, {"creditBalance": 100, "tier": {}, "usage": {}})
    client = _client(provider_context)

    health = await client.health_check()
    models = await client.list_models()

    assert health.status == "healthy"
    assert health.latency_ms in {24, 25}
    assert health.details == {"endpoint": "api.dev.runwayml.com"}
    assert [model.remote_model_id for model in models] == ["gen4.5"]
    assert models[0].pricing == ()


@pytest.mark.asyncio
async def test_submit_maps_versioned_request_and_safe_call_metadata(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    fake_http.queue(200, {"id": "task_123"}, headers={"x-request-id": "req_submit"})
    client = _client(
        provider_context,
        {"request_timeout_seconds": 30, "poll_interval_seconds": 7},
    )
    result = await client.submit(
        _request(
            inputs={
                "prompt": "a quiet ocean",
                "duration": 6,
                "ratio": "720:1280",
                "seed": 42,
            }
        )
    )

    assert result.mode == "accepted"
    assert result.remote_task_id == "task_123"
    assert result.progress == 0
    assert result.poll_after_seconds == 7
    assert result.provider_request_id == "req_submit"
    assert result.call is not None
    assert (result.call.method, result.call.url_template, result.call.http_status) == (
        "POST",
        "/v1/text_to_video",
        200,
    )
    sent = fake_http.requests[0]
    assert sent.url == "https://api.dev.runwayml.com/v1/text_to_video"
    assert sent.headers["Authorization"] == "Bearer key_offline_runway"
    assert sent.headers["X-Runway-Version"] == "2024-11-06"
    assert sent.idempotency_key is None
    assert sent.timeout_seconds == 30
    assert sent.trace_id == "trace-runway"
    assert sent.json_body == {
        "model": "gen4.5",
        "promptText": "a quiet ocean",
        "duration": 6,
        "ratio": "720:1280",
        "seed": 42,
    }


@pytest.mark.asyncio
async def test_poll_maps_all_nonterminal_and_canceled_states(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    fake_http.queue(200, {"id": "task_1", "status": "PENDING"})
    fake_http.queue(200, {"id": "task_1", "status": "THROTTLED"})
    fake_http.queue(200, {"id": "task_1", "status": "RUNNING", "progress": 0.376})
    fake_http.queue(200, {"id": "task_1", "status": "CANCELLED"})
    client = _client(provider_context)

    pending = await client.get_task("task_1")
    throttled = await client.get_task("task_1")
    running = await client.get_task("task_1")
    canceled = await client.get_task("task_1")

    assert (pending.state, pending.progress, pending.poll_after_seconds) == ("queued", 0, 5)
    assert throttled.state == "queued"
    assert (running.state, running.progress) == ("running", 38)
    assert canceled.state == "canceled"
    assert all(request.url.endswith("/v1/tasks/task_1") for request in fake_http.requests)
    assert running.call is not None
    assert running.call.url_template == "/v1/tasks/{task_id}"


@pytest.mark.asyncio
async def test_succeeded_task_returns_ephemeral_video_artifacts(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    first = "https://assets-a.cloudfront.net/video.mp4?token=SECRET_ONE"
    second = "https://assets-b.cloudfront.net/video.mp4?token=SECRET_TWO"
    fake_http.queue(
        200,
        {"id": "task_1", "status": "SUCCEEDED", "output": [first, second]},
        headers={"x-request-id": "req_poll"},
    )

    snapshot = await _client(provider_context).get_task("task_1")

    assert snapshot.state == "succeeded"
    assert snapshot.progress == 100
    assert snapshot.output is not None
    assert snapshot.output.data == {"video_count": 2}
    assert [artifact.value for artifact in snapshot.output.artifacts] == [first, second]
    assert all(artifact.source == "url" for artifact in snapshot.output.artifacts)
    assert all(artifact.mime_type == "video/mp4" for artifact in snapshot.output.artifacts)
    assert snapshot.call is not None
    assert snapshot.call.provider_request_id == "req_poll"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_code", "retryable"),
    [
        ("SAFETY", False),
        ("ASSET.INVALID", False),
        ("INPUT_PREPROCESSING.INTERNAL", True),
        ("THIRD_PARTY.UNAVAILABLE", True),
        ("INTERNAL", True),
        (None, True),
        ("UNKNOWN", False),
    ],
)
async def test_failed_task_maps_safe_retry_policy(
    provider_context: ProviderContext,
    fake_http: Any,
    failure_code: str | None,
    retryable: bool,
) -> None:
    payload: dict[str, object] = {
        "id": "task_1",
        "status": "FAILED",
        "failure": "REMOTE_FAILURE_SECRET_CANARY",
    }
    if failure_code is not None:
        payload["failureCode"] = failure_code
    fake_http.queue(200, payload)

    snapshot = await _client(provider_context).get_task("task_1")

    assert snapshot.error is not None
    assert snapshot.error.retryable is retryable
    assert "REMOTE_FAILURE_SECRET_CANARY" not in repr(snapshot.error)


@pytest.mark.asyncio
async def test_cancel_uses_delete_and_marks_terminal(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    fake_http.queue(204, b"", headers={"x-request-id": "req_cancel"})

    result = await _client(provider_context).cancel_task("task_123")

    assert result.accepted is True
    assert result.terminal is True
    assert result.call is not None
    assert result.call.url_template == "/v1/tasks/{task_id}"
    assert fake_http.requests[0].method == "DELETE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, ProviderValidationError),
        (401, ProviderAuthenticationError),
        (403, ProviderPermissionError),
        (408, ProviderTimeoutError),
        (429, ProviderRateLimitError),
        (502, ProviderUnavailableError),
        (503, ProviderUnavailableError),
        (504, ProviderUnavailableError),
        (599, ProviderProtocolError),
    ],
)
async def test_http_errors_are_normalized_without_remote_message_leak(
    provider_context: ProviderContext,
    fake_http: Any,
    status: int,
    expected: type[ProviderError],
) -> None:
    canary = "RUNWAY_REMOTE_SECRET_CANARY"
    fake_http.queue(
        status,
        {"error": {"message": canary, "code": "safe_code"}},
        headers={"x-request-id": "req_error", "retry-after": "2.5"},
    )

    with pytest.raises(expected) as captured:
        await _client(provider_context).health_check()

    assert canary not in str(captured.value)
    assert canary not in repr(captured.value)
    assert captured.value.provider_request_id == "req_error"
    if isinstance(captured.value, ProviderRateLimitError):
        assert captured.value.retry_after_seconds == 2.5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"prompt": " ", "duration": 5, "ratio": "1280:720"},
        {"prompt": "x" * 1001, "duration": 5, "ratio": "1280:720"},
        {"prompt": "x", "duration": True, "ratio": "1280:720"},
        {"prompt": "x", "duration": 1, "ratio": "1280:720"},
        {"prompt": "x", "duration": 5, "ratio": "1:1"},
        {"prompt": "x", "duration": 5, "ratio": "1280:720", "seed": -1},
        {"prompt": "x", "duration": 5, "ratio": "1280:720", "unknown": 1},
    ],
)
async def test_submit_validates_parameters_before_network(
    provider_context: ProviderContext,
    fake_http: Any,
    inputs: Mapping[str, object],
) -> None:
    with pytest.raises(ProviderValidationError):
        await _client(provider_context).submit(_request(inputs=inputs))
    assert fake_http.requests == []


@pytest.mark.asyncio
async def test_protocol_configuration_identity_and_close_fail_safely(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    fake_http.queue(200, b"not-json")
    with pytest.raises(ProviderProtocolError, match="JSON"):
        await _client(provider_context).health_check()

    fake_http.queue(200, {"status": "RUNNING", "progress": 2})
    with pytest.raises(ProviderProtocolError, match="进度"):
        await _client(provider_context).get_task("task_1")

    fake_http.queue(200, {"status": "MYSTERY"})
    with pytest.raises(ProviderProtocolError, match="状态"):
        await _client(provider_context).get_task("task_1")

    with pytest.raises(ProviderValidationError, match="任务标识"):
        await _client(provider_context).get_task("../escape")
    with pytest.raises(UnsupportedOperationError):
        await _client(provider_context).submit(_request(operation="image.generate"))
    with pytest.raises(ProviderValidationError, match=r"gen4\.5"):
        await _client(provider_context).submit(_request(model="other"))
    with pytest.raises(ProviderAuthenticationError):
        await _client(provider_context, credential_ref=None).health_check()
    with pytest.raises(PluginConfigurationError):
        await _client(provider_context, {"poll_interval_seconds": 1}).submit(_request())

    client = _client(provider_context)
    await client.close()
    await client.close()
    with pytest.raises(ProviderProtocolError, match="closed"):
        await client.list_models()
