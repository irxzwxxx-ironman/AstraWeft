"""Task runtime commands."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CreateTask:
    provider_id: str
    model_id: str
    operation: str
    inputs: Mapping[str, object]
    priority: int = 100
    timeout_seconds: float = 300.0
    task_id: str | None = None

    def __post_init__(self) -> None:
        if not all((self.provider_id, self.model_id, self.operation)):
            raise ValueError("任务的 Provider、模型和操作不能为空")
        if self.priority < 0:
            raise ValueError("任务优先级不能为负数")
        if not 0 < self.timeout_seconds <= 86_400:
            raise ValueError("任务超时必须在 1 秒到 24 小时之间")
        if self.task_id is not None and not self.task_id.strip():
            raise ValueError("显式任务 ID 不能为空")
