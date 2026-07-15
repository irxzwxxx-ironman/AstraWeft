"""Workflow runtime lifecycle, failure isolation, and bounded shutdown tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from astraweft.application.events import EventBus
from astraweft.application.workflows import (
    WorkflowExecutionService,
    WorkflowRuntimeCoordinator,
)


class RuntimeService:
    def __init__(self, *, fail_list: bool = False, fail_advance: bool = False) -> None:
        self.fail_list = fail_list
        self.fail_advance = fail_advance
        self.listed = asyncio.Event()
        self.advanced = asyncio.Event()
        self.calls = 0

    async def list_active_runs(self) -> tuple[SimpleNamespace, ...]:
        self.listed.set()
        if self.fail_list:
            raise RuntimeError("database unavailable")
        return (SimpleNamespace(id="run-1"),) if self.fail_advance else ()

    async def advance(self, _run_id: str) -> None:
        self.calls += 1
        self.advanced.set()
        if self.fail_advance:
            raise RuntimeError("one run failed")


class BlockingRuntimeService(RuntimeService):
    async def list_active_runs(self) -> tuple[SimpleNamespace, ...]:
        self.listed.set()
        await asyncio.sleep(10)
        return ()


def _runtime(
    service: RuntimeService,
    *,
    tick: float = 0.01,
    grace: float = 0.1,
) -> WorkflowRuntimeCoordinator:
    return WorkflowRuntimeCoordinator(
        cast(WorkflowExecutionService, cast(Any, service)),
        EventBus(),
        tick_seconds=tick,
        shutdown_grace_seconds=grace,
    )


@pytest.mark.parametrize(
    ("tick", "grace", "message"),
    [(0.0, 1.0, "tick_seconds"), (1.0, 0.0, "shutdown_grace_seconds")],
)
def test_workflow_runtime_rejects_non_positive_intervals(
    tick: float,
    grace: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _runtime(RuntimeService(), tick=tick, grace=grace)


@pytest.mark.asyncio
async def test_workflow_runtime_ticks_wakes_and_stops_idempotently() -> None:
    service = RuntimeService()
    runtime = _runtime(service)

    await runtime.stop()
    runtime.start()
    runtime.start()
    await service.listed.wait()
    runtime._on_change(object())
    await asyncio.sleep(0.03)
    assert runtime.running
    await runtime.stop()
    assert not runtime.running


@pytest.mark.asyncio
async def test_workflow_runtime_isolates_one_run_failure() -> None:
    service = RuntimeService(fail_advance=True)
    runtime = _runtime(service)

    runtime.start()
    await service.advanced.wait()
    await runtime.stop()

    assert service.calls >= 1
    assert not runtime.running


@pytest.mark.asyncio
async def test_workflow_runtime_contains_list_failure() -> None:
    service = RuntimeService(fail_list=True)
    runtime = _runtime(service)

    runtime.start()
    await service.listed.wait()
    for _attempt in range(20):
        if not runtime.running:
            break
        await asyncio.sleep(0)
    await runtime.stop()

    assert not runtime.running


@pytest.mark.asyncio
async def test_workflow_runtime_cancels_after_bounded_shutdown_grace() -> None:
    service = BlockingRuntimeService()
    runtime = _runtime(service, grace=0.001)

    runtime.start()
    await service.listed.wait()
    await runtime.stop()

    assert not runtime.running
