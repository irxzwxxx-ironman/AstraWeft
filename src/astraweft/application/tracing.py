"""Async-safe trace context for diagnostics and request correlation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from astraweft.ports.runtime import IdGenerator

_TRACE_ID: ContextVar[str | None] = ContextVar("astraweft_trace_id", default=None)


def current_trace_id() -> str | None:
    """Return the trace ID bound to the current async context, if any."""
    return _TRACE_ID.get()


class TraceContext:
    """Create nestable trace scopes without leaking state between tasks."""

    def __init__(self, ids: IdGenerator) -> None:
        self._ids = ids

    @contextmanager
    def start(self, trace_id: str | None = None) -> Iterator[str]:
        value = self._ids.new() if trace_id is None else trace_id.strip()
        if not value:
            raise ValueError("trace_id must not be empty")
        token = _TRACE_ID.set(value)
        try:
            yield value
        finally:
            _TRACE_ID.reset(token)
