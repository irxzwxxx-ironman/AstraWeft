"""Fail-closed edge coverage for declarative mappings and client lifecycle."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

import pytest

import astraweft_custom_rest_provider.client as subject
from astraweft_custom_rest_provider import CustomRestProviderPlugin
from astraweft_provider_sdk import (
    HttpResponse,
    PluginConfigurationError,
    ProviderAuthenticationError,
    ProviderContext,
    ProviderProtocolError,
    ProviderRequest,
    ProviderValidationError,
    UnsupportedOperationError,
)


def _request(operation: str = "video.generate") -> ProviderRequest:
    return ProviderRequest(
        operation=operation,
        remote_model_id="multi-model",
        inputs={"prompt": "edge"},
        idempotency_key="edge-idem",
        trace_id="edge-trace",
        timeout_seconds=10,
    )


def test_rich_output_mapping_covers_data_artifacts_usage_and_finish_reason() -> None:
    payload = {
        "root": {"copied": 1},
        "nested": "value",
        "json": {"answer": 42},
        "number": 7,
        "usage": {"tokens": 12, "label": "seconds"},
        "cost": 99,
        "finish": "stop",
    }
    output = subject._provider_output(
        payload,
        {
            "data_pointer": "/root",
            "data": {"nested": "/nested"},
            "artifacts": [
                {
                    "kind": "json",
                    "source": "json",
                    "pointer": "/json",
                    "filename": "result.json",
                },
                {"kind": "text", "source": "text", "pointer": "/number"},
                {
                    "kind": "image",
                    "source": "url",
                    "pointer": "/missing",
                    "optional": True,
                },
            ],
            "usage": {
                "units": {"tokens": "/usage/tokens", "duration": "/usage/label"},
                "cost_micros": "/cost",
                "currency": "usd",
            },
            "finish_reason": "/finish",
        },
    )

    assert output.data == {"copied": 1, "nested": "value"}
    assert output.artifacts[0].value == {"answer": 42}
    assert output.artifacts[0].filename_hint == "result.json"
    assert output.artifacts[1].value == "7"
    assert output.usage is not None
    assert output.usage.units == {"tokens": 12, "duration": "seconds"}
    assert (output.usage.cost_micros, output.usage.currency) == (99, "USD")
    assert output.finish_reason == "stop"


@pytest.mark.parametrize(
    ("payload", "spec"),
    [
        ({"value": 1}, {"data_pointer": "/value"}),
        (
            {"value": "not-json"},
            {"artifacts": [{"kind": "json", "source": "json", "pointer": "/value"}]},
        ),
        (
            {"value": {"bad": True}},
            {"artifacts": [{"kind": "image", "source": "url", "pointer": "/value"}]},
        ),
        ({"value": True}, {"usage": {"units": {"bad": "/value"}}}),
        (
            {"value": -1},
            {"usage": {"units": {}, "cost_micros": "/value", "currency": "USD"}},
        ),
    ],
)
def test_invalid_output_mappings_fail_as_protocol_errors(
    payload: object, spec: Mapping[str, object]
) -> None:
    with pytest.raises(ProviderProtocolError):
        subject._provider_output(payload, spec)


def test_response_decoding_state_progress_and_pointer_edges() -> None:
    request = {"method": "GET", "path": "/status"}
    text = subject._response_payload(HttpResponse(200, {}, b"hello"), {"format": "text"}, request)
    assert text == {"text": "hello"}
    with pytest.raises(ProviderProtocolError):
        subject._response_payload(HttpResponse(200, {}, b"\xff"), {"format": "text"}, request)
    with pytest.raises(ProviderProtocolError):
        subject._response_payload(HttpResponse(200, {}, b"not-json"), {}, request)

    states = {
        "states": {
            "queued": ["PENDING"],
            "running": ["ACTIVE"],
            "succeeded": ["DONE"],
            "failed": ["ERROR"],
            "canceled": ["CANCELLED"],
        }
    }
    assert (
        subject._normalized_state(1, {"states": {**states["states"], "queued": ["1"]}}) == "queued"
    )
    with pytest.raises(ProviderProtocolError):
        subject._normalized_state(True, states)
    with pytest.raises(ProviderProtocolError):
        subject._normalized_state("unknown", states)
    assert subject._optional_progress({}, None) is None
    assert subject._optional_progress({"p": 500}, "/p") == 100
    with pytest.raises(ProviderProtocolError):
        subject._optional_progress({"p": "bad"}, "/p")
    assert subject._pointer({"a/b": {"~key": ["zero"]}}, "/a~1b/~0key/0") == "zero"
    assert subject._pointer([1], "/9", missing="missing") == "missing"
    with pytest.raises(ProviderProtocolError):
        subject._pointer([1], "/bad")
    with pytest.raises(ProviderProtocolError):
        subject._pointer({}, "/missing")


def test_template_header_query_and_auth_helpers_fail_closed() -> None:
    assert subject._render(
        ["${input.present}", "${input.missing}", 3],
        {"input": {"present": "yes"}},
    ) == ["yes", 3]
    with pytest.raises(ProviderValidationError):
        subject._render("hello ${input.missing}", {"input": {}})
    with pytest.raises(ProviderValidationError):
        subject._render("hello ${input.value}", {"input": {"value": {}}})
    with pytest.raises(ProviderValidationError):
        subject._render_path("/x/${input.value}", {"input": {"value": []}})
    with pytest.raises(ProviderValidationError):
        subject._render_path("not-absolute", {})
    with pytest.raises(ProviderValidationError):
        subject._render_string_mapping({"X": True}, {}, "headers")
    with pytest.raises(ProviderValidationError):
        subject._render_string_mapping({"X": "bad\nvalue"}, {}, "headers")

    assert subject._encoded_query({"empty": None, "flag": True, "many": [1, 2]}) == (
        "empty=&flag=true&many=1&many=2"
    )
    with pytest.raises(ProviderValidationError):
        subject._encoded_query({"bad": {"nested": True}})
    with pytest.raises(PluginConfigurationError):
        subject._referenced_secrets("${secret.unknown}")
    with pytest.raises(PluginConfigurationError):
        subject._validate_templates({"nested": ["${environment.home}"]})

    headers: dict[str, str] = {}
    subject._merge_auth_headers(
        headers,
        {"auth_mode": "api_key_header", "auth_header_name": "X-Key", "auth_prefix": "Token"},
        {"api_key": "key"},
    )
    assert headers == {"X-Key": "Token key"}
    for settings in (
        {"auth_mode": "api_key_header", "auth_header_name": "Host"},
        {"auth_mode": "api_key_header", "auth_header_name": "X-Key", "auth_prefix": "x\n"},
        {"auth_mode": "invalid"},
    ):
        with pytest.raises(PluginConfigurationError):
            subject._merge_auth_headers({}, settings, {"api_key": "key"})
    with pytest.raises(PluginConfigurationError):
        subject._merge_auth_headers(
            {"authorization": "configured"}, {"auth_mode": "bearer"}, {"api_key": "key"}
        )


@pytest.mark.parametrize(
    "definition",
    [
        {
            "models": [
                {
                    "id": "x",
                    "name": "x",
                    "modality": "TEXT",
                    "operations": ["text.generate"],
                    "input_schema": {"type": "not-a-schema-type"},
                    "requests": {},
                }
            ]
        },
        {
            "models": [
                {
                    "id": "x",
                    "name": "x",
                    "modality": "TEXT",
                    "operations": ["text.generate"],
                    "input_schema": {"type": "object"},
                    "requests": {"text.generate": {"submit": {}, "response": {}, "extra": 1}},
                }
            ]
        },
    ],
)
def test_deep_definition_errors_are_rejected(definition: Mapping[str, object]) -> None:
    with pytest.raises(PluginConfigurationError):
        subject._validate_definition(definition)


@pytest.mark.parametrize(
    "spec",
    [
        {"method": "GET", "path": "/x", "unknown": True},
        {"method": "TRACE", "path": "/x"},
        {"method": "GET", "path": "/x", "headers": {"Bad Header": "x"}},
        {"method": "GET", "path": "/x", "headers": {"Host": "x"}},
    ],
)
def test_request_definition_edges_are_rejected(spec: Mapping[str, object]) -> None:
    with pytest.raises(PluginConfigurationError):
        subject._validate_request(spec, "test")


@pytest.mark.parametrize(
    "spec",
    [
        {"mode": "bad"},
        {"mode": "sync", "format": "binary", "output": {}},
        {
            "mode": "async",
            "task_id": "/id",
            "state": "/state",
            "poll": {"method": "GET", "path": "/x"},
            "states": {},
            "output": {},
        },
    ],
)
def test_response_definition_edges_are_rejected(spec: Mapping[str, object]) -> None:
    with pytest.raises(PluginConfigurationError):
        subject._validate_response(spec)


@pytest.mark.parametrize(
    "spec",
    [
        {"unknown": True},
        {"artifacts": [{"kind": "bad", "source": "url", "pointer": "/x"}]},
        {"artifacts": [{"kind": "image", "source": "bad", "pointer": "/x"}]},
    ],
)
def test_output_definition_edges_are_rejected(spec: Mapping[str, object]) -> None:
    with pytest.raises(PluginConfigurationError):
        subject._validate_output(spec)


@pytest.mark.asyncio
async def test_client_failed_canceled_and_lifecycle_edges(
    provider_context: ProviderContext,
    fake_http: Any,
    settings: dict[str, object],
) -> None:
    client = CustomRestProviderPlugin().create_client(provider_context, settings, "credential-ref")
    fake_http.queue(202, {"id": "job"})
    accepted = await client.submit(_request())
    assert accepted.remote_task_id is not None
    fake_http.queue(200, {"status": "failed", "progress": 50})
    failed = await client.get_task(accepted.remote_task_id)
    assert failed.error is not None
    fake_http.queue(200, {"status": "canceled", "progress": 50})
    canceled = await client.get_task(accepted.remote_task_id)
    assert canceled.state == "canceled" and canceled.poll_after_seconds is None

    with pytest.raises(ProviderValidationError):
        await client.submit(
            ProviderRequest(
                operation="video.generate",
                remote_model_id="missing",
                inputs={"prompt": "x"},
                idempotency_key="i",
                trace_id="t",
                timeout_seconds=1,
            )
        )
    with pytest.raises(UnsupportedOperationError):
        await client.submit(_request("custom.invoke"))
    with pytest.raises(ProviderProtocolError):
        await client.get_task(subject._encode_task_identity("multi-model", "image.generate", "x"))
    for invalid in ("bad", "cr1.not-base64", "cr1.WzFd"):
        with pytest.raises(ProviderProtocolError):
            await client.get_task(invalid)
    await client.close()
    await client.close()
    with pytest.raises(ProviderProtocolError):
        await client.list_models()


@pytest.mark.asyncio
async def test_client_missing_auth_cancel_and_bad_remote_ids(
    provider_context: ProviderContext,
    fake_http: Any,
    settings: dict[str, object],
) -> None:
    without_credentials = CustomRestProviderPlugin().create_client(provider_context, settings, None)
    with pytest.raises(ProviderAuthenticationError):
        await without_credentials.health_check()

    no_cancel_settings = copy.deepcopy(settings)
    definition = no_cancel_settings["definition"]
    assert isinstance(definition, dict)
    models = definition["models"]
    assert isinstance(models, list) and isinstance(models[0], dict)
    requests = models[0]["requests"]
    assert isinstance(requests, dict) and isinstance(requests["video.generate"], dict)
    response = requests["video.generate"]["response"]
    assert isinstance(response, dict)
    response.pop("cancel")
    no_cancel = CustomRestProviderPlugin().create_client(
        provider_context, no_cancel_settings, "credential-ref"
    )
    identity = subject._encode_task_identity("multi-model", "video.generate", "job")
    with pytest.raises(UnsupportedOperationError):
        await no_cancel.cancel_task(identity)

    fake_http.queue(202, {"id": True})
    client = CustomRestProviderPlugin().create_client(provider_context, settings, "credential-ref")
    with pytest.raises(ProviderProtocolError):
        await client.submit(_request())
    fake_http.queue(202, {"id": ""})
    with pytest.raises(ProviderProtocolError):
        await client.submit(_request())


def test_low_level_type_guards() -> None:
    assert subject._positive_number("bad", 2) == 2
    assert subject._optional_mapping(None) == {}
    assert subject._optional_string(None) is None
    assert subject._pointer({"root": True}, "") == {"root": True}
    for call in (
        lambda: subject._mapping([], "x"),
        lambda: subject._sequence("not-list", "x"),
        lambda: subject._string_list([1], "x"),
        lambda: subject._string("", "x"),
        lambda: subject._optional_string(""),
        lambda: subject._pointer_text("not-pointer", "x"),
    ):
        with pytest.raises(PluginConfigurationError):
            call()
