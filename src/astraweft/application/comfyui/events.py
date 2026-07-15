"""Post-commit ComfyUI events used by runtime and presentation refreshes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from astraweft.domain.comfyui import ComfyUIExecutionStatus


@dataclass(frozen=True, slots=True)
class ComfyUIInstanceChanged:
    instance_id: str
    action: Literal["created", "updated", "deleted", "health_checked"]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ComfyUITemplateChanged:
    template_id: str
    instance_id: str
    action: Literal["imported", "updated"]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ComfyUIExecutionChanged:
    execution_id: str
    node_run_id: str
    status: ComfyUIExecutionStatus
    occurred_at: datetime
