"""Post-commit task runtime notifications."""

from dataclasses import dataclass
from datetime import datetime

from astraweft.domain.task import TaskStatus


@dataclass(frozen=True, slots=True)
class TaskChanged:
    task_id: str
    status: TaskStatus
    occurred_at: datetime
