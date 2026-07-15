"""Deterministic time and identifier boundaries."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """UTC wall time plus a monotonic duration source."""

    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class IdGenerator(Protocol):
    """Generate locally unique, persistence-safe identifiers."""

    def new(self) -> str: ...
