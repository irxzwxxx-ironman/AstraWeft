"""Post-commit workflow definition and execution notifications."""

from dataclasses import dataclass
from datetime import datetime

from astraweft.domain.workflow import NodeRunStatus, WorkflowRunStatus


@dataclass(frozen=True, slots=True)
class WorkflowChanged:
    workflow_id: str
    version_id: str
    action: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class WorkflowRunChanged:
    run_id: str
    status: WorkflowRunStatus
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class NodeRunChanged:
    run_id: str
    node_run_id: str
    status: NodeRunStatus
    occurred_at: datetime
