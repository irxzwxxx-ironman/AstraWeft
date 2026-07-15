"""Immutable workflow definitions, runs, and node execution state machines."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from astraweft.domain.common import freeze_mapping

_NODE_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PORT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class WorkflowVersionStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class WorkflowNodeType(StrEnum):
    PROVIDER_MODEL = "PROVIDER_MODEL"
    TRANSFORM = "TRANSFORM"
    COMFYUI = "COMFYUI"
    CONDITION = "CONDITION"
    APPROVAL = "APPROVAL"


class WorkflowRunStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELED = "CANCELED"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCESS, self.FAILED, self.CANCELED}


class NodeRunStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELED = "CANCELED"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCESS, self.FAILED, self.SKIPPED, self.CANCELED}


class ArtifactLinkDirection(StrEnum):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"


class WorkflowTransitionError(ValueError):
    """A workflow or node run transition violates durable execution semantics."""


_RUN_TRANSITIONS: Mapping[WorkflowRunStatus, frozenset[WorkflowRunStatus]] = {
    WorkflowRunStatus.CREATED: frozenset({WorkflowRunStatus.RUNNING, WorkflowRunStatus.CANCELED}),
    WorkflowRunStatus.RUNNING: frozenset(
        {
            WorkflowRunStatus.WAITING,
            WorkflowRunStatus.SUCCESS,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELED,
        }
    ),
    WorkflowRunStatus.WAITING: frozenset(
        {
            WorkflowRunStatus.RUNNING,
            WorkflowRunStatus.SUCCESS,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELED,
        }
    ),
    WorkflowRunStatus.SUCCESS: frozenset(),
    WorkflowRunStatus.FAILED: frozenset(),
    WorkflowRunStatus.CANCELED: frozenset(),
}

_NODE_TRANSITIONS: Mapping[NodeRunStatus, frozenset[NodeRunStatus]] = {
    NodeRunStatus.PENDING: frozenset(
        {NodeRunStatus.READY, NodeRunStatus.SKIPPED, NodeRunStatus.CANCELED}
    ),
    NodeRunStatus.READY: frozenset(
        {
            NodeRunStatus.RUNNING,
            NodeRunStatus.SUCCESS,
            NodeRunStatus.FAILED,
            NodeRunStatus.CANCELED,
        }
    ),
    NodeRunStatus.RUNNING: frozenset(
        {
            NodeRunStatus.WAITING_APPROVAL,
            NodeRunStatus.SUCCESS,
            NodeRunStatus.FAILED,
            NodeRunStatus.CANCELED,
        }
    ),
    NodeRunStatus.WAITING_APPROVAL: frozenset(
        {
            NodeRunStatus.RUNNING,
            NodeRunStatus.SUCCESS,
            NodeRunStatus.FAILED,
            NodeRunStatus.CANCELED,
        }
    ),
    NodeRunStatus.SUCCESS: frozenset(),
    NodeRunStatus.FAILED: frozenset(),
    NodeRunStatus.SKIPPED: frozenset(),
    NodeRunStatus.CANCELED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Workflow:
    id: str
    name: str
    description: str
    current_version_id: str | None
    row_version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not self.id or not name:
            raise ValueError("workflow identity fields must not be empty")
        if len(name) > 160 or len(self.description) > 4000:
            raise ValueError("workflow text exceeds the safe length limit")
        if self.row_version < 1:
            raise ValueError("workflow row_version must be positive")
        object.__setattr__(self, "name", name)
        _require_aware(self.created_at)
        _require_aware(self.updated_at)
        if self.deleted_at is not None:
            _require_aware(self.deleted_at)

    def with_current_version(self, version_id: str, at: datetime) -> Workflow:
        _require_aware(at)
        if not version_id:
            raise ValueError("current workflow version must not be empty")
        return replace(
            self,
            current_version_id=version_id,
            updated_at=at,
            row_version=self.row_version + 1,
        )


@dataclass(frozen=True, slots=True)
class WorkflowVersion:
    id: str
    workflow_id: str
    version_no: int
    status: WorkflowVersionStatus
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    output_bindings: Mapping[str, object]
    checksum: str
    row_version: int
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.workflow_id or self.version_no < 1:
            raise ValueError("invalid workflow version identity")
        if not _SHA256.fullmatch(self.checksum):
            raise ValueError("workflow checksum must be a lowercase SHA-256 digest")
        if self.row_version < 1:
            raise ValueError("workflow version row_version must be positive")
        object.__setattr__(self, "input_schema", freeze_mapping(self.input_schema))
        object.__setattr__(self, "output_schema", freeze_mapping(self.output_schema))
        object.__setattr__(self, "output_bindings", freeze_mapping(self.output_bindings))
        _require_aware(self.created_at)
        _require_aware(self.updated_at)
        if self.published_at is not None:
            _require_aware(self.published_at)
        if self.status is WorkflowVersionStatus.DRAFT and self.published_at is not None:
            raise ValueError("draft workflow version cannot have published_at")
        if self.status is not WorkflowVersionStatus.DRAFT and self.published_at is None:
            raise ValueError("immutable workflow version requires published_at")

    def with_draft_definition(
        self,
        *,
        input_schema: Mapping[str, object],
        output_schema: Mapping[str, object],
        output_bindings: Mapping[str, object],
        checksum: str,
        at: datetime,
    ) -> WorkflowVersion:
        if self.status is not WorkflowVersionStatus.DRAFT:
            raise WorkflowTransitionError("published workflow version is immutable")
        _require_aware(at)
        return replace(
            self,
            input_schema=input_schema,
            output_schema=output_schema,
            output_bindings=output_bindings,
            checksum=checksum,
            updated_at=at,
            row_version=self.row_version + 1,
        )

    def publish(self, at: datetime) -> WorkflowVersion:
        if self.status is not WorkflowVersionStatus.DRAFT:
            raise WorkflowTransitionError("only a draft workflow version can be published")
        _require_aware(at)
        return replace(
            self,
            status=WorkflowVersionStatus.PUBLISHED,
            published_at=at,
            updated_at=at,
            row_version=self.row_version + 1,
        )

    def archive(self, at: datetime) -> WorkflowVersion:
        if self.status is not WorkflowVersionStatus.PUBLISHED:
            raise WorkflowTransitionError("only a published workflow version can be archived")
        _require_aware(at)
        return replace(
            self,
            status=WorkflowVersionStatus.ARCHIVED,
            updated_at=at,
            row_version=self.row_version + 1,
        )


@dataclass(frozen=True, slots=True)
class WorkflowPort:
    name: str
    schema: Mapping[str, object]
    required: bool

    def __post_init__(self) -> None:
        if not _PORT_NAME.fullmatch(self.name):
            raise ValueError("invalid workflow port name")
        object.__setattr__(self, "schema", freeze_mapping(self.schema))


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    id: str
    workflow_version_id: str
    node_key: str
    node_type: WorkflowNodeType
    name: str
    provider_id: str | None
    model_id: str | None
    operation: str | None
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    input_bindings: Mapping[str, object]
    config: Mapping[str, object]
    continue_on_error: bool
    position_x: int
    position_y: int

    def __post_init__(self) -> None:
        if not self.id or not self.workflow_version_id or not _NODE_KEY.fullmatch(self.node_key):
            raise ValueError("invalid workflow node identity")
        name = self.name.strip()
        if not name or len(name) > 160:
            raise ValueError("workflow node name is invalid")
        if self.node_type is WorkflowNodeType.PROVIDER_MODEL and not all(
            (self.provider_id, self.model_id, self.operation)
        ):
            raise ValueError("Provider node requires provider, model, and operation")
        if self.node_type is not WorkflowNodeType.PROVIDER_MODEL and any(
            (self.provider_id, self.model_id, self.operation)
        ):
            raise ValueError("non-Provider node cannot carry Provider identity")
        if not -1_000_000 <= self.position_x <= 1_000_000 or not (
            -1_000_000 <= self.position_y <= 1_000_000
        ):
            raise ValueError("workflow node position is outside the safe canvas")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "input_schema", freeze_mapping(self.input_schema))
        object.__setattr__(self, "output_schema", freeze_mapping(self.output_schema))
        object.__setattr__(self, "input_bindings", freeze_mapping(self.input_bindings))
        object.__setattr__(self, "config", freeze_mapping(self.config))


@dataclass(frozen=True, slots=True)
class WorkflowEdge:
    id: str
    workflow_version_id: str
    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str

    def __post_init__(self) -> None:
        if not all((self.id, self.workflow_version_id, self.source_node_id, self.target_node_id)):
            raise ValueError("workflow edge identity fields must not be empty")
        if self.source_node_id == self.target_node_id:
            raise ValueError("workflow edge cannot be a self-loop")
        if not _PORT_NAME.fullmatch(self.source_port) or not _PORT_NAME.fullmatch(self.target_port):
            raise ValueError("workflow edge port name is invalid")


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    id: str
    workflow_id: str
    workflow_version_id: str
    status: WorkflowRunStatus
    input: Mapping[str, object]
    output: Mapping[str, object] | None
    definition_checksum: str
    row_version: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancel_requested_at: datetime | None = None

    def __post_init__(self) -> None:
        if not all((self.id, self.workflow_id, self.workflow_version_id)):
            raise ValueError("workflow run identity fields must not be empty")
        if not _SHA256.fullmatch(self.definition_checksum):
            raise ValueError("workflow run checksum is invalid")
        if self.row_version < 1:
            raise ValueError("workflow run row_version must be positive")
        object.__setattr__(self, "input", freeze_mapping(self.input))
        if self.output is not None:
            object.__setattr__(self, "output", freeze_mapping(self.output))
        for value in (
            self.created_at,
            self.updated_at,
            self.started_at,
            self.completed_at,
            self.cancel_requested_at,
        ):
            if value is not None:
                _require_aware(value)
        if self.status.terminal and self.completed_at is None:
            raise ValueError("terminal workflow run requires completed_at")
        if not self.status.terminal and self.completed_at is not None:
            raise ValueError("non-terminal workflow run cannot have completed_at")
        if self.status is WorkflowRunStatus.SUCCESS and self.output is None:
            raise ValueError("successful workflow run requires output")

    def transition(
        self,
        target: WorkflowRunStatus,
        at: datetime,
        *,
        output: Mapping[str, object] | None = None,
    ) -> WorkflowRun:
        _require_aware(at)
        if target not in _RUN_TRANSITIONS[self.status]:
            raise WorkflowTransitionError(
                f"invalid workflow run transition: {self.status} -> {target}"
            )
        effective_output = self.output if output is None else output
        if target is WorkflowRunStatus.SUCCESS and effective_output is None:
            raise WorkflowTransitionError("successful workflow run requires output")
        return replace(
            self,
            status=target,
            output=effective_output,
            started_at=at
            if target is WorkflowRunStatus.RUNNING and self.started_at is None
            else self.started_at,
            completed_at=at if target.terminal else None,
            updated_at=at,
            row_version=self.row_version + 1,
        )

    def request_cancel(self, at: datetime) -> WorkflowRun:
        if self.status.terminal:
            raise WorkflowTransitionError("terminal workflow run cannot be canceled")
        _require_aware(at)
        return replace(
            self,
            cancel_requested_at=at,
            updated_at=at,
            row_version=self.row_version + 1,
        )


@dataclass(frozen=True, slots=True)
class NodeRun:
    id: str
    workflow_run_id: str
    workflow_node_id: str
    node_key: str
    status: NodeRunStatus
    resolved_input: Mapping[str, object] | None
    output: Mapping[str, object] | None
    planned_task_id: str | None
    task_id: str | None
    error_code: str | None
    error_message: str | None
    row_version: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    planned_comfyui_execution_id: str | None = None
    comfyui_execution_id: str | None = None

    def __post_init__(self) -> None:
        if not all((self.id, self.workflow_run_id, self.workflow_node_id)) or not (
            _NODE_KEY.fullmatch(self.node_key)
        ):
            raise ValueError("invalid node run identity")
        if self.row_version < 1:
            raise ValueError("node run row_version must be positive")
        if self.resolved_input is not None:
            object.__setattr__(self, "resolved_input", freeze_mapping(self.resolved_input))
        if self.output is not None:
            object.__setattr__(self, "output", freeze_mapping(self.output))
        for value in (self.created_at, self.updated_at, self.started_at, self.completed_at):
            if value is not None:
                _require_aware(value)
        if self.status.terminal and self.completed_at is None:
            raise ValueError("terminal node run requires completed_at")
        if not self.status.terminal and self.completed_at is not None:
            raise ValueError("non-terminal node run cannot have completed_at")
        if self.status is NodeRunStatus.SUCCESS and self.output is None:
            raise ValueError("successful node run requires output")
        if self.task_id is not None and self.planned_task_id != self.task_id:
            raise ValueError("node run task must match its persisted execution intent")
        if (
            self.comfyui_execution_id is not None
            and self.planned_comfyui_execution_id != self.comfyui_execution_id
        ):
            raise ValueError("node run ComfyUI execution must match its persisted intent")
        if self.planned_task_id is not None and self.planned_comfyui_execution_id is not None:
            raise ValueError("node run cannot plan Provider and ComfyUI execution together")

    def transition(
        self,
        target: NodeRunStatus,
        at: datetime,
        *,
        resolved_input: Mapping[str, object] | None = None,
        output: Mapping[str, object] | None = None,
        planned_task_id: str | None = None,
        task_id: str | None = None,
        planned_comfyui_execution_id: str | None = None,
        comfyui_execution_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> NodeRun:
        _require_aware(at)
        if target not in _NODE_TRANSITIONS[self.status]:
            raise WorkflowTransitionError(f"invalid node run transition: {self.status} -> {target}")
        effective_output = self.output if output is None else output
        if target is NodeRunStatus.SUCCESS and effective_output is None:
            raise WorkflowTransitionError("successful node run requires output")
        effective_planned = planned_task_id or self.planned_task_id
        effective_task = task_id or self.task_id
        if effective_task is not None and effective_task != effective_planned:
            raise WorkflowTransitionError("node task does not match planned execution identity")
        effective_comfyui_planned = (
            planned_comfyui_execution_id or self.planned_comfyui_execution_id
        )
        effective_comfyui = comfyui_execution_id or self.comfyui_execution_id
        if effective_comfyui is not None and effective_comfyui != effective_comfyui_planned:
            raise WorkflowTransitionError(
                "node ComfyUI execution does not match planned execution identity"
            )
        if effective_planned is not None and effective_comfyui_planned is not None:
            raise WorkflowTransitionError(
                "node cannot plan Provider and ComfyUI execution together"
            )
        return replace(
            self,
            status=target,
            resolved_input=self.resolved_input if resolved_input is None else resolved_input,
            output=effective_output,
            planned_task_id=effective_planned,
            task_id=effective_task,
            planned_comfyui_execution_id=effective_comfyui_planned,
            comfyui_execution_id=effective_comfyui,
            error_code=error_code,
            error_message=error_message,
            started_at=at
            if target is NodeRunStatus.RUNNING and self.started_at is None
            else self.started_at,
            completed_at=at if target.terminal else None,
            updated_at=at,
            row_version=self.row_version + 1,
        )

    def attach_task(self, at: datetime, task_id: str) -> NodeRun:
        if self.status is not NodeRunStatus.RUNNING or self.planned_task_id != task_id:
            raise WorkflowTransitionError("task can only attach to its running planned node")
        _require_aware(at)
        return replace(
            self,
            task_id=task_id,
            updated_at=at,
            row_version=self.row_version + 1,
        )

    def attach_comfyui_execution(self, at: datetime, execution_id: str) -> NodeRun:
        if (
            self.status is not NodeRunStatus.RUNNING
            or self.planned_comfyui_execution_id != execution_id
        ):
            raise WorkflowTransitionError(
                "ComfyUI execution can only attach to its running planned node"
            )
        _require_aware(at)
        return replace(
            self,
            comfyui_execution_id=execution_id,
            updated_at=at,
            row_version=self.row_version + 1,
        )


@dataclass(frozen=True, slots=True)
class ArtifactLink:
    id: str
    node_run_id: str
    artifact_id: str
    direction: ArtifactLinkDirection
    port_name: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not all((self.id, self.node_run_id, self.artifact_id)):
            raise ValueError("artifact link identity fields must not be empty")
        if not _PORT_NAME.fullmatch(self.port_name):
            raise ValueError("artifact link port name is invalid")
        _require_aware(self.created_at)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
