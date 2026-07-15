"""Post-commit Provider events used to refresh presentation state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class ProviderChanged:
    provider_id: str
    action: Literal["created", "updated", "deleted", "health_checked"]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ModelsSynced:
    provider_id: str
    model_count: int
    occurred_at: datetime
