"""Clock, UUID v7, and async trace context tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import RFC_4122, UUID

import pytest

from astraweft.application.tracing import TraceContext, current_trace_id
from astraweft.infrastructure.runtime import SystemClock, UUID7Generator


@dataclass
class MutableClock:
    current: datetime

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return 1.0


class SequentialIds:
    def __init__(self) -> None:
        self.value = 0

    def new(self) -> str:
        self.value += 1
        return f"trace-{self.value}"


def test_uuid7_is_valid_and_monotonic_when_clock_stalls_or_moves_back() -> None:
    clock = MutableClock(datetime(2026, 7, 15, 10, 0, tzinfo=UTC))
    generator = UUID7Generator(clock)

    values = [generator.new(), generator.new()]
    clock.current -= timedelta(seconds=1)
    values.append(generator.new())

    parsed = [UUID(value) for value in values]
    assert all(value.version == 7 for value in parsed)
    assert all(value.variant == RFC_4122 for value in parsed)
    assert values == sorted(values)


def test_uuid7_advances_with_wall_clock_and_rejects_invalid_epoch() -> None:
    clock = MutableClock(datetime(2026, 7, 15, 10, 0, tzinfo=UTC))
    generator = UUID7Generator(clock)
    first = generator.new()
    clock.current += timedelta(milliseconds=1)
    second = generator.new()
    assert first < second

    invalid = UUID7Generator(MutableClock(datetime(1900, 1, 1, tzinfo=UTC)))
    with pytest.raises(OverflowError):
        invalid.new()


def test_system_clock_is_utc_and_monotonic() -> None:
    clock = SystemClock()

    assert clock.now().tzinfo is UTC
    assert clock.monotonic() <= clock.monotonic()


def test_trace_scopes_nest_and_restore() -> None:
    traces = TraceContext(SequentialIds())
    assert current_trace_id() is None

    with traces.start() as outer:
        assert current_trace_id() == outer == "trace-1"
        with traces.start(" explicit ") as inner:
            assert inner == "explicit"
            assert current_trace_id() == "explicit"
        assert current_trace_id() == outer

    assert current_trace_id() is None
    with pytest.raises(ValueError), traces.start("  "):
        pass


@pytest.mark.asyncio
async def test_trace_context_is_isolated_between_async_tasks() -> None:
    traces = TraceContext(SequentialIds())
    ready = asyncio.Event()

    async def observe(trace_id: str) -> str | None:
        with traces.start(trace_id):
            ready.set()
            await asyncio.sleep(0)
            return current_trace_id()

    first, second = await asyncio.gather(observe("first"), observe("second"))
    assert ready.is_set()
    assert (first, second) == ("first", "second")
    assert current_trace_id() is None
