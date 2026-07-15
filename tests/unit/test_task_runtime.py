"""Task runtime lifecycle boundaries."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from astraweft.application.events import EventBus
from astraweft.application.providers import ProviderService
from astraweft.application.tasks import TaskRuntimeCoordinator, TaskService


class DelayedRecoveryService:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.started = asyncio.Event()
        self.completed = False
        self.canceled = False

    async def recover_pending(self) -> tuple[()]:
        self.started.set()
        try:
            await asyncio.sleep(self.delay)
            self.completed = True
        except asyncio.CancelledError:
            self.canceled = True
            raise
        return ()


def _runtime(service: DelayedRecoveryService, *, grace: float) -> TaskRuntimeCoordinator:
    return TaskRuntimeCoordinator(
        cast(TaskService, cast(Any, service)),
        cast(ProviderService, cast(Any, object())),
        EventBus(),
        shutdown_grace_seconds=grace,
    )


@pytest.mark.asyncio
async def test_runtime_allows_short_database_recovery_to_finish_on_shutdown() -> None:
    service = DelayedRecoveryService(0.01)
    runtime = _runtime(service, grace=0.5)

    runtime.start()
    await service.started.wait()
    await runtime.stop()

    assert service.completed
    assert not service.canceled
    assert not runtime.running


@pytest.mark.asyncio
async def test_runtime_cancels_after_bounded_shutdown_grace() -> None:
    service = DelayedRecoveryService(10.0)
    runtime = _runtime(service, grace=0.001)

    runtime.start()
    await service.started.wait()
    await runtime.stop()

    assert service.canceled
    assert not runtime.running


@pytest.mark.parametrize("grace", [0.0, -1.0])
def test_runtime_rejects_non_positive_shutdown_grace(grace: float) -> None:
    with pytest.raises(ValueError, match="shutdown_grace_seconds"):
        _runtime(DelayedRecoveryService(0.0), grace=grace)
