"""Persistence-neutral workflow editing and import command values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from astraweft.domain.workflow import WorkflowNodeType


@dataclass(frozen=True, slots=True)
class CreateWorkflow:
    name: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowNodeDraft:
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
    continue_on_error: bool = False
    position_x: int = 0
    position_y: int = 0


@dataclass(frozen=True, slots=True)
class WorkflowEdgeDraft:
    source_node: str
    source_port: str
    target_node: str
    target_port: str


@dataclass(frozen=True, slots=True)
class SaveWorkflowDraft:
    version_id: str
    expected_row_version: int
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    output_bindings: Mapping[str, object]
    nodes: tuple[WorkflowNodeDraft, ...]
    edges: tuple[WorkflowEdgeDraft, ...]


@dataclass(frozen=True, slots=True)
class ImportWorkflow:
    document: bytes | str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class StartWorkflowRun:
    version_id: str
    inputs: Mapping[str, object]
