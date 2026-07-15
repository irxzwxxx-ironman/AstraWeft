"""Restricted transform and portable workflow envelope tests."""

from __future__ import annotations

import json

import pytest

from astraweft.application.workflows import (
    TransformConfigurationError,
    WorkflowImportError,
    execute_transform,
    validate_transform_config,
)
from astraweft.application.workflows.serialization import MAX_DOCUMENT_BYTES, decode_workflow


def test_project_and_scalar_text_template_are_deterministic() -> None:
    project = {"kind": "project", "outputs": {"title": "topic", "count": "amount"}}
    template = {"kind": "text_template", "template": "{topic} · {amount}", "output": "text"}

    assert execute_transform(project, {"topic": "Stars", "amount": 3}) == {
        "title": "Stars",
        "count": 3,
    }
    assert execute_transform(template, {"topic": "Stars", "amount": 3}) == {"text": "Stars · 3"}
    assert execute_transform(template, {"topic": "Stars", "amount": 3}) == execute_transform(
        template, {"topic": "Stars", "amount": 3}
    )


@pytest.mark.parametrize(
    ("config", "inputs"),
    [
        ({"kind": "project", "outputs": {"result": 1}}, {"value": 1}),
        ({"kind": "text_template", "template": "{value}", "output": "text", "x": 1}, {}),
        ({"kind": "text_template", "template": "", "output": "text"}, {}),
        ({"kind": "project", "outputs": {"result": "missing"}}, {}),
        ({"kind": "text_template", "template": "{missing}", "output": "text"}, {}),
        (
            {"kind": "text_template", "template": "{value}", "output": "text"},
            {"value": {"nested": True}},
        ),
        (
            {"kind": "text_template", "template": "{value}", "output": "text"},
            {"value": [1, 2]},
        ),
        ({"kind": "text_template", "template": "{value", "output": "text"}, {"value": 1}),
    ],
)
def test_transforms_reject_invalid_mappings_missing_inputs_and_complex_values(
    config: dict[str, object],
    inputs: dict[str, object],
) -> None:
    with pytest.raises(TransformConfigurationError):
        execute_transform(config, inputs)


@pytest.mark.parametrize(
    "config",
    [
        {"kind": "project", "outputs": {}},
        {"kind": "project", "outputs": {"x": "y"}, "path": "forbidden"},
        {"kind": "text_template", "template": "{user.name}", "output": "text"},
        {"kind": "text_template", "template": "{value!r}", "output": "text"},
        {"kind": "shell", "command": "echo unsafe"},
    ],
)
def test_transform_rejects_executable_or_ambiguous_semantics(
    config: dict[str, object],
) -> None:
    with pytest.raises(TransformConfigurationError):
        validate_transform_config(config)

    with pytest.raises(TransformConfigurationError):
        execute_transform(config, {"user": "Ada", "value": 1})


def test_import_envelope_rejects_size_unknown_fields_remote_refs_and_secrets() -> None:
    with pytest.raises(WorkflowImportError, match="1 MiB"):
        decode_workflow(b"{" + b" " * MAX_DOCUMENT_BYTES + b"}")

    document = _empty_document()
    document["unexpected"] = True
    with pytest.raises(WorkflowImportError, match="未知字段"):
        decode_workflow(json.dumps(document))

    document = _empty_document()
    definition = document["definition"]
    assert isinstance(definition, dict)
    input_schema = definition["input_schema"]
    assert isinstance(input_schema, dict)
    input_schema["$ref"] = "https://example.invalid/schema.json"
    with pytest.raises(WorkflowImportError, match=r"\$ref"):
        decode_workflow(json.dumps(document))

    document = _empty_document()
    definition = document["definition"]
    assert isinstance(definition, dict)
    definition["output_bindings"] = {"api_key": "not-allowed"}
    with pytest.raises(WorkflowImportError, match="机密"):
        decode_workflow(json.dumps(document))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("format", "astraweft.workflow/v2", "不受支持"),
        ("checksum", "XYZ", "checksum"),
        ("name", 7, "name"),
        ("name", " ", "长度"),
    ],
)
def test_import_envelope_rejects_invalid_metadata_and_format(
    field: str,
    value: object,
    message: str,
) -> None:
    document = _empty_document()
    document[field] = value
    with pytest.raises(WorkflowImportError, match=message):
        decode_workflow(json.dumps(document))


def test_import_envelope_rejects_invalid_json_and_non_object_root() -> None:
    with pytest.raises(WorkflowImportError, match="UTF-8 JSON"):
        decode_workflow(b"\xff")
    with pytest.raises(WorkflowImportError, match="根对象"):
        decode_workflow("[]")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("nodes", {}, "必须是数组"),
        ("edges", "edge", "必须是数组"),
        ("nodes", [7], "必须是对象"),
        ("nodes", [{"node_key": "missing-fields"}], "缺少字段"),
    ],
)
def test_import_envelope_rejects_invalid_definition_collections(
    field: str,
    value: object,
    message: str,
) -> None:
    document = _empty_document()
    definition = document["definition"]
    assert isinstance(definition, dict)
    definition[field] = value
    with pytest.raises(WorkflowImportError, match=message):
        decode_workflow(json.dumps(document))


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("node_type",), "UNKNOWN", "node_type"),
        (("continue_on_error",), 1, "布尔值"),
        (("position", "x"), True, "整数"),
        (("provider_id",), 1, "provider_id"),
        (("position",), {"x": 0, "y": 0, "z": 0}, "未知字段"),
    ],
)
def test_import_envelope_rejects_invalid_node_fields(
    path: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    document = _empty_document()
    definition = document["definition"]
    assert isinstance(definition, dict)
    node = _valid_node()
    target = node
    for key in path[:-1]:
        child = target[key]
        assert isinstance(child, dict)
        target = child
    target[path[-1]] = value
    definition["nodes"] = [node]
    with pytest.raises(WorkflowImportError, match=message):
        decode_workflow(json.dumps(document))


def _empty_document() -> dict[str, object]:
    return {
        "format": "astraweft.workflow/v1",
        "name": "Imported",
        "description": "",
        "checksum": "0" * 64,
        "definition": {
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object", "properties": {}},
            "output_bindings": {},
            "nodes": [],
            "edges": [],
        },
    }


def _valid_node() -> dict[str, object]:
    return {
        "node_key": "transform_1",
        "node_type": "TRANSFORM",
        "name": "Transform",
        "provider_id": None,
        "model_id": None,
        "operation": None,
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {}},
        "input_bindings": {},
        "config": {"kind": "project", "outputs": {"result": "value"}},
        "continue_on_error": False,
        "position": {"x": 0, "y": 0},
    }
