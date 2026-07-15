"""Strict, size-bounded AstraWeft workflow import/export format."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from astraweft.application.workflows.commands import WorkflowEdgeDraft, WorkflowNodeDraft
from astraweft.domain.workflow import (
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeType,
    WorkflowVersion,
    contains_secret_key,
    definition_payload,
)

FORMAT = "astraweft.workflow/v1"
MAX_DOCUMENT_BYTES = 1_048_576


class WorkflowImportError(ValueError):
    """An imported definition violates the portable workflow envelope."""


@dataclass(frozen=True, slots=True)
class WorkflowImportPayload:
    name: str
    description: str
    checksum: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    output_bindings: Mapping[str, object]
    nodes: tuple[WorkflowNodeDraft, ...]
    edges: tuple[WorkflowEdgeDraft, ...]


def encode_workflow(
    workflow: Workflow,
    version: WorkflowVersion,
    nodes: tuple[WorkflowNode, ...],
    edges: tuple[WorkflowEdge, ...],
) -> str:
    payload = {
        "format": FORMAT,
        "name": workflow.name,
        "description": workflow.description,
        "checksum": version.checksum,
        "definition": definition_payload(version, nodes, edges),
    }
    return json.dumps(
        _plain_json(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def decode_workflow(document: bytes | str) -> WorkflowImportPayload:
    encoded = document.encode("utf-8") if isinstance(document, str) else document
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise WorkflowImportError("工作流文件超过 1 MiB 上限")
    try:
        raw = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowImportError("工作流文件不是有效的 UTF-8 JSON") from exc
    root = _mapping(raw, "根对象")
    _exact_keys(root, {"format", "name", "description", "checksum", "definition"}, "根对象")
    if root.get("format") != FORMAT:
        raise WorkflowImportError("工作流格式或版本不受支持")
    name = _text(root.get("name"), "name", maximum=160)
    description = _text(root.get("description"), "description", maximum=4000, allow_empty=True)
    checksum = _text(root.get("checksum"), "checksum", maximum=64)
    if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
        raise WorkflowImportError("工作流 checksum 格式无效")
    definition = _mapping(root.get("definition"), "definition")
    _exact_keys(
        definition,
        {"input_schema", "output_schema", "output_bindings", "nodes", "edges"},
        "definition",
    )
    _reject_unsafe_tree(definition)
    input_schema = _string_mapping(definition.get("input_schema"), "input_schema")
    output_schema = _string_mapping(definition.get("output_schema"), "output_schema")
    output_bindings = _string_mapping(definition.get("output_bindings"), "output_bindings")
    nodes = _nodes(definition.get("nodes"))
    edges = _edges(definition.get("edges"))
    return WorkflowImportPayload(
        name=name,
        description=description,
        checksum=checksum,
        input_schema=input_schema,
        output_schema=output_schema,
        output_bindings=output_bindings,
        nodes=nodes,
        edges=edges,
    )


def _nodes(value: object) -> tuple[WorkflowNodeDraft, ...]:
    values = _sequence(value, "nodes")
    nodes: list[WorkflowNodeDraft] = []
    for index, item in enumerate(values):
        label = f"nodes[{index}]"
        raw = _mapping(item, label)
        _exact_keys(
            raw,
            {
                "node_key",
                "node_type",
                "name",
                "provider_id",
                "model_id",
                "operation",
                "input_schema",
                "output_schema",
                "input_bindings",
                "config",
                "continue_on_error",
                "position",
            },
            label,
        )
        try:
            node_type = WorkflowNodeType(_text(raw.get("node_type"), f"{label}.node_type"))
        except ValueError as exc:
            raise WorkflowImportError(f"{label}.node_type 不受支持") from exc
        position = _mapping(raw.get("position"), f"{label}.position")
        _exact_keys(position, {"x", "y"}, f"{label}.position")
        nodes.append(
            WorkflowNodeDraft(
                node_key=_text(raw.get("node_key"), f"{label}.node_key", maximum=64),
                node_type=node_type,
                name=_text(raw.get("name"), f"{label}.name", maximum=160),
                provider_id=_optional_text(raw.get("provider_id"), f"{label}.provider_id"),
                model_id=_optional_text(raw.get("model_id"), f"{label}.model_id"),
                operation=_optional_text(raw.get("operation"), f"{label}.operation"),
                input_schema=_string_mapping(raw.get("input_schema"), f"{label}.input_schema"),
                output_schema=_string_mapping(raw.get("output_schema"), f"{label}.output_schema"),
                input_bindings=_string_mapping(
                    raw.get("input_bindings"), f"{label}.input_bindings"
                ),
                config=_string_mapping(raw.get("config"), f"{label}.config"),
                continue_on_error=_boolean(
                    raw.get("continue_on_error"), f"{label}.continue_on_error"
                ),
                position_x=_integer(position.get("x"), f"{label}.position.x"),
                position_y=_integer(position.get("y"), f"{label}.position.y"),
            )
        )
    return tuple(nodes)


def _edges(value: object) -> tuple[WorkflowEdgeDraft, ...]:
    values = _sequence(value, "edges")
    edges: list[WorkflowEdgeDraft] = []
    for index, item in enumerate(values):
        label = f"edges[{index}]"
        raw = _mapping(item, label)
        _exact_keys(
            raw,
            {"source_node", "source_port", "target_node", "target_port"},
            label,
        )
        edges.append(
            WorkflowEdgeDraft(
                source_node=_text(raw.get("source_node"), f"{label}.source_node"),
                source_port=_text(raw.get("source_port"), f"{label}.source_port"),
                target_node=_text(raw.get("target_node"), f"{label}.target_node"),
                target_port=_text(raw.get("target_port"), f"{label}.target_port"),
            )
        )
    return tuple(edges)


def _reject_unsafe_tree(value: object) -> None:
    if contains_secret_key(value):
        raise WorkflowImportError("工作流文件包含机密字段")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) == "$ref" and isinstance(child, str) and not child.startswith("#/"):
                raise WorkflowImportError("工作流文件禁止远程或文件 $ref")
            _reject_unsafe_tree(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _reject_unsafe_tree(child)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise WorkflowImportError(f"{label} 必须是对象")
    return cast(Mapping[str, object], value)


def _string_mapping(value: object, label: str) -> Mapping[str, object]:
    return dict(_mapping(value, label))


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise WorkflowImportError(f"{label} 必须是数组")
    return cast(Sequence[object], value)


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        details = []
        if unknown:
            details.append("未知字段 " + ", ".join(unknown))
        if missing:
            details.append("缺少字段 " + ", ".join(missing))
        raise WorkflowImportError(f"{label} 结构无效：{'；'.join(details)}")


def _text(value: object, label: str, *, maximum: int = 240, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise WorkflowImportError(f"{label} 必须是文本")
    normalized = value.strip() if not allow_empty else value
    if (not allow_empty and not normalized) or len(normalized) > maximum:
        raise WorkflowImportError(f"{label} 长度无效")
    return normalized


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkflowImportError(f"{label} 必须是整数")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise WorkflowImportError(f"{label} 必须是布尔值")
    return value


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(child) for child in value]
    return value
