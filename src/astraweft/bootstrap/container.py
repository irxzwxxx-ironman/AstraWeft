"""Phase 1 dependency composition."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from astraweft import __version__
from astraweft.application.comfyui import ComfyUIService
from astraweft.application.events import EventBus
from astraweft.application.maintenance import MaintenanceService
from astraweft.application.providers import ProviderService
from astraweft.application.query import QueryService
from astraweft.application.settings import SettingsService
from astraweft.application.tasks import TaskRuntimeCoordinator, TaskService
from astraweft.application.tracing import TraceContext
from astraweft.application.workflows import (
    WorkflowExecutionService,
    WorkflowRuntimeCoordinator,
    WorkflowService,
)
from astraweft.bootstrap.context import AppContext
from astraweft.infrastructure.artifacts import LocalArtifactWriter
from astraweft.infrastructure.comfyui import AioHttpComfyUIClient
from astraweft.infrastructure.config import (
    SettingsPluginPreferenceStore,
    SettingsStore,
    resolve_app_paths,
)
from astraweft.infrastructure.database import (
    Database,
    SQLiteComfyUIUnitOfWorkFactory,
    SQLiteProviderUnitOfWorkFactory,
    SQLiteQueryAdapter,
    SQLiteTaskUnitOfWorkFactory,
    SQLiteUnitOfWorkFactory,
    SQLiteWorkflowUnitOfWorkFactory,
    database_revision,
    latest_revision,
    run_migrations,
)
from astraweft.infrastructure.gateway import LoopbackGateway
from astraweft.infrastructure.maintenance import LocalMaintenanceAdapter
from astraweft.infrastructure.network import CoreHttpClient
from astraweft.infrastructure.observability import configure_logging
from astraweft.infrastructure.providers import (
    CoreProviderContextFactory,
    EntryPointProviderRegistry,
)
from astraweft.infrastructure.runtime import SystemClock, UUID7Generator
from astraweft.infrastructure.secrets import create_secret_store
from astraweft.ports.comfyui import ComfyUIClient
from astraweft.ports.secrets import SecretStore
from astraweft_provider_sdk import PLUGIN_API_VERSION


async def build_app_context(
    override_root: Path | None = None,
    *,
    secret_store_override: SecretStore | None = None,
    http_client_override: CoreHttpClient | None = None,
    comfyui_client_override: ComfyUIClient | None = None,
    gateway_port_override: int | None = None,
) -> AppContext:
    """Create and verify local resources before the main window is shown."""
    paths = resolve_app_paths(override_root)
    paths.ensure()
    settings_store = SettingsStore(paths.settings_path)
    settings = settings_store.load()
    log_path = configure_logging(paths.log_dir, settings.log_level)
    logger = logging.getLogger("astraweft.bootstrap")
    clock = SystemClock()
    ids = UUID7Generator(clock)
    traces = TraceContext(ids)
    with traces.start():
        logger.info("bootstrap_started", extra={"data_dir": str(paths.data_dir)})

        expected_revision = await asyncio.to_thread(latest_revision, paths.database_path)
        maintenance_adapter = LocalMaintenanceAdapter(
            paths,
            settings,
            app_version=__version__,
            expected_revision=expected_revision,
        )
        restored_backup = await asyncio.to_thread(maintenance_adapter.apply_pending_restore)
        if restored_backup is not None:
            logger.info(
                "pending_restore_applied",
                extra={"safety_backup": restored_backup.path.name},
            )
        current_revision = await asyncio.to_thread(database_revision, paths.database_path)
        if current_revision is not None and current_revision != expected_revision:
            migration_backup = await asyncio.to_thread(
                maintenance_adapter.create_backup,
                reason="pre-migration",
            )
            logger.info(
                "pre_migration_backup_created",
                extra={
                    "backup": migration_backup.path.name,
                    "from_revision": current_revision,
                    "to_revision": expected_revision,
                },
            )
        await asyncio.to_thread(run_migrations, paths.database_path)
        database_health = await asyncio.to_thread(maintenance_adapter.check_database)
        if not database_health.healthy:
            raise RuntimeError("database integrity verification failed after migration")
        database = Database(paths.database_path)
        try:
            database_online = await database.ping()
        except Exception:
            await database.close()
            logger.exception("database_startup_failed")
            raise

        secret_store = secret_store_override or create_secret_store()
        http_client = http_client_override or CoreHttpClient(user_agent=f"AstraWeft/{__version__}")
        events = EventBus()
        plugin_preferences = SettingsPluginPreferenceStore(settings_store)
        plugins = EntryPointProviderRegistry(
            disabled_plugins=frozenset(settings.disabled_provider_plugins)
        )
        plugin_records = await asyncio.to_thread(plugins.discover)
        for record in plugin_records:
            if record.diagnostic is not None:
                logger.warning(
                    "provider_plugin_unavailable",
                    extra={
                        "entry_point": record.entry_point_name,
                        "plugin_state": record.state.value,
                        "diagnostic": record.diagnostic,
                    },
                )
        provider_service = ProviderService(
            plugins=plugins,
            uow_factory=SQLiteProviderUnitOfWorkFactory(database.sessions, events),
            secret_store=secret_store,
            clock=clock,
            ids=ids,
            provider_contexts=CoreProviderContextFactory(
                secret_store=secret_store,
                clock=clock,
                plugin_data_root=paths.data_dir / "plugins",
                core_version=__version__,
                plugin_api_version=PLUGIN_API_VERSION,
                http_client=http_client,
            ),
            plugin_preferences=plugin_preferences,
        )
        artifact_writer = LocalArtifactWriter(
            paths.artifact_dir,
            trash_root=paths.trash_dir,
            downloader=http_client,
        )
        comfyui_client = comfyui_client_override or AioHttpComfyUIClient(
            user_agent=f"AstraWeft/{__version__}"
        )
        comfyui_service = ComfyUIService(
            uow_factory=SQLiteComfyUIUnitOfWorkFactory(database.sessions, events),
            client=comfyui_client,
            artifacts=artifact_writer,
            clock=clock,
            ids=ids,
        )
        task_service = TaskService(
            providers=provider_service,
            uow_factory=SQLiteTaskUnitOfWorkFactory(database.sessions, events),
            artifacts=artifact_writer,
            artifact_lifecycle=artifact_writer,
            clock=clock,
            ids=ids,
            traces=traces,
        )
        purged_logs = await task_service.purge_request_logs(
            retention_days=settings.request_log_retention_days
        )
        purged_artifacts = await task_service.purge_expired_artifacts(
            retention_days=settings.artifact_trash_retention_days
        )
        if purged_logs or purged_artifacts:
            logger.info(
                "retention_applied",
                extra={
                    "request_logs": purged_logs,
                    "artifacts": purged_artifacts,
                },
            )
        task_runtime = TaskRuntimeCoordinator(task_service, provider_service, events)
        query_service = QueryService(SQLiteQueryAdapter(database.sessions))
        loopback_gateway = LoopbackGateway(
            tasks=task_service,
            providers=provider_service,
            secrets_store=secret_store,
            artifact_root=paths.artifact_dir,
            port=17493 if gateway_port_override is None else gateway_port_override,
        )
        workflow_uow_factory = SQLiteWorkflowUnitOfWorkFactory(database.sessions, events)
        workflow_service = WorkflowService(
            uow_factory=workflow_uow_factory,
            providers=provider_service,
            comfyui=comfyui_service,
            clock=clock,
            ids=ids,
        )
        workflow_execution = WorkflowExecutionService(
            uow_factory=workflow_uow_factory,
            tasks=task_service,
            comfyui=comfyui_service,
            clock=clock,
            ids=ids,
        )
        workflow_runtime = WorkflowRuntimeCoordinator(workflow_execution, events)
        maintenance_service = MaintenanceService(maintenance_adapter)
        settings_service = SettingsService(settings_store)
        logger.info(
            "bootstrap_ready",
            extra={
                "database_online": database_online,
                "keyring_backend_persistent": secret_store.persistent,
            },
        )
        return AppContext(
            paths=paths,
            settings=settings,
            settings_service=settings_service,
            database=database,
            http_client=http_client,
            secret_store=secret_store,
            events=events,
            clock=clock,
            ids=ids,
            traces=traces,
            uow_factory=SQLiteUnitOfWorkFactory(database.sessions, events),
            provider_service=provider_service,
            comfyui_service=comfyui_service,
            comfyui_client=comfyui_client,
            task_service=task_service,
            task_runtime=task_runtime,
            query_service=query_service,
            loopback_gateway=loopback_gateway,
            workflow_service=workflow_service,
            workflow_execution=workflow_execution,
            workflow_runtime=workflow_runtime,
            maintenance_service=maintenance_service,
            log_path=log_path,
            database_online=database_online,
        )
