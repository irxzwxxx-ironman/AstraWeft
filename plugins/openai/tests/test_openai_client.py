"""Offline OpenAI Models, Responses, error, and usage mapping tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from astraweft_openai_provider import OpenAIProviderPlugin
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
    SecretValue,
    UnsupportedOperationError,
)


class CanarySecrets:
    def __init__(self, value: str) -> None:
        self.value = value

    async def get(self, credential_ref: str, field: str) -> SecretValue:
        assert credential_ref == "credential-ref"
        assert field == "api_key"
        return SecretValue(self.value)


def _request(
    *,
    inputs: Mapping[str, object] | None = None,
    operation: str = "text.generate",
    trace_id: str = "trace-123",
    timeout_seconds: float = 90,
) -> ProviderRequest:
    return ProviderRequest(
        operation=operation,
        remote_model_id="gpt-5-mini",
        inputs={"prompt": "hello"} if inputs is None else inputs,
        idempotency_key="must-not-be-sent",
        trace_id=trace_id,
        timeout_seconds=timeout_seconds,
    )


def _client(
    context: ProviderContext,
    settings: Mapping[str, object] | None = None,
    credential_ref: str | None = "credential-ref",
) -> ProviderClient:
    return OpenAIProviderPlugin().create_client(context, settings or {}, credential_ref)


@pytest.mark.asyncio
async def test_health_and_model_discovery_use_conservative_text_filter(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    payload = {
        "object": "list",
        "data": [
            {"id": "gpt-5-mini"},
            {"id": "gpt-4.1"},
            {"id": "o3-mini"},
            {"id": "ft:gpt-4.1:team:custom"},
            {"id": "gpt-4o-mini-transcribe"},
            {"id": "gpt-5-codex"},
            {"id": "text-embedding-3-large"},
            {"id": "chatgpt-4o-latest"},
        ],
    }
    fake_http.queue(200, payload)
    fake_http.queue(200, payload)
    client = _client(provider_context)

    health = await client.health_check()
    models = await client.list_models()

    assert health.status == "healthy"
    assert health.latency_ms == 25
    assert [model.remote_model_id for model in models] == [
        "ft:gpt-4.1:team:custom",
        "gpt-4.1",
        "gpt-5-mini",
        "o3-mini",
    ]
    assert all(not model.pricing for model in models)


@pytest.mark.asyncio
async def test_submit_maps_request_output_usage_and_safe_call_metadata(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    fake_http.queue(
        200,
        {
            "id": "resp_123",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "first"},
                        {"type": "output_text", "text": "second"},
                    ],
                }
            ],
            "usage": {
                "input_tokens": 12,
                "input_tokens_details": {"cached_tokens": 4},
                "output_tokens": 8,
                "output_tokens_details": {"reasoning_tokens": 2},
                "total_tokens": 20,
            },
        },
        headers={"x-request-id": "req_123"},
    )
    client = _client(
        provider_context,
        {
            "organization": "org_test",
            "project": "proj_test",
            "request_timeout_seconds": 30,
        },
    )
    result = await client.submit(
        _request(
            inputs={
                "prompt": "hello",
                "instructions": "be concise",
                "max_output_tokens": 50,
            }
        )
    )

    assert result.output is not None
    assert result.output.data["text"] == "first\nsecond"
    assert result.output.finish_reason == "completed"
    assert result.output.usage is not None
    assert result.output.usage.units == {
        "input_tokens": 12,
        "cached_tokens": 4,
        "output_tokens": 8,
        "reasoning_tokens": 2,
        "total_tokens": 20,
    }
    assert result.output.usage.cost_micros is None
    assert result.provider_request_id == "req_123"
    assert result.call is not None
    assert (result.call.method, result.call.url_template, result.call.http_status) == (
        "POST",
        "/v1/responses",
        200,
    )

    sent = fake_http.requests[0]
    assert sent.url == "https://api.openai.com/v1/responses"
    assert sent.headers["Authorization"] == "Bearer sk-offline-test"
    assert sent.headers["OpenAI-Organization"] == "org_test"
    assert sent.headers["OpenAI-Project"] == "proj_test"
    assert sent.headers["X-Client-Request-Id"] == "astraweft-trace-123"
    assert sent.idempotency_key is None
    assert sent.timeout_seconds == 30
    assert sent.json_body == {
        "model": "gpt-5-mini",
        "input": "hello",
        "instructions": "be concise",
        "max_output_tokens": 50,
        "store": False,
    }


@pytest.mark.asyncio
async def test_incomplete_and_refusal_outputs_remain_user_visible(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    fake_http.queue(
        200,
        {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "partial"}]}
            ],
        },
    )
    fake_http.queue(
        200,
        {
            "status": "completed",
            "output": [
                {"type": "message", "content": [{"type": "refusal", "refusal": "Cannot help"}]}
            ],
        },
    )
    client = _client(provider_context)

    incomplete = await client.submit(_request())
    refusal = await client.submit(_request())

    assert incomplete.output is not None
    assert incomplete.output.finish_reason == "max_output_tokens"
    assert refusal.output is not None
    assert refusal.output.data["text"] == "Cannot help"
    assert refusal.output.finish_reason == "refusal"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, ProviderValidationError),
        (401, ProviderAuthenticationError),
        (403, ProviderPermissionError),
        (408, ProviderTimeoutError),
        (429, ProviderRateLimitError),
        (500, ProviderUnavailableError),
        (503, ProviderUnavailableError),
    ],
)
async def test_http_errors_are_normalized_without_remote_message_leak(
    provider_context: ProviderContext,
    fake_http: Any,
    status: int,
    expected: type[ProviderError],
) -> None:
    canary = "REMOTE_ERROR_SECRET_CANARY"
    fake_http.queue(
        status,
        {"error": {"message": canary, "type": "api_error", "code": "safe_code"}},
        headers={"x-request-id": "req_error", "retry-after": "1.5"},
    )
    client = _client(provider_context)

    with pytest.raises(expected) as captured:
        await client.list_models()

    assert canary not in str(captured.value)
    assert canary not in repr(captured.value)
    error = captured.value
    assert error.provider_request_id == "req_error"
    assert error.safe_details["provider_error_code"] == "safe_code"
    if isinstance(error, ProviderRateLimitError):
        assert error.retry_after_seconds == 1.5


@pytest.mark.asyncio
async def test_protocol_failures_and_unsupported_operations_are_explicit(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    client = _client(provider_context)
    fake_http.queue(200, b"not-json")
    with pytest.raises(ProviderProtocolError, match="JSON"):
        await client.list_models()

    fake_http.queue(200, {"status": "completed", "output": []})
    with pytest.raises(ProviderProtocolError, match="文本输出"):
        await client.submit(_request())

    with pytest.raises(UnsupportedOperationError):
        await client.submit(_request(operation="image.generate"))
    with pytest.raises(UnsupportedOperationError):
        await client.get_task("remote")
    with pytest.raises(UnsupportedOperationError):
        await client.cancel_task("remote")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"prompt": " "},
        {"prompt": "hello", "instructions": ""},
        {"prompt": "hello", "max_output_tokens": True},
        {"prompt": "hello", "max_output_tokens": 0},
        {"prompt": "hello", "temperature": 0.5},
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
async def test_missing_credentials_settings_and_close_are_safe(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    with pytest.raises(ProviderAuthenticationError):
        await _client(provider_context, credential_ref=None).health_check()
    with pytest.raises(PluginConfigurationError):
        await _client(provider_context, {"request_timeout_seconds": 0}).health_check()

    client = _client(provider_context)
    await client.close()
    await client.close()
    with pytest.raises(ProviderProtocolError, match="closed"):
        await client.list_models()
    assert fake_http.requests == []


@pytest.mark.asyncio
async def test_secret_and_untrusted_request_ids_do_not_leak(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    canary = "OPENAI_SECRET_CANARY"
    provider_context = ProviderContext(
        http=provider_context.http,
        secrets=CanarySecrets(canary),
        logger=provider_context.logger,
        clock=provider_context.clock,
        plugin_data=provider_context.plugin_data,
        core_version=provider_context.core_version,
        plugin_api_version=provider_context.plugin_api_version,
    )
    fake_http.queue(
        401,
        {"error": {"message": canary}},
        headers={"x-request-id": "不安全请求号"},
    )

    with pytest.raises(ProviderAuthenticationError) as captured:
        await _client(provider_context).health_check()

    assert captured.value.provider_request_id is None
    assert canary not in str(captured.value)
    assert canary not in repr(captured.value)


@pytest.mark.asyncio
async def test_non_ascii_trace_id_is_hashed_for_client_request_header(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    fake_http.queue(
        200,
        {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
        },
    )
    await _client(provider_context).submit(_request(trace_id="追踪-一"))

    request_id = fake_http.requests[0].headers["X-Client-Request-Id"]
    assert request_id.startswith("astraweft-")
    assert request_id.isascii()


@pytest.mark.asyncio
async def test_insufficient_quota_is_not_scheduled_for_automatic_retry(
    provider_context: ProviderContext,
    fake_http: Any,
) -> None:
    fake_http.queue(
        429,
        {"error": {"type": "insufficient_quota", "code": "insufficient_quota"}},
    )

    with pytest.raises(ProviderRateLimitError) as captured:
        await _client(provider_context).list_models()

    assert captured.value.retryable is False
