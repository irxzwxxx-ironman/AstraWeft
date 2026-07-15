"""Event-driven workflow recovery and scheduling lifecycle."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from astraweft.application.comfyui.events import ComfyUIExecutionChanged
from astraweft.application.events import EventBus
from astraweft.application.tasks import TaskChanged
from astraweft.application.workflows.events import NodeRunChanged, WorkflowRunChanged
from astraweft.application.workflows.execution import WorkflowExecutionService


class WorkflowRuntimeCoordinator:
    """Recover active runs and advance them in bounded, non-overlapping waves."""

    def __init__(
        self,
        service: WorkflowExecutionService,
        events: EventBus,
        *,
        tick_seconds: float = 0.25,
        shutdown_grace_seconds: float = 1.0,
    ) -> None:
        if tick_seconds <= 0:
            raise ValueError("tick_seconds must be positive")
        if shutdown_grace_seconds <= 0:
            raise ValueError("shutdown_grace_seconds must be positive")
        self._service = service
        self._tick_seconds = tick_seconds
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._unsubscribers = (
            events.subscribe(ComfyUIExecutionChanged, self._on_change),
            events.subscribe(TaskChanged, self._on_change),
            events.subscribe(NodeRunChanged, self._on_change),
            events.subscribe(WorkflowRunChanged, self._on_change),
        )

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._task = asyncio.get_event_loop().create_task(
            self._run(),
            name="astraweft-workflow-runtime",
        )

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        task = self._task
        if task is None:
            return
        self._stop.set()
        self._wake.set()
        done, _pending = await asyncio.wait({task}, timeout=self._shutdown_grace_seconds)
        if task not in done:
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None

    def _on_change(self, _event: object) -> None:
        self.wake()

    async def _run(self) -> None:
        logger = logging.getLogger("astraweft.application.workflows.runtime")
        try:
            while not self._stop.is_set():
                runs = await self._service.list_active_runs()
                for run in runs:
                    try:
                        await self._service.advance(run.id)
                    except Exception:
                        logger.exception(
                            "workflow_advance_failed",
                            extra={"workflow_run_id": run.id},
                        )
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self._tick_seconds)
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("workflow_runtime_failed")
