"""Qt/qasync application lifecycle."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QLocale, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox
from qasync import QEventLoop

from astraweft.bootstrap.container import build_app_context
from astraweft.bootstrap.context import AppContext
from astraweft.bootstrap.single_instance import InstanceOutcome, SingleInstanceCoordinator
from astraweft.infrastructure.config import resolve_app_paths
from astraweft.presentation.design_system import apply_theme
from astraweft.presentation.main_window import MainWindow


def run_desktop(
    data_root: Path | None = None,
    *,
    quit_after_ms: int | None = None,
    gateway_port_override: int | None = None,
) -> int:
    """Start AstraWeft and own all process resources until Qt quits."""
    application = QApplication.instance()
    if application is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        application = QApplication(sys.argv)
    if not isinstance(application, QApplication):
        raise RuntimeError("an incompatible Qt application instance already exists")

    application.setApplicationName("AstraWeft")
    application.setApplicationDisplayName("AstraWeft · 星纬")
    application.setOrganizationName("AstraWeft")
    apply_theme(application)

    instance: SingleInstanceCoordinator | None = None
    try:
        paths = resolve_app_paths(data_root)
        paths.ensure()
        instance = SingleInstanceCoordinator(paths.cache_dir, paths.data_dir)
        outcome = instance.start()
    except Exception as exc:
        if instance is not None:
            instance.close()
        QMessageBox.critical(
            None,
            "AstraWeft 启动失败",
            f"无法建立安全的本地实例。\n\n{type(exc).__name__}: {exc}",
        )
        return 1
    if outcome is not InstanceOutcome.PRIMARY:
        if outcome is InstanceOutcome.EXISTING_UNREACHABLE:
            QMessageBox.information(
                None,
                "AstraWeft 已在运行",
                "检测到同一数据目录正在被另一个实例使用，但无法激活其窗口。",
            )
        instance.close()
        return 0

    loop = QEventLoop(application)
    asyncio.set_event_loop(loop)
    context: AppContext | None = None
    try:
        with loop:
            try:
                context = loop.run_until_complete(
                    build_app_context(
                        data_root,
                        gateway_port_override=gateway_port_override,
                    )
                )
                QLocale.setDefault(QLocale(getattr(context.settings, "language", "zh_CN")))
                apply_theme(
                    application,
                    theme=context.settings.theme,
                    reduce_motion=context.settings.reduce_motion,
                )
                context.task_runtime.start()
                context.workflow_runtime.start()
                gateway = getattr(context, "loopback_gateway", None)
                if gateway is not None:
                    with context.traces.start():
                        try:
                            loop.run_until_complete(gateway.start())
                            logging.getLogger("astraweft.bootstrap").info(
                                "loopback_gateway_ready",
                                extra={
                                    "secure_storage_persistent": context.secret_store.persistent,
                                    "port": gateway.bound_port,
                                },
                            )
                        except Exception:
                            logging.getLogger("astraweft.bootstrap").exception(
                                "loopback_gateway_start_failed"
                            )
                window = MainWindow(
                    context.presentation_status(),
                    context.provider_service,
                    context.task_service,
                    context.workflow_service,
                    context.workflow_execution,
                    getattr(context, "comfyui_service", None),
                    getattr(context, "maintenance_service", None),
                    getattr(context, "query_service", None),
                    getattr(context, "events", None),
                    getattr(context, "settings_service", None),
                    system_notifications=getattr(
                        context.settings,
                        "system_notifications",
                        True,
                    ),
                    language=getattr(context.settings, "language", "zh_CN"),
                    app_settings=context.settings,
                )
                instance.set_activation_handler(window.activate_from_external_instance)
                window.show()
                application.aboutToQuit.connect(loop.stop)
                if quit_after_ms is not None:
                    QTimer.singleShot(quit_after_ms, application.quit)
                loop.run_forever()
            finally:
                if context is not None:
                    loop.run_until_complete(context.close())
        return 0
    except Exception as exc:
        logging.getLogger("astraweft.bootstrap").exception("desktop_runtime_failed")
        QMessageBox.critical(
            None,
            "AstraWeft 启动失败",
            f"本地核心未能安全启动。\n\n{type(exc).__name__}: {exc}",
        )
        return 1
    finally:
        instance.close()
