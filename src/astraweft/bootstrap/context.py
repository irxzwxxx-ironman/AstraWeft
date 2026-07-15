"""Owned process resources and presentation-safe status mapping."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from astraweft import __version__
from astraweft.application.comfyui import ComfyUIService
from astraweft.application.events import EventBus
from astraweft.application.maintenance import MaintenanceService
from astraweft.application.providers import ProviderService
from astraweft.application.query import QueryService
from astraweft.application.settings import AppSettings, SettingsService
from astraweft.application.status import ApplicationStatus
from astraweft.application.tasks import TaskRuntimeCoordinator, TaskService
from astraweft.application.tracing import TraceContext
from astraweft.application.workflows import (
    WorkflowExecutionService,
    WorkflowRuntimeCoordinator,
    WorkflowService,
)
from astraweft.infrastructure.config.paths import AppPaths
from astraweft.infrastructure.database.engine import Database
from astraweft.infrastructure.gateway import LoopbackGateway
from astraweft.infrastructure.network import CoreHttpClient
from astraweft.ports.comfyui import ComfyUIClient
from astraweft.ports.runtime import Clock, IdGenerator
from astraweft.ports.secrets import SecretStore
from astraweft.ports.unit_of_work import UnitOfWorkFactory


@dataclass(slots=True)
class AppContext:
    """Resources created by the composition root and closed as a unit."""

    paths: AppPaths
    settings: AppSettings
    settings_service: SettingsService
    database: Database
    http_client: CoreHttpClient
    secret_store: SecretStore
    events: EventBus
    clock: Clock
    ids: IdGenerator
    traces: TraceContext
    uow_factory: UnitOfWorkFactory
    provider_service: ProviderService
    comfyui_service: ComfyUIService
    comfyui_client: ComfyUIClient
    task_service: TaskService
    task_runtime: TaskRuntimeCoordinator
    query_service: QueryService
    loopback_gateway: LoopbackGateway
    workflow_service: WorkflowService
    workflow_execution: WorkflowExecutionService
    workflow_runtime: WorkflowRuntimeCoordinator
    maintenance_service: MaintenanceService
    log_path: Path
    database_online: bool
    _closed: bool = field(default=False, init=False, repr=False)

    def presentation_status(self) -> ApplicationStatus:
        return ApplicationStatus(
            database_online=self.database_online,
            credential_store_persistent=self.secret_store.persistent,
            data_directory=str(self.paths.data_dir),
            log_path=str(self.log_path),
            version=__version__,
            cache_directory=str(self.paths.cache_dir),
        )

    async def close(self) -> None:
        if self._closed:
            return
        logger = logging.getLogger("astraweft.bootstrap")
        with self.traces.start():
            logger.info("shutdown_started")
            try:
                await self.workflow_runtime.stop()
                await self.task_runtime.stop()
                await self.loopback_gateway.stop()
                await self.comfyui_client.close()
                await self.http_client.close()
                await self.database.close()
            except Exception:
                logger.exception("shutdown_failed")
                raise
            self._closed = True
            logger.info("shutdown_complete")
