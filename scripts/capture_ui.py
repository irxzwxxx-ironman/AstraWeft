"""Render the real app shell to PNG for deterministic visual QA."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from astraweft.application.providers import CreateProvider, ProviderService
from astraweft.application.workflows import CreateWorkflow
from astraweft.bootstrap.container import build_app_context
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.provider_plugins import PluginLoadState
from astraweft.ports.secrets import SecretValue
from astraweft.presentation.design_system import apply_theme
from astraweft.presentation.main_window import MainWindow
from astraweft.presentation.pages.workflows import WorkflowPage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--page", default="dashboard")
    parser.add_argument("--language", choices=("zh_CN", "en_US"))
    parser.add_argument("--open-drawer", action="store_true")
    parser.add_argument("--seed-mock", action="store_true")
    parser.add_argument("--workflow-editor", action="store_true")
    arguments = parser.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    if not isinstance(application, QApplication):
        raise RuntimeError("an incompatible Qt application instance already exists")
    apply_theme(application)
    loop = QEventLoop(application)
    asyncio.set_event_loop(loop)
    result = 1

    with loop:
        context = loop.run_until_complete(
            build_app_context(
                arguments.data_dir,
                secret_store_override=SessionSecretStore() if arguments.seed_mock else None,
            )
        )
        if arguments.seed_mock:
            loop.run_until_complete(
                _seed_mock(
                    context.provider_service,
                    english=arguments.language == "en_US",
                )
            )
        app_settings = (
            context.settings.model_copy(update={"language": arguments.language})
            if arguments.language is not None
            else context.settings
        )
        window = MainWindow(
            context.presentation_status(),
            context.provider_service,
            context.task_service,
            context.workflow_service,
            context.workflow_execution,
            context.comfyui_service,
            context.maintenance_service,
            context.query_service,
            context.events,
            context.settings_service,
            system_notifications=False,
            language=arguments.language or context.settings.language,
            app_settings=app_settings,
        )
        window._show_page(arguments.page)
        if arguments.workflow_editor:
            snapshot = loop.run_until_complete(
                context.workflow_service.create(CreateWorkflow("Launch Content Pipeline"))
            )
            workflow_page = window._stack.widget(window._page_indexes["workflows"])
            if not isinstance(workflow_page, WorkflowPage):
                raise RuntimeError("workflow page is unavailable")
            workflow_page._load_snapshot(snapshot)
            workflow_page._add_transform_node()
        window.resize(1440, 900)
        window.show()
        if arguments.open_drawer:
            window._toggle_queue_drawer()

        def capture() -> None:
            nonlocal result
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            if arguments.page == "models":
                screen = application.primaryScreen()
                if screen is None:
                    raise RuntimeError("no screen is available for UI capture")
                pixmap = screen.grabWindow(int(window.winId()))
            else:
                pixmap = QPixmap(window.size())
                pixmap.fill(Qt.GlobalColor.transparent)
                window.render(pixmap)
            result = 0 if pixmap.save(str(arguments.output), "PNG") else 1
            application.quit()

        QTimer.singleShot(600, capture)
        loop.run_forever()
        loop.run_until_complete(context.close())
    return result


async def _seed_mock(service: ProviderService, *, english: bool = False) -> None:
    providers = await service.list_providers()
    if providers:
        return
    record = next(
        record
        for record in service.plugin_records()
        if record.state is PluginLoadState.READY
        and record.descriptor is not None
        and record.descriptor.name == "AstraWeft Mock Provider"
    )
    descriptor = record.descriptor
    if descriptor is None:
        return
    provider = await service.create(
        CreateProvider(
            plugin_id=descriptor.plugin_id,
            name="Local Mock Studio" if english else "本地 Mock Studio",
            settings={"mode": "healthy", "catalog_revision": 1},
            credentials={"api_key": SecretValue("mock-valid-key")},
        )
    )
    await service.test_connection(provider.id)
    await service.sync_models(provider.id)


if __name__ == "__main__":
    raise SystemExit(main())
