"""Bounded local worker, polling coordinator, and restart lifecycle."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from astraweft.application.events import EventBus
from astraweft.application.providers import ProviderService
from astraweft.application.tasks.events import TaskChanged
from astraweft.application.tasks.service import TaskExecutionError, TaskService
from astraweft.domain.task import Task


class TaskWorker:
    """Advance tasks under global and per-Provider concurrency limits."""

    def __init__(
        self,
        service: TaskService,
        providers: ProviderService,
        *,
        global_concurrency: int = 8,
    ) -> None:
        if global_concurrency < 1:
            raise ValueError("global_concurrency must be positive")
        self._service = service
        self._providers = providers
        self._global = asyncio.Semaphore(global_concurrency)
        self._provider_limits: dict[str, asyncio.Semaphore] = {}
        self._active: set[str] = set()

    async def advance(self, task: Task) -> Task:
        if task.id in self._active:
            return await self._service.get(task.id)
        self._active.add(task.id)
        try:
            limit = await self._providers.concurrency_limit(task.provider_id)
            provider_slot = self._provider_limits.setdefault(
                task.provider_id,
                asyncio.Semaphore(limit),
            )
            async with self._global, provider_slot:
                return await self._service.run_once(task.id)
        finally:
            self._active.discard(task.id)


class PollingCoordinator:
    """Select due tasks in persisted priority order and dispatch one bounded wave."""

    def __init__(self, service: TaskService, worker: TaskWorker) -> None:
        self._service = service
        self._worker = worker

    async def run_ready(self, *, limit: int = 100) -> tuple[Task, ...]:
        ready = await self._service.list_ready(limit=limit)
        if not ready:
            return ()
        advanced = await asyncio.gather(
            *(self._worker.advance(task) for task in ready),
            return_exceptions=True,
        )
        completed: list[Task] = []
        logger = logging.getLogger("astraweft.application.tasks.runtime")
        for task, result in zip(ready, advanced, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    "task_worker_failed",
                    extra={"task_id": task.id, "error_type": type(result).__name__},
                )
                continue
            completed.append(result)
        return tuple(completed)


class TaskRuntimeCoordinator:
    """Recover once, then continuously wake the due-task coordinator."""

    def __init__(
        self,
        service: TaskService,
        providers: ProviderService,
        events: EventBus,
        *,
        global_concurrency: int = 8,
        tick_seconds: float = 0.25,
        shutdown_grace_seconds: float = 1.0,
    ) -> None:
        if tick_seconds <= 0:
            raise ValueError("tick_seconds must be positive")
        if shutdown_grace_seconds <= 0:
            raise ValueError("shutdown_grace_seconds must be positive")
        self._service = service
        self._worker = TaskWorker(
            service,
            providers,
            global_concurrency=global_concurrency,
        )
        self._polling = PollingCoordinator(service, self._worker)
        self._tick_seconds = tick_seconds
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._unsubscribe = events.subscribe(TaskChanged, self._on_task_changed)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._run(), name="astraweft-task-runtime")

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        self._unsubscribe()
        task = self._task
        if task is None:
            return
        self._stop.set()
        self._wake.set()
        # Let short SQLite operations leave their worker threads cleanly before
        # the qasync signal bridge is destroyed. Provider actions remain bounded:
        # their durable intent was persisted before execution, so a task that does
        # not stop within the grace period can be canceled and recovered next boot.
        done, _pending = await asyncio.wait(
            {task},
            timeout=self._shutdown_grace_seconds,
        )
        if task not in done:
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None

    def _on_task_changed(self, _event: TaskChanged) -> None:
        self.wake()

    async def _run(self) -> None:
        logger = logging.getLogger("astraweft.application.tasks.runtime")
        try:
            await self._service.recover_pending()
            while not self._stop.is_set():
                await self._polling.run_ready()
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self._tick_seconds)
                except TimeoutError:
                    continue
        except TaskExecutionError:
            logger.exception("task_runtime_recovery_failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("task_runtime_failed")
