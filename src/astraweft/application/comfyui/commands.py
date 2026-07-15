"""Validated command values for ComfyUI configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CreateComfyUIInstance:
    name: str
    base_url: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class UpdateComfyUIInstance:
    instance_id: str
    name: str
    base_url: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class ImportComfyUITemplate:
    instance_id: str
    name: str
    prompt: Mapping[str, object]
    input_schema: Mapping[str, object]
    input_targets: Mapping[str, object]
    output_nodes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EnsureComfyUIExecution:
    execution_id: str
    node_run_id: str
    instance_id: str
    template_id: str | None
    template_checksum: str
    workflow_checksum: str
    prompt: Mapping[str, object]
    output_nodes: tuple[str, ...]
    input_targets: Mapping[str, object]
    inputs: Mapping[str, object]
    timeout_seconds: float = 24 * 60 * 60
