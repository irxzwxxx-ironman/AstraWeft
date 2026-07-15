"""Mock Provider success, async, and fault-mode tests."""

from __future__ import annotations

import pytest

from astraweft_mock_provider import MockProviderPlugin
from astraweft_provider_sdk import (
    ProviderAuthenticationError,
    ProviderContext,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderRequest,
    ProviderTaskFailedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    SecretValue,
)


class CanarySecrets:
    def __init__(self, value: str) -> None:
        self.value = value

    async def get(self, credential_ref: str, field: str) -> SecretValue:
        assert credential_ref
        assert field == "api_key"
        return SecretValue(self.value)


def _request(operation: str = "text.generate") -> ProviderRequest:
    return ProviderRequest(
        operation=operation,
        remote_model_id="mock-text-v1",
        inputs={"prompt": "hello"},
        idempotency_key="stable-key",
        trace_id="trace-1",
        timeout_seconds=10,
    )


@pytest.mark.asyncio
async def test_sync_submit_returns_normalized_output(provider_context: ProviderContext) -> None:
    client = MockProviderPlugin().create_client(
        provider_context,
        {"mode": "healthy", "response_mode": "completed"},
        "credential-ref",
    )
    health = await client.health_check()
    result = await client.submit(_request())
    await client.close()
    await client.close()

    assert health.status == "healthy"
    assert result.mode == "completed"
    assert result.output is not None
    assert result.output.data["text"] == "Mock response"
    assert result.output.usage is not None
    assert result.output.usage.cost_micros == 1_000
    with pytest.raises(ProviderProtocolError):
        await client.health_check()


@pytest.mark.asyncio
async def test_async_submit_poll_and_cancel_are_deterministic(
    provider_context: ProviderContext,
) -> None:
    client = MockProviderPlugin().create_client(
        provider_context,
        {"response_mode": "accepted"},
        "credential-ref",
    )
    first = await client.submit(_request("video.generate"))
    duplicate = await client.submit(_request("video.generate"))
    assert first.remote_task_id == duplicate.remote_task_id
    assert first.remote_task_id is not None

    running = await client.get_task(first.remote_task_id)
    succeeded = await client.get_task(first.remote_task_id)
    assert (running.state, running.progress) == ("running", 50)
    assert succeeded.state == "succeeded"
    assert succeeded.output is not None

    canceled_submission = await client.submit(
        ProviderRequest(
            operation="video.generate",
            remote_model_id="mock-video-v1",
            inputs={"prompt": "cancel"},
            idempotency_key="cancel-key",
            trace_id="trace-2",
            timeout_seconds=10,
        )
    )
    assert canceled_submission.remote_task_id is not None
    canceled = await client.cancel_task(canceled_submission.remote_task_id)
    snapshot = await client.get_task(canceled_submission.remote_task_id)
    assert canceled.accepted and canceled.terminal
    assert snapshot.state == "canceled"
    missing = await client.cancel_task("missing")
    assert missing.accepted is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "error_type"),
    [
        ("authentication_error", ProviderAuthenticationError),
        ("rate_limit", ProviderRateLimitError),
        ("unavailable", ProviderUnavailableError),
        ("timeout", ProviderTimeoutError),
        ("protocol_error", ProviderProtocolError),
    ],
)
async def test_health_fault_modes_are_standard_errors(
    provider_context: ProviderContext,
    mode: str,
    error_type: type[Exception],
) -> None:
    client = MockProviderPlugin().create_client(
        provider_context,
        {"mode": mode},
        "credential-ref",
    )
    with pytest.raises(error_type):
        await client.health_check()


@pytest.mark.asyncio
async def test_secret_canary_never_reaches_error_strings(
    provider_context: ProviderContext,
) -> None:
    canary = "MOCK_SECRET_CANARY"
    context = ProviderContext(
        http=provider_context.http,
        secrets=CanarySecrets(canary),
        logger=provider_context.logger,
        clock=provider_context.clock,
        plugin_data=provider_context.plugin_data,
        core_version=provider_context.core_version,
        plugin_api_version=provider_context.plugin_api_version,
    )
    client = MockProviderPlugin().create_client(context, {}, "credential-ref")

    with pytest.raises(ProviderAuthenticationError) as error:
        await client.health_check()

    assert canary not in str(error.value)
    assert canary not in repr(error.value)


@pytest.mark.asyncio
async def test_model_catalog_changes_keep_stable_remote_ids(
    provider_context: ProviderContext,
) -> None:
    plugin = MockProviderPlugin()
    first = plugin.create_client(provider_context, {"catalog_revision": 1}, "credential-ref")
    second = plugin.create_client(provider_context, {"catalog_revision": 2}, "credential-ref")

    first_models = await first.list_models()
    second_models = await second.list_models()

    assert {model.remote_model_id for model in first_models} == {
        "mock-text-v1",
        "mock-image-v1",
    }
    assert {model.remote_model_id for model in second_models} == {
        "mock-text-v1",
        "mock-video-v1",
    }


@pytest.mark.asyncio
async def test_task_failure_and_unknown_remote_task_are_explicit(
    provider_context: ProviderContext,
) -> None:
    client = MockProviderPlugin().create_client(
        provider_context,
        {"mode": "task_failed"},
        "credential-ref",
    )
    with pytest.raises(ProviderTaskFailedError):
        await client.submit(_request())

    healthy = MockProviderPlugin().create_client(provider_context, {}, "credential-ref")
    with pytest.raises(ProviderProtocolError):
        await healthy.get_task("missing")
    with pytest.raises(ProviderProtocolError):
        await healthy.submit(_request("unknown.operation"))
