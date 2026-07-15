"""GUI shell behavior tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QSystemTrayIcon
from pytestqt.qtbot import QtBot

from astraweft.application.events import EventBus
from astraweft.application.status import ApplicationStatus
from astraweft.application.tasks import TaskChanged
from astraweft.domain.task import TaskStatus
from astraweft.presentation.design_system import apply_theme
from astraweft.presentation.main_window import CommandPalette, MainWindow
from astraweft.presentation.notifications import DesktopNotificationController
from astraweft.presentation.pages.dashboard import DashboardPage


@pytest.fixture
def status(tmp_path: Path) -> ApplicationStatus:
    return ApplicationStatus(
        database_online=True,
        credential_store_persistent=False,
        data_directory=str(tmp_path / "data"),
        log_path=str(tmp_path / "logs" / "astraweft.jsonl"),
        version="0.1-test",
    )


@pytest.mark.gui
def test_theme_and_main_window_render(qtbot: QtBot, status: ApplicationStatus) -> None:
    application = QApplication.instance()
    assert isinstance(application, QApplication)
    apply_theme(application)
    window = MainWindow(status)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)

    assert window.windowTitle().startswith("AstraWeft")
    assert window.minimumWidth() == 1180
    assert "#090B10" in application.styleSheet()
    assert window.findChild(QLineEdit, "GlobalSearch") is not None
    title = window.findChild(QLabel, "PageTitle")
    assert title is not None
    assert title.text() == "概览"
    assert window._buttons["dashboard"].accessibleName() == "概览"
    assert window._buttons["dashboard"].nextInFocusChain() is window._buttons["playground"]


@pytest.mark.gui
def test_sidebar_and_dashboard_action_navigate(qtbot: QtBot, status: ApplicationStatus) -> None:
    window = MainWindow(status)
    qtbot.addWidget(window)
    window.show()

    window._buttons["models"].click()
    title = window.findChild(QLabel, "PageTitle")
    assert title is not None
    assert title.text() == "模型"
    assert window._buttons["models"].isChecked()

    window._show_page("dashboard")
    dashboard = window._stack.currentWidget()
    assert isinstance(dashboard, DashboardPage)
    dashboard._apply_summary(
        calls=0,
        terminal=0,
        successes=0,
        running=0,
        known=(),
        unknown=0,
        provider_count=0,
        enabled=0,
        healthy=0,
        artifact_count=0,
        artifact_size=0,
    )
    assert dashboard._hero_destination == "providers"
    dashboard._hero_action.click()
    assert title.text() == "Provider"
    assert window._buttons["providers"].isChecked()

    previous_index = window._stack.currentIndex()
    window._show_page("missing")
    assert window._stack.currentIndex() == previous_index

    assert window._queue_drawer.isHidden()
    window._queue_button.click()
    assert window._queue_drawer.isVisible()
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert window._queue_drawer.isHidden()

    window.showMinimized()
    window.activate_from_external_instance()
    assert not window.isMinimized()


@pytest.mark.gui
def test_degraded_status_is_presented_without_fake_health(qtbot: QtBot, tmp_path: Path) -> None:
    status = ApplicationStatus(
        database_online=False,
        credential_store_persistent=True,
        data_directory=str(tmp_path),
        log_path=str(tmp_path / "log.jsonl"),
        version="dev",
    )
    window = MainWindow(status)
    qtbot.addWidget(window)

    labels = [label.text() for label in window.findChildren(QLabel)]
    assert "本地数据库异常" in labels
    assert "系统密钥环" in labels


@pytest.mark.gui
def test_english_localization_covers_shell_dashboard_and_commands(
    qtbot: QtBot,
    status: ApplicationStatus,
) -> None:
    window = MainWindow(status, language="en_US")
    qtbot.addWidget(window)
    window.show()

    title = window.findChild(QLabel, "PageTitle")
    assert title is not None and title.text() == "Overview"
    assert window._buttons["dashboard"].accessibleName() == "Overview"
    assert "creative workspace is ready" in " ".join(
        label.text() for label in window.findChildren(QLabel)
    )
    window._command_palette.open_with_query("cost")
    labels = [
        window._command_palette._list.item(index).text()
        for index in range(window._command_palette._list.count())
    ]
    assert "Open Cost Analysis" in labels


@pytest.mark.gui
def test_command_palette_filters_and_navigates_without_mouse(
    qtbot: QtBot,
    status: ApplicationStatus,
) -> None:
    window = MainWindow(status)
    qtbot.addWidget(window)
    window.show()

    window._search.setText("日志")
    window._search.returnPressed.emit()
    palette = window.findChild(CommandPalette, "CommandPalette")
    assert palette is not None and palette.isVisible()
    assert palette._list.count() == 1
    palette._activate_current()

    title = window.findChild(QLabel, "PageTitle")
    assert title is not None and title.text() == "调用日志"
    palette.open_with_query("不存在的命令")
    assert palette._list.count() == 0
    palette._activate_current()


@pytest.mark.gui
@pytest.mark.asyncio
async def test_native_notifications_only_report_terminal_task_events(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = QLabel()
    qtbot.addWidget(parent)
    messages: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        QSystemTrayIcon,
        "showMessage",
        lambda _self, *args: messages.append(args),
    )
    events = EventBus()
    controller = DesktopNotificationController(events, parent, available=True)
    now = datetime.now(UTC)

    await events.publish(TaskChanged("task-running", TaskStatus.RUNNING, now))
    assert messages == []
    controller.set_enabled(False)
    await events.publish(TaskChanged("task-muted", TaskStatus.SUCCESS, now))
    assert messages == []
    controller.set_enabled(True)
    await events.publish(TaskChanged("task-success", TaskStatus.SUCCESS, now))
    assert len(messages) == 1
    assert messages[0][0] == "任务已完成"

    controller.close()
    await events.publish(TaskChanged("task-failed", TaskStatus.FAILED, now))
    assert len(messages) == 1
