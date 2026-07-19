"""Multi-route submit, poll, cancel, auth, mapping, and privacy tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from astraweft_custom_rest_provider import CustomRestProviderPlugin
from astraweft_provider_sdk import (
    PluginConfigurationError,
    ProviderAuthenticationError,
    ProviderContext,
    ProviderError,
    ProviderRateLimitError,
    ProviderRequest,
)


def _request(operation: str, inputs: Mapping[str, object]) -> ProviderRequest:
    return ProviderRequest(
        operation=operation,
        remote_model_id="multi-model",
        inputs=inputs,
        idempotency_key="idem-1",
        trace_id="trace-1",
        timeout_seconds=90,
    )


@pytest.mark.asyncio
async def test_health_models_and_sync_route_render_auth_and_artifact(
    provider_context: ProviderContext,
    fake_http: Any,
    settings: Mapping[str, object],
) -> None:
    fake_http.queue(200, {"ok": True})
    fake_http.queue(
        200,
        {"id": "image-1", "result": {"url": "https://assets.example.test/1.png"}},
        headers={"X-Request-ID": "request-1"},
    )
    client = CustomRestProviderPlugin().create_client(provider_context, settings, "credential-ref")

    health = await client.health_check()
    models = await client.list_models()
    result = await client.submit(_request("image.generate", {"prompt": "hello", "seed": 42}))

    assert health.status == "healthy"
    assert health.latency_ms == 24
    assert len(models) == 1
    assert models[0].operations == {"image.generate", "video.generate"}
    assert result.mode == "completed"
    assert result.output is not None
    assert result.output.data == {"request_id": "image-1"}
    assert result.output.artifacts[0].value == "https://assets.example.test/1.png"
    assert result.provider_request_id == "request-1"
    sent = fake_http.requests[1]
    assert sent.url == "https://gateway.example.test/api/images/multi-model?seed=42"
    assert sent.headers["Authorization"] == "Bearer KEY_SECRET_CANARY"
    assert sent.headers["X-Provider-Secret"] == "SECOND_SECRET_CANARY"
    assert sent.json_body == {"prompt": "hello", "model": "multi-model"}
    assert sent.idempotency_key == "idem-1"
    assert sent.trace_id == "trace-1"


@pytest.mark.asyncio
async def test_optional_input_is_pruned_and_async_route_recovers_polls_and_cancels(
    provider_context: ProviderContext,
    fake_http: Any,
    settings: Mapping[str, object],
) -> None:
    fake_http.queue(200, {"id": "image-2", "result": {"url": "https://a.test/i.png"}})
    fake_http.queue(202, {"id": "job/with unsafe chars"})
    fake_http.queue(200, {"id": "job", "status": "running", "progress": 37.9})
    fake_http.queue(
        200,
        {
            "id": "job",
            "status": "done",
            "progress": 100,
            "result": {"url": "https://assets.example.test/video.mp4"},
        },
    )
    fake_http.queue(204, b"")
    client = CustomRestProviderPlugin().create_client(provider_context, settings, "credential-ref")

    await client.submit(_request("image.generate", {"prompt": "no seed"}))
    accepted = await client.submit(_request("video.generate", {"prompt": "movie"}))
    assert accepted.remote_task_id is not None
    running = await client.get_task(accepted.remote_task_id)
    succeeded = await client.get_task(accepted.remote_task_id)
    canceled = await client.cancel_task(accepted.remote_task_id)

    assert fake_http.requests[0].url.endswith("/images/multi-model")
    assert "?" not in fake_http.requests[0].url
    assert accepted.mode == "accepted"
    assert accepted.poll_after_seconds == 1.5
    assert (running.state, running.progress) == ("running", 37)
    assert succeeded.state == "succeeded"
    assert succeeded.output is not None
    assert succeeded.output.artifacts[0].kind == "video"
    assert fake_http.requests[2].url.endswith("/jobs/job%2Fwith%20unsafe%20chars")
    assert canceled.accepted is True
    assert canceled.terminal is True
    assert fake_http.requests[-1].method == "DELETE"


@pytest.mark.asyncio
async def test_custom_secret_template_and_basic_auth_modes(
    provider_context: ProviderContext,
    fake_http: Any,
    settings: dict[str, object],
) -> None:
    definition = settings["definition"]
    assert isinstance(definition, dict)
    models = definition["models"]
    assert isinstance(models, list)
    model = models[0]
    assert isinstance(model, dict)
    requests = model["requests"]
    assert isinstance(requests, dict)
    image_flow = requests["image.generate"]
    assert isinstance(image_flow, dict)
    submit = image_flow["submit"]
    assert isinstance(submit, dict)
    submit["query"] = {"key": "${secret.api_key}"}
    settings["auth_mode"] = "custom_templates"
    fake_http.queue(200, {"id": "1", "result": {"url": "https://a.test/i.png"}})
    custom = CustomRestProviderPlugin().create_client(provider_context, settings, "credential-ref")
    await custom.submit(_request("image.generate", {"prompt": "x"}))
    assert fake_http.requests[0].url.endswith("?key=KEY_SECRET_CANARY")
    assert "Authorization" not in fake_http.requests[0].headers

    settings["auth_mode"] = "basic"
    submit["query"] = {}
    fake_http.queue(200, {"id": "2", "result": {"url": "https://a.test/i.png"}})
    basic = CustomRestProviderPlugin().create_client(provider_context, settings, "credential-ref")
    await basic.submit(_request("image.generate", {"prompt": "x"}))
    assert fake_http.requests[1].headers["Authorization"] == "Basic dXNlcjpwYXNz"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 500])
async def test_upstream_errors_are_normalized_without_response_or_secret_leaks(
    provider_context: ProviderContext,
    fake_http: Any,
    settings: Mapping[str, object],
    status: int,
) -> None:
    fake_http.queue(status, {"error": "REMOTE_RESPONSE_SECRET_CANARY"})
    client = CustomRestProviderPlugin().create_client(provider_context, settings, "credential-ref")
    expected = (
        ProviderAuthenticationError
        if status == 401
        else (ProviderRateLimitError if status == 429 else ProviderError)
    )
    with pytest.raises(expected) as captured:
        await client.submit(_request("image.generate", {"prompt": "x"}))
    rendered = str(captured.value) + repr(captured.value)
    assert "REMOTE_RESPONSE_SECRET_CANARY" not in rendered
    assert "KEY_SECRET_CANARY" not in rendered


@pytest.mark.parametrize(
    "mutation",
    [
        lambda definition: definition.update({"unknown": True}),
        lambda definition: definition.update({"models": []}),
        lambda definition: definition["models"][0].update({"operations": ["bad.op"]}),
        lambda definition: definition["models"][0]["requests"]["image.generate"]["submit"].update(
            {"path": "https://evil.example"}
        ),
    ],
)
def test_invalid_declarative_definitions_fail_closed(
    provider_context: ProviderContext,
    settings: dict[str, object],
    mutation: Any,
) -> None:
    definition = settings["definition"]
    mutation(definition)
    with pytest.raises(PluginConfigurationError):
        CustomRestProviderPlugin().create_client(provider_context, settings, "credential-ref")


def test_missing_endpoint_and_literal_sensitive_header_fail_closed(
    provider_context: ProviderContext,
    settings: dict[str, object],
) -> None:
    no_endpoint = ProviderContext(
        http=provider_context.http,
        secrets=provider_context.secrets,
        logger=provider_context.logger,
        clock=provider_context.clock,
        plugin_data=provider_context.plugin_data,
        core_version=provider_context.core_version,
        plugin_api_version=provider_context.plugin_api_version,
    )
    with pytest.raises(PluginConfigurationError):
        CustomRestProviderPlugin().create_client(no_endpoint, settings, "credential-ref")
    definition = settings["definition"]
    assert isinstance(definition, dict)
    models = definition["models"]
    assert isinstance(models, list) and isinstance(models[0], dict)
    requests = models[0]["requests"]
    assert isinstance(requests, dict)
    image = requests["image.generate"]
    assert isinstance(image, dict) and isinstance(image["submit"], dict)
    image["submit"]["headers"] = {"Authorization": "Bearer literal-secret"}
    with pytest.raises(PluginConfigurationError):
        CustomRestProviderPlugin().create_client(provider_context, settings, "credential-ref")
