"""Workflow version, DAG, port, checksum, and run state tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from astraweft.domain.workflow import (
    ArtifactLink,
    ArtifactLinkDirection,
    NodeRun,
    NodeRunStatus,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeType,
    WorkflowPort,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowTransitionError,
    WorkflowVersion,
    WorkflowVersionStatus,
    contains_secret_key,
    definition_checksum,
    ports_from_schema,
    schemas_compatible,
    topological_node_ids,
    validate_definition,
)

_NOW = datetime(2026, 7, 15, tzinfo=UTC)
_EMPTY_SHA = "0" * 64


def _version(**changes: object) -> WorkflowVersion:
    values: dict[str, object] = {
        "id": "version-1",
        "workflow_id": "workflow-1",
        "version_no": 1,
        "status": WorkflowVersionStatus.DRAFT,
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "output_bindings": {"text": {"node": "second", "port": "text"}},
        "checksum": _EMPTY_SHA,
        "row_version": 1,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    values.update(changes)
    return WorkflowVersion(**values)  # type: ignore[arg-type]


def _node(
    node_id: str,
    key: str,
    *,
    bindings: dict[str, object] | None = None,
    output_type: str = "string",
    config: dict[str, object] | None = None,
) -> WorkflowNode:
    return WorkflowNode(
        id=node_id,
        workflow_version_id="version-1",
        node_key=key,
        node_type=WorkflowNodeType.PROVIDER_MODEL,
        name=key.title(),
        provider_id="provider-1",
        model_id="model-1",
        operation="text.generate",
        input_schema={
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
        },
        output_schema={
            "type": "object",
            "properties": {"text": {"type": output_type}},
            "required": ["text"],
        },
        input_bindings=bindings or {},
        config=config or {},
        continue_on_error=False,
        position_x=0,
        position_y=0,
    )


def _valid_definition() -> tuple[
    WorkflowVersion, tuple[WorkflowNode, ...], tuple[WorkflowEdge, ...]
]:
    first = _node(
        "node-1",
        "first",
        bindings={"prompt": {"kind": "workflow_input", "name": "topic"}},
    )
    second = _node("node-2", "second")
    edge = WorkflowEdge(
        id="edge-1",
        workflow_version_id="version-1",
        source_node_id=first.id,
        source_port="text",
        target_node_id=second.id,
        target_port="prompt",
    )
    return _version(), (first, second), (edge,)


def _workflow(**changes: object) -> Workflow:
    values: dict[str, object] = {
        "id": "workflow-1",
        "name": "Workflow",
        "description": "",
        "current_version_id": None,
        "row_version": 1,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    values.update(changes)
    return Workflow(**values)  # type: ignore[arg-type]


def _run(**changes: object) -> WorkflowRun:
    values: dict[str, object] = {
        "id": "run-1",
        "workflow_id": "workflow-1",
        "workflow_version_id": "version-1",
        "status": WorkflowRunStatus.CREATED,
        "input": {},
        "output": None,
        "definition_checksum": _EMPTY_SHA,
        "row_version": 1,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    values.update(changes)
    return WorkflowRun(**values)  # type: ignore[arg-type]


def _node_run(**changes: object) -> NodeRun:
    values: dict[str, object] = {
        "id": "node-run-1",
        "workflow_run_id": "run-1",
        "workflow_node_id": "node-1",
        "node_key": "node_1",
        "status": NodeRunStatus.PENDING,
        "resolved_input": None,
        "output": None,
        "planned_task_id": None,
        "task_id": None,
        "error_code": None,
        "error_message": None,
        "row_version": 1,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    values.update(changes)
    return NodeRun(**values)  # type: ignore[arg-type]


def test_workflow_and_version_enforce_immutable_publication() -> None:
    workflow = Workflow(
        id="workflow-1",
        name="  Story Flow  ",
        description="Generate a story",
        current_version_id=None,
        row_version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )
    version, nodes, edges = _valid_definition()
    checksum = definition_checksum(version, nodes, edges)
    saved = version.with_draft_definition(
        input_schema=version.input_schema,
        output_schema=version.output_schema,
        output_bindings=version.output_bindings,
        checksum=checksum,
        at=_NOW + timedelta(seconds=1),
    )
    published = saved.publish(_NOW + timedelta(seconds=2))
    archived = published.archive(_NOW + timedelta(seconds=3))
    current = workflow.with_current_version(published.id, _NOW + timedelta(seconds=2))

    assert workflow.name == "Story Flow"
    assert current.current_version_id == published.id
    assert published.status is WorkflowVersionStatus.PUBLISHED
    assert archived.status is WorkflowVersionStatus.ARCHIVED
    with pytest.raises(WorkflowTransitionError, match="immutable"):
        published.with_draft_definition(
            input_schema={},
            output_schema={},
            output_bindings={},
            checksum=_EMPTY_SHA,
            at=_NOW,
        )
    with pytest.raises(WorkflowTransitionError, match="draft"):
        published.publish(_NOW)


def test_valid_dag_has_ports_topology_and_stable_checksum() -> None:
    version, nodes, edges = _valid_definition()

    assert validate_definition(version, nodes, edges) == ()
    assert topological_node_ids(nodes, edges) == ("node-1", "node-2")
    assert [port.name for port in ports_from_schema(nodes[0].input_schema)] == ["prompt"]
    assert definition_checksum(version, nodes, edges) == definition_checksum(
        version, tuple(reversed(nodes)), edges
    )
    assert schemas_compatible({"type": "integer"}, {"type": "number"})
    assert not schemas_compatible({"type": "number"}, {"type": "integer"})


def test_publish_validation_reports_cycles_ports_bindings_types_and_secrets() -> None:
    version, nodes, edges = _valid_definition()
    first, second = nodes
    broken_first = replace(
        first,
        config={"nested": {"api_key": "SHOULD_NOT_EXIST"}},
    )
    broken_second = replace(second, input_bindings={"missing": {"kind": "constant", "value": 1}})
    reverse = WorkflowEdge(
        id="edge-2",
        workflow_version_id=version.id,
        source_node_id=second.id,
        source_port="text",
        target_node_id=first.id,
        target_port="prompt",
    )
    duplicate_target = WorkflowEdge(
        id="edge-3",
        workflow_version_id=version.id,
        source_node_id=first.id,
        source_port="text",
        target_node_id=second.id,
        target_port="prompt",
    )
    bad_version = replace(
        version,
        output_bindings={"text": {"node": "missing", "port": "text"}},
    )

    issues = validate_definition(
        bad_version,
        (broken_first, broken_second),
        (*edges, reverse, duplicate_target),
    )
    codes = {issue.code for issue in issues}

    assert {
        "secret_in_definition",
        "cycle",
        "duplicate_input_edge",
        "input_bound_twice",
        "binding_port_missing",
        "output_node_missing",
    } <= codes
    assert contains_secret_key({"safe": [{"client-secret": "x"}]})
    assert not contains_secret_key({"max_output_tokens": 100})


def test_run_and_node_state_machines_require_outputs_and_matching_task_intent() -> None:
    run = WorkflowRun(
        id="run-1",
        workflow_id="workflow-1",
        workflow_version_id="version-1",
        status=WorkflowRunStatus.CREATED,
        input={"topic": "space"},
        output=None,
        definition_checksum=_EMPTY_SHA,
        row_version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )
    running = run.transition(WorkflowRunStatus.RUNNING, _NOW + timedelta(seconds=1))
    with pytest.raises(WorkflowTransitionError, match="output"):
        running.transition(WorkflowRunStatus.SUCCESS, _NOW + timedelta(seconds=2))
    success = running.transition(
        WorkflowRunStatus.SUCCESS,
        _NOW + timedelta(seconds=2),
        output={"text": "done"},
    )
    assert success.completed_at is not None

    node = NodeRun(
        id="node-run-1",
        workflow_run_id=run.id,
        workflow_node_id="node-1",
        node_key="first",
        status=NodeRunStatus.PENDING,
        resolved_input=None,
        output=None,
        planned_task_id=None,
        task_id=None,
        error_code=None,
        error_message=None,
        row_version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )
    ready = node.transition(NodeRunStatus.READY, _NOW + timedelta(seconds=1))
    executing = ready.transition(
        NodeRunStatus.RUNNING,
        _NOW + timedelta(seconds=2),
        resolved_input={"prompt": "space"},
        planned_task_id="task-1",
    )
    with pytest.raises(WorkflowTransitionError, match="planned"):
        executing.attach_task(_NOW, "task-other")
    attached = executing.attach_task(_NOW + timedelta(seconds=3), "task-1")
    completed = attached.transition(
        NodeRunStatus.SUCCESS,
        _NOW + timedelta(seconds=4),
        output={"text": "done"},
    )
    assert completed.task_id == "task-1"
    assert completed.status.terminal


@pytest.mark.parametrize(
    "factory",
    [
        lambda: _node("node-1", "Invalid Key"),
        lambda: WorkflowEdge(
            id="edge",
            workflow_version_id="version-1",
            source_node_id="same",
            source_port="text",
            target_node_id="same",
            target_port="prompt",
        ),
        lambda: _version(checksum="not-a-checksum"),
    ],
)
def test_invalid_definition_identity_is_rejected(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: _workflow(id=""),
        lambda: _workflow(name="x" * 161),
        lambda: _workflow(row_version=0),
        lambda: _workflow(deleted_at=datetime(2026, 7, 15)),
        lambda: _workflow().with_current_version("", _NOW),
        lambda: _version(id=""),
        lambda: _version(row_version=0),
        lambda: _version(published_at=_NOW),
        lambda: _version(status=WorkflowVersionStatus.PUBLISHED),
        lambda: _version().archive(_NOW),
        lambda: WorkflowPort("bad port", {}, False),
        lambda: replace(_node("node-1", "node"), name=" "),
        lambda: replace(_node("node-1", "node"), model_id=None),
        lambda: replace(
            _node("node-1", "node"),
            node_type=WorkflowNodeType.TRANSFORM,
        ),
        lambda: replace(_node("node-1", "node"), position_x=1_000_001),
        lambda: WorkflowEdge("", "version-1", "a", "text", "b", "prompt"),
        lambda: WorkflowEdge("edge", "version-1", "a", "bad port", "b", "prompt"),
        lambda: _run(id=""),
        lambda: _run(definition_checksum="bad"),
        lambda: _run(row_version=0),
        lambda: _run(status=WorkflowRunStatus.FAILED),
        lambda: _run(completed_at=_NOW),
        lambda: _run(status=WorkflowRunStatus.SUCCESS, completed_at=_NOW),
        lambda: _run().transition(WorkflowRunStatus.SUCCESS, _NOW, output={}),
        lambda: _run(
            status=WorkflowRunStatus.FAILED,
            completed_at=_NOW,
        ).request_cancel(_NOW),
        lambda: _node_run(id=""),
        lambda: _node_run(row_version=0),
        lambda: _node_run(status=NodeRunStatus.FAILED),
        lambda: _node_run(completed_at=_NOW),
        lambda: _node_run(status=NodeRunStatus.SUCCESS, completed_at=_NOW),
        lambda: _node_run(planned_task_id="task-1", task_id="task-2"),
        lambda: _node_run().transition(NodeRunStatus.SUCCESS, _NOW, output={}),
        lambda: _node_run().transition(
            NodeRunStatus.READY,
            _NOW,
            planned_task_id="task-1",
            task_id="task-2",
        ),
        lambda: ArtifactLink("", "node-run", "artifact", ArtifactLinkDirection.INPUT, "port", _NOW),
        lambda: ArtifactLink(
            "link", "node-run", "artifact", ArtifactLinkDirection.INPUT, "bad port", _NOW
        ),
        lambda: ArtifactLink(
            "link",
            "node-run",
            "artifact",
            ArtifactLinkDirection.INPUT,
            "port",
            datetime(2026, 7, 15),
        ),
    ],
)
def test_workflow_entities_reject_invalid_boundaries(
    factory: Callable[[], object],
) -> None:
    with pytest.raises((ValueError, WorkflowTransitionError)):
        factory()
