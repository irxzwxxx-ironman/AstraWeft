"""ComfyUI URL, template, prompt patch, and execution state contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from astraweft.domain.comfyui import (
    ComfyUIExecution,
    ComfyUIExecutionStatus,
    ComfyUIHealth,
    ComfyUIInstance,
    ComfyUITemplate,
    ComfyUITransitionError,
    comfyui_prompt_checksum,
    normalize_comfyui_base_url,
    patch_api_prompt,
    validate_api_prompt,
)

_NOW = datetime(2026, 7, 15, tzinfo=UTC)
_PROMPT = {
    "1": {
        "class_type": "KSampler",
        "inputs": {"seed": 1, "positive": ["2", 0]},
    },
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
}


def _instance() -> ComfyUIInstance:
    return ComfyUIInstance(
        id="instance-1",
        name=" Local Comfy ",
        base_url="http://LOCALHOST:8188/",
        enabled=True,
        health=ComfyUIHealth.UNKNOWN,
        version=None,
        python_version=None,
        capabilities={"nodes": 2},
        node_catalog_hash=None,
        last_error_code=None,
        last_checked_at=None,
        row_version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _execution(status: ComfyUIExecutionStatus = ComfyUIExecutionStatus.PLANNED) -> ComfyUIExecution:
    remote = (
        "prompt-1"
        if status
        in {
            ComfyUIExecutionStatus.QUEUED,
            ComfyUIExecutionStatus.RUNNING,
            ComfyUIExecutionStatus.MATERIALIZING,
            ComfyUIExecutionStatus.CANCELING,
            ComfyUIExecutionStatus.SUCCESS,
        }
        else None
    )
    terminal = status.terminal
    output = {"artifacts": ["a"]} if status is ComfyUIExecutionStatus.SUCCESS else None
    return ComfyUIExecution(
        id="execution-1",
        node_run_id="node-run-1",
        instance_id="instance-1",
        template_id="template-1",
        template_checksum="a" * 64,
        workflow_checksum="b" * 64,
        prompt=_PROMPT,
        output_nodes=("1",),
        client_id="client-1",
        status=status,
        remote_prompt_id=remote,
        progress=100 if status is ComfyUIExecutionStatus.SUCCESS else None,
        output=output,
        artifact_ids=("a",) if status is ComfyUIExecutionStatus.SUCCESS else (),
        error_code=None,
        error_message=None,
        poll_after_at=None,
        timeout_at=_NOW + timedelta(hours=1),
        row_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        completed_at=_NOW if terminal else None,
    )


def test_instance_normalizes_url_and_probe_snapshot() -> None:
    instance = _instance()
    assert instance.name == "Local Comfy"
    assert instance.base_url == "http://localhost:8188"
    checked = instance.with_probe(
        health=ComfyUIHealth.HEALTHY,
        version="0.3.50",
        python_version="3.12",
        capabilities={"node_count": 100},
        node_catalog_hash="c" * 64,
        error_code=None,
        checked_at=_NOW + timedelta(seconds=1),
    )
    assert checked.row_version == 2
    assert checked.capabilities["node_count"] == 100


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://COMFY.example.com:443/api/", "https://comfy.example.com/api"),
        ("http://127.0.0.1:8188/", "http://127.0.0.1:8188"),
        ("http://[::1]:8188", "http://[::1]:8188"),
    ],
)
def test_url_normalization_accepts_secure_remote_and_loopback(value: str, expected: str) -> None:
    assert normalize_comfyui_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://192.168.1.10:8188",
        "ftp://localhost:8188",
        "https://user:pass@example.com",
        "https://example.com/?token=secret",
        "relative/path",
    ],
)
def test_url_normalization_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_comfyui_base_url(value)


def test_prompt_checksum_is_canonical_and_patch_is_immutable() -> None:
    reversed_prompt = dict(reversed(tuple(_PROMPT.items())))
    assert comfyui_prompt_checksum(_PROMPT) == comfyui_prompt_checksum(reversed_prompt)
    patched = patch_api_prompt(
        _PROMPT,
        {"prompt": {"node_id": "2", "input_name": "text"}},
        {"prompt": "new"},
    )
    assert patched["2"]["inputs"]["text"] == "new"  # type: ignore[index]
    assert _PROMPT["2"]["inputs"]["text"] == "old"  # type: ignore[index]


@pytest.mark.parametrize(
    "prompt",
    [
        {},
        {"1": {}},
        {"1": {"class_type": "X", "inputs": "bad"}},
        {1: {"class_type": "X", "inputs": {}}},
    ],
)
def test_api_prompt_validation_rejects_invalid_nodes(prompt: dict[object, object]) -> None:
    with pytest.raises(ValueError):
        validate_api_prompt(prompt)  # type: ignore[arg-type]


def test_template_validates_checksum_and_freezes_values() -> None:
    template = ComfyUITemplate(
        id="template-1",
        instance_id="instance-1",
        name=" Image ",
        prompt=_PROMPT,
        checksum=comfyui_prompt_checksum(_PROMPT),
        input_schema={"type": "object", "properties": {"prompt": {"type": "string"}}},
        input_targets={"prompt": {"node_id": "2", "input_name": "text"}},
        output_nodes=("1",),
        row_version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert template.name == "Image"
    with pytest.raises(ValueError):
        replace(template, checksum="X" * 64)


def test_execution_state_machine_requires_identity_and_materialized_output() -> None:
    planned = _execution()
    submitting = planned.transition(
        ComfyUIExecutionStatus.SUBMITTING,
        _NOW + timedelta(seconds=1),
    )
    queued = submitting.transition(
        ComfyUIExecutionStatus.QUEUED,
        _NOW + timedelta(seconds=2),
        remote_prompt_id="prompt-1",
        progress=0,
    )
    running = queued.transition(
        ComfyUIExecutionStatus.RUNNING,
        _NOW + timedelta(seconds=3),
        progress=42,
    )
    materializing = running.transition(
        ComfyUIExecutionStatus.MATERIALIZING,
        _NOW + timedelta(seconds=4),
        output={"9": {"images": []}},
    )
    success = materializing.transition(
        ComfyUIExecutionStatus.SUCCESS,
        _NOW + timedelta(seconds=5),
        output={"artifacts": ["a"]},
        artifact_ids=("a",),
    )
    assert success.status is ComfyUIExecutionStatus.SUCCESS
    assert success.progress == 100
    assert success.completed_at == _NOW + timedelta(seconds=5)
    with pytest.raises(ComfyUITransitionError):
        success.refresh(_NOW + timedelta(seconds=6), progress=100)
    with pytest.raises(ComfyUITransitionError):
        planned.transition(ComfyUIExecutionStatus.RUNNING, _NOW)


def test_execution_refresh_updates_progress_without_changing_status() -> None:
    queued = _execution(ComfyUIExecutionStatus.QUEUED)
    refreshed = queued.refresh(
        _NOW + timedelta(seconds=1),
        progress=25,
        poll_after_at=_NOW + timedelta(seconds=2),
    )
    assert refreshed.status is ComfyUIExecutionStatus.QUEUED
    assert refreshed.progress == 25
    assert refreshed.row_version == 2


@pytest.mark.parametrize(
    "change",
    [
        {"template_checksum": "g" * 64},
        {"progress": 101},
        {"status": ComfyUIExecutionStatus.RUNNING, "remote_prompt_id": None},
        {"status": ComfyUIExecutionStatus.FAILED, "completed_at": None},
    ],
)
def test_execution_rejects_corrupt_persisted_facts(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(_execution(), **change)  # type: ignore[arg-type]


def test_patch_rejects_missing_targets() -> None:
    with pytest.raises(ValueError, match="missing"):
        patch_api_prompt(_PROMPT, {}, {"missing": "value"})
    with pytest.raises(ValueError, match="does not exist"):
        patch_api_prompt(
            _PROMPT,
            {"prompt": {"node_id": "404", "input_name": "text"}},
            {"prompt": "value"},
        )
