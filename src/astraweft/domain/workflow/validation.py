"""Pure DAG, port, binding, secret, and checksum validation."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from astraweft.domain.comfyui import comfyui_prompt_checksum
from astraweft.domain.workflow.entities import (
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeType,
    WorkflowPort,
    WorkflowVersion,
)

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "client_secret",
        "access_token",
        "refresh_token",
        "private_key",
    }
)


class WorkflowIssueSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass(frozen=True, slots=True)
class WorkflowIssue:
    severity: WorkflowIssueSeverity
    code: str
    message: str
    node_key: str | None = None
    port_name: str | None = None


def ports_from_schema(schema: Mapping[str, object]) -> tuple[WorkflowPort, ...]:
    properties = schema.get("properties")
    required_value = schema.get("required", ())
    required = (
        frozenset(item for item in required_value if isinstance(item, str))
        if isinstance(required_value, Sequence)
        and not isinstance(required_value, (str, bytes, bytearray))
        else frozenset()
    )
    if not isinstance(properties, Mapping):
        return ()
    ports: list[WorkflowPort] = []
    for name, value in properties.items():
        if isinstance(name, str) and isinstance(value, Mapping):
            ports.append(
                WorkflowPort(
                    name=name,
                    schema={str(key): child for key, child in value.items()},
                    required=name in required,
                )
            )
    return tuple(ports)


def validate_definition(
    version: WorkflowVersion,
    nodes: Sequence[WorkflowNode],
    edges: Sequence[WorkflowEdge],
) -> tuple[WorkflowIssue, ...]:
    issues: list[WorkflowIssue] = []
    if not _object_schema(version.input_schema):
        issues.append(_issue("workflow_input_schema", "工作流输入 Schema 必须是 object"))
    if not _object_schema(version.output_schema):
        issues.append(_issue("workflow_output_schema", "工作流输出 Schema 必须是 object"))

    by_id: dict[str, WorkflowNode] = {}
    by_key: dict[str, WorkflowNode] = {}
    for node in nodes:
        if node.workflow_version_id != version.id:
            issues.append(_node_issue(node, "node_version", "节点不属于当前工作流版本"))
        if node.id in by_id or node.node_key in by_key:
            issues.append(_node_issue(node, "duplicate_node", "节点 key 或 ID 重复"))
        by_id[node.id] = node
        by_key[node.node_key] = node
        if contains_secret_key(node.config) or contains_secret_key(node.input_bindings):
            issues.append(_node_issue(node, "secret_in_definition", "节点定义包含机密字段"))
        if node.node_type is WorkflowNodeType.COMFYUI:
            issues.extend(_validate_comfyui_config(node))

    incoming: dict[str, list[WorkflowEdge]] = defaultdict(list)
    outgoing: dict[str, list[WorkflowEdge]] = defaultdict(list)
    occupied_targets: set[tuple[str, str]] = set()
    valid_graph_edges: list[WorkflowEdge] = []
    for edge in edges:
        source = by_id.get(edge.source_node_id)
        target = by_id.get(edge.target_node_id)
        if edge.workflow_version_id != version.id:
            issues.append(_issue("edge_version", "边不属于当前工作流版本"))
        if source is None or target is None:
            issues.append(_issue("edge_node_missing", "边引用了不存在的节点"))
            continue
        target_key = (target.id, edge.target_port)
        if target_key in occupied_targets:
            issues.append(
                _port_issue(target, edge.target_port, "duplicate_input_edge", "输入端口有多条边")
            )
        occupied_targets.add(target_key)
        source_ports = {port.name: port for port in ports_from_schema(source.output_schema)}
        target_ports = {port.name: port for port in ports_from_schema(target.input_schema)}
        source_port = source_ports.get(edge.source_port)
        target_port = target_ports.get(edge.target_port)
        if source_port is None:
            issues.append(
                _port_issue(source, edge.source_port, "source_port_missing", "源输出端口不存在")
            )
        if target_port is None:
            issues.append(
                _port_issue(target, edge.target_port, "target_port_missing", "目标输入端口不存在")
            )
        if (
            source_port is not None
            and target_port is not None
            and not schemas_compatible(source_port.schema, target_port.schema)
        ):
            issues.append(
                _port_issue(target, edge.target_port, "port_type_mismatch", "上下游端口类型不兼容")
            )
        incoming[target.id].append(edge)
        outgoing[source.id].append(edge)
        valid_graph_edges.append(edge)

    if _has_cycle(tuple(by_id), valid_graph_edges):
        issues.append(_issue("cycle", "工作流图包含环"))

    workflow_inputs = {port.name for port in ports_from_schema(version.input_schema)}
    for node in nodes:
        connected = {edge.target_port for edge in incoming[node.id]}
        input_ports = {port.name: port for port in ports_from_schema(node.input_schema)}
        for port in input_ports.values():
            binding = node.input_bindings.get(port.name)
            if port.name not in connected and binding is None and port.required:
                issues.append(
                    _port_issue(node, port.name, "required_input_unbound", "必填输入未绑定")
                )
            if port.name in connected and binding is not None:
                issues.append(
                    _port_issue(node, port.name, "input_bound_twice", "输入端口同时配置了边和绑定")
                )
            if binding is not None:
                issues.extend(_validate_binding(node, port, binding, workflow_inputs))
        issues.extend(
            _port_issue(node, bound_name, "binding_port_missing", "绑定引用了不存在的输入端口")
            for bound_name in node.input_bindings
            if bound_name not in input_ports
        )

    workflow_outputs = {port.name: port for port in ports_from_schema(version.output_schema)}
    for name, port in workflow_outputs.items():
        binding = version.output_bindings.get(name)
        if binding is None and port.required:
            issues.append(
                WorkflowIssue(
                    WorkflowIssueSeverity.ERROR,
                    "required_output_unbound",
                    "必填工作流输出未绑定",
                    port_name=name,
                )
            )
        if binding is not None:
            issues.extend(_validate_output_binding(name, port, binding, by_key))
    issues.extend(
        WorkflowIssue(
            WorkflowIssueSeverity.ERROR,
            "output_binding_missing",
            "输出绑定引用了不存在的工作流输出端口",
            port_name=bound_name,
        )
        for bound_name in version.output_bindings
        if bound_name not in workflow_outputs
    )
    return tuple(issues)


def schemas_compatible(
    source: Mapping[str, object],
    target: Mapping[str, object],
) -> bool:
    source_types = _schema_types(source)
    target_types = _schema_types(target)
    if not source_types or not target_types:
        return True
    expanded_source = set(source_types)
    if "integer" in expanded_source:
        expanded_source.add("number")
    return bool(expanded_source & target_types)


def contains_secret_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _SECRET_KEYS or contains_secret_key(child):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(contains_secret_key(child) for child in value)
    return False


def _validate_comfyui_config(node: WorkflowNode) -> tuple[WorkflowIssue, ...]:
    config = node.config
    instance_id = config.get("instance_id")
    template_checksum = config.get("template_checksum")
    prompt = config.get("prompt")
    targets = config.get("input_targets")
    outputs = config.get("output_nodes")
    if (
        not isinstance(instance_id, str)
        or not instance_id
        or not isinstance(template_checksum, str)
        or not isinstance(prompt, Mapping)
        or not isinstance(targets, Mapping)
        or not isinstance(outputs, Sequence)
        or isinstance(outputs, (str, bytes, bytearray))
        or not outputs
    ):
        return (_node_issue(node, "comfyui_config", "ComfyUI 节点配置不完整"),)
    try:
        actual_checksum = comfyui_prompt_checksum(prompt)
    except ValueError:
        return (_node_issue(node, "comfyui_prompt", "ComfyUI API 模板格式无效"),)
    issues: list[WorkflowIssue] = []
    if actual_checksum != template_checksum:
        issues.append(_node_issue(node, "comfyui_checksum", "ComfyUI 模板校验和不一致"))
    properties = node.input_schema.get("properties")
    input_names = set(properties) if isinstance(properties, Mapping) else set()
    if set(targets) != input_names:
        issues.append(_node_issue(node, "comfyui_input_targets", "ComfyUI 输入端口映射不完整"))
    for target in targets.values():
        if not isinstance(target, Mapping):
            issues.append(_node_issue(node, "comfyui_input_target", "ComfyUI 输入映射无效"))
            break
        node_id = target.get("node_id")
        input_name = target.get("input_name")
        prompt_node = prompt.get(node_id) if isinstance(node_id, str) else None
        prompt_inputs = prompt_node.get("inputs") if isinstance(prompt_node, Mapping) else None
        if (
            not isinstance(input_name, str)
            or not isinstance(prompt_inputs, Mapping)
            or input_name not in prompt_inputs
        ):
            issues.append(
                _node_issue(node, "comfyui_input_target", "ComfyUI 输入映射指向不存在的参数")
            )
            break
    if any(not isinstance(node_id, str) or node_id not in prompt for node_id in outputs):
        issues.append(_node_issue(node, "comfyui_output_nodes", "ComfyUI 输出节点不存在"))
    return tuple(issues)


def definition_payload(
    version: WorkflowVersion,
    nodes: Sequence[WorkflowNode],
    edges: Sequence[WorkflowEdge],
) -> Mapping[str, object]:
    by_id = {node.id: node.node_key for node in nodes}
    node_payloads = [
        {
            "node_key": node.node_key,
            "node_type": node.node_type.value,
            "name": node.name,
            "provider_id": node.provider_id,
            "model_id": node.model_id,
            "operation": node.operation,
            "input_schema": node.input_schema,
            "output_schema": node.output_schema,
            "input_bindings": node.input_bindings,
            "config": node.config,
            "continue_on_error": node.continue_on_error,
            "position": {"x": node.position_x, "y": node.position_y},
        }
        for node in sorted(nodes, key=lambda item: item.node_key)
    ]
    edge_payloads = [
        {
            "source_node": by_id.get(edge.source_node_id, edge.source_node_id),
            "source_port": edge.source_port,
            "target_node": by_id.get(edge.target_node_id, edge.target_node_id),
            "target_port": edge.target_port,
        }
        for edge in sorted(
            edges,
            key=lambda item: (
                by_id.get(item.source_node_id, item.source_node_id),
                item.source_port,
                by_id.get(item.target_node_id, item.target_node_id),
                item.target_port,
            ),
        )
    ]
    return {
        "input_schema": version.input_schema,
        "output_schema": version.output_schema,
        "output_bindings": version.output_bindings,
        "nodes": node_payloads,
        "edges": edge_payloads,
    }


def definition_checksum(
    version: WorkflowVersion,
    nodes: Sequence[WorkflowNode],
    edges: Sequence[WorkflowEdge],
) -> str:
    payload = _plain_json(definition_payload(version, nodes, edges))
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def topological_node_ids(
    nodes: Sequence[WorkflowNode],
    edges: Sequence[WorkflowEdge],
) -> tuple[str, ...]:
    ids = {node.id for node in nodes}
    indegree = dict.fromkeys(ids, 0)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.source_node_id in ids and edge.target_node_id in ids:
            indegree[edge.target_node_id] += 1
            outgoing[edge.source_node_id].append(edge.target_node_id)
    ready = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    ordered: list[str] = []
    while ready:
        node_id = ready.popleft()
        ordered.append(node_id)
        for target in sorted(outgoing[node_id]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    return tuple(ordered)


def _validate_binding(
    node: WorkflowNode,
    port: WorkflowPort,
    binding: object,
    workflow_inputs: set[str],
) -> tuple[WorkflowIssue, ...]:
    if not isinstance(binding, Mapping):
        return (_port_issue(node, port.name, "binding_shape", "输入绑定格式无效"),)
    kind = binding.get("kind")
    if kind == "workflow_input":
        name = binding.get("name")
        if not isinstance(name, str) or name not in workflow_inputs:
            return (
                _port_issue(node, port.name, "workflow_input_missing", "绑定的工作流输入不存在"),
            )
        return ()
    if kind == "constant":
        if "value" not in binding:
            return (_port_issue(node, port.name, "constant_missing", "常量绑定缺少 value"),)
        return ()
    return (_port_issue(node, port.name, "binding_kind", "输入绑定类型不受支持"),)


def _validate_output_binding(
    name: str,
    port: WorkflowPort,
    binding: object,
    by_key: Mapping[str, WorkflowNode],
) -> tuple[WorkflowIssue, ...]:
    if not isinstance(binding, Mapping):
        return (
            WorkflowIssue(
                WorkflowIssueSeverity.ERROR,
                "output_binding_shape",
                "输出绑定格式无效",
                port_name=name,
            ),
        )
    node_key = binding.get("node")
    source_port_name = binding.get("port")
    if not isinstance(node_key, str) or not isinstance(source_port_name, str):
        return (
            WorkflowIssue(
                WorkflowIssueSeverity.ERROR,
                "output_binding_shape",
                "输出绑定必须包含 node 和 port",
                port_name=name,
            ),
        )
    node = by_key.get(node_key)
    if node is None:
        return (
            WorkflowIssue(
                WorkflowIssueSeverity.ERROR,
                "output_node_missing",
                "输出绑定引用了不存在的节点",
                node_key=node_key,
                port_name=name,
            ),
        )
    source_ports = {item.name: item for item in ports_from_schema(node.output_schema)}
    source_port = source_ports.get(source_port_name)
    if source_port is None:
        return (_port_issue(node, source_port_name, "output_port_missing", "输出端口不存在"),)
    if not schemas_compatible(source_port.schema, port.schema):
        return (
            _port_issue(node, source_port_name, "output_type_mismatch", "工作流输出类型不兼容"),
        )
    return ()


def _has_cycle(node_ids: Sequence[str], edges: Sequence[WorkflowEdge]) -> bool:
    nodes = tuple(node_ids)
    fake_nodes = set(nodes)
    ordered = topological_node_ids_from_ids(fake_nodes, edges)
    return len(ordered) != len(fake_nodes)


def topological_node_ids_from_ids(
    ids: set[str],
    edges: Sequence[WorkflowEdge],
) -> tuple[str, ...]:
    indegree = dict.fromkeys(ids, 0)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.source_node_id in ids and edge.target_node_id in ids:
            indegree[edge.target_node_id] += 1
            outgoing[edge.source_node_id].append(edge.target_node_id)
    ready = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    ordered: list[str] = []
    while ready:
        source = ready.popleft()
        ordered.append(source)
        for target in sorted(outgoing[source]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    return tuple(ordered)


def _schema_types(schema: Mapping[str, object]) -> set[str]:
    raw = schema.get("type")
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        return {item for item in raw if isinstance(item, str)}
    return set()


def _object_schema(schema: Mapping[str, object]) -> bool:
    return schema.get("type") == "object" and isinstance(schema.get("properties", {}), Mapping)


def _issue(code: str, message: str) -> WorkflowIssue:
    return WorkflowIssue(WorkflowIssueSeverity.ERROR, code, message)


def _node_issue(node: WorkflowNode, code: str, message: str) -> WorkflowIssue:
    return WorkflowIssue(WorkflowIssueSeverity.ERROR, code, message, node_key=node.node_key)


def _port_issue(
    node: WorkflowNode,
    port: str,
    code: str,
    message: str,
) -> WorkflowIssue:
    return WorkflowIssue(
        WorkflowIssueSeverity.ERROR,
        code,
        message,
        node_key=node.node_key,
        port_name=port,
    )


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(child) for child in value]
    return value
