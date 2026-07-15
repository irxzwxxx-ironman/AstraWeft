"""Public Provider DTO and error safety tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest

from astraweft_provider_sdk import (
    ProviderAuthenticationError,
    ProviderCallInfo,
    ProviderDescriptor,
    ProviderOutput,
    RemoteArtifact,
    RemoteError,
    RemoteTaskSnapshot,
    SecretValue,
    SubmissionResult,
    Usage,
)
from astraweft_provider_sdk._json import canonical_json, freeze_json


def _descriptor() -> ProviderDescriptor:
    schema: Mapping[str, object] = {"type": "object", "properties": {}}
    return ProviderDescriptor(
        plugin_id="com.example.provider",
        name="Example",
        version="1.0.0",
        plugin_api=">=1.0,<2",
        description="Example",
        operations=frozenset({"text.generate"}),
        supports_async_tasks=False,
        supports_cancel=False,
        supports_model_discovery=True,
        supports_usage=False,
        default_endpoint=None,
        settings_schema=schema,
        settings_ui_schema={},
        credential_schema=schema,
    )


def test_json_values_are_frozen_canonical_and_strict() -> None:
    original = {"nested": {"enabled": True}, "items": [2, 1]}
    frozen = freeze_json(original)
    assert isinstance(frozen, Mapping)
    assert canonical_json(frozen) == '{"items":[2,1],"nested":{"enabled":true}}'
    with pytest.raises(TypeError):
        frozen["new"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        freeze_json({1: "invalid"})
    with pytest.raises(TypeError):
        freeze_json(float("nan"))
    with pytest.raises(TypeError):
        freeze_json(object())


def test_secret_and_provider_error_repr_are_safe() -> None:
    canary = "SDK_SECRET_CANARY"
    secret = SecretValue(canary)
    error = ProviderAuthenticationError(
        "Credential rejected",
        technical_message="safe diagnostic",
        safe_details={"field": "api_key"},
    )

    assert secret.reveal() == canary
    assert secret.as_bearer().endswith(canary)
    assert canary not in str(secret)
    assert canary not in repr(secret)
    assert "safe diagnostic" not in repr(error)
    assert error.code == "authentication_error"
    assert error.retryable is False
    with pytest.raises(ValueError):
        SecretValue("")
    with pytest.raises(ValueError):
        ProviderAuthenticationError("")
    with pytest.raises(ValueError):
        ProviderAuthenticationError("error", retry_after_seconds=-1)


def test_descriptor_and_usage_validate_invariants() -> None:
    descriptor = _descriptor()
    assert descriptor.operations == frozenset({"text.generate"})
    with pytest.raises(TypeError):
        descriptor.settings_schema["new"] = True  # type: ignore[index]
    with pytest.raises(ValueError):
        replace(descriptor, operations=frozenset())
    with pytest.raises(ValueError):
        Usage(units={}, cost_micros=1, currency=None)
    with pytest.raises(ValueError):
        Usage(units={}, cost_micros=-1, currency="USD")


def test_submission_and_remote_task_state_invariants() -> None:
    output = ProviderOutput(
        data={"text": "ok"},
        artifacts=(RemoteArtifact("text", "text", "ok"),),
        usage=Usage(units={"requests": 1}),
    )
    completed = SubmissionResult(mode="completed", output=output, progress=100)
    accepted = SubmissionResult(mode="accepted", remote_task_id="remote-1", progress=0)
    assert completed.output is output
    assert accepted.remote_task_id == "remote-1"
    with pytest.raises(ValueError):
        SubmissionResult(mode="completed")
    with pytest.raises(ValueError):
        SubmissionResult(mode="accepted", remote_task_id="remote", output=output)
    with pytest.raises(ValueError):
        SubmissionResult(mode="accepted", remote_task_id="remote", progress=101)
    with pytest.raises(ValueError):
        RemoteTaskSnapshot(state="succeeded")
    with pytest.raises(ValueError):
        RemoteTaskSnapshot(state="failed")
    failed = RemoteTaskSnapshot(state="failed", error=RemoteError("failed", "safe"), progress=20)
    assert failed.error is not None


def test_call_metadata_is_normalized_and_request_ids_stay_consistent() -> None:
    call = ProviderCallInfo(" post ", " /v1/responses ", 200, " req_123 ")
    output = ProviderOutput(data={"text": "ok"})
    result = SubmissionResult(mode="completed", output=output, call=call)
    error = ProviderAuthenticationError("Credential rejected", call=call)

    assert (call.method, call.url_template, call.provider_request_id) == (
        "POST",
        "/v1/responses",
        "req_123",
    )
    assert result.provider_request_id == "req_123"
    assert error.provider_request_id == "req_123"
    with pytest.raises(ValueError, match="request IDs"):
        SubmissionResult(
            mode="completed",
            output=output,
            provider_request_id="different",
            call=call,
        )
    with pytest.raises(ValueError, match="request IDs"):
        ProviderAuthenticationError(
            "Credential rejected",
            provider_request_id="different",
            call=call,
        )


@pytest.mark.parametrize(
    "url_template",
    ["https://api.example.test/v1/models", "v1/models", "/v1/models?key=secret", "/v1/#x"],
)
def test_call_metadata_rejects_unsafe_url_templates(url_template: str) -> None:
    with pytest.raises(ValueError, match="safe path template"):
        ProviderCallInfo("GET", url_template, 200)


def test_call_metadata_rejects_invalid_status_and_request_id() -> None:
    with pytest.raises(ValueError, match="HTTP status"):
        ProviderCallInfo("GET", "/v1/models", 99)
    with pytest.raises(ValueError, match="safe ASCII"):
        ProviderCallInfo("GET", "/v1/models", 200, "请求-1")
