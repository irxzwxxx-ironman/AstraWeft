"""Native desktop notifications driven by post-commit task events."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QApplication, QStyle, QSystemTrayIcon, QWidget

from astraweft.application.events import EventBus
from astraweft.application.tasks import TaskChanged
from astraweft.domain.task import TaskStatus
from astraweft.presentation.i18n import Translator


class DesktopNotificationController:
    """Own the system-tray notifier and release its event subscription."""

    def __init__(
        self,
        events: EventBus,
        parent: QWidget,
        *,
        enabled: bool = True,
        available: bool | None = None,
        translator: Translator | None = None,
    ) -> None:
        self._enabled = enabled
        self._translator = translator or Translator()
        self._tray = QSystemTrayIcon(parent)
        style = QApplication.style()
        if style is not None:
            self._tray.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        self._tray.setToolTip(self._translator.text("AstraWeft · 星纬", "AstraWeft"))
        self._available = (
            QSystemTrayIcon.isSystemTrayAvailable() if available is None else available
        )
        if self._enabled and self._available:
            self._tray.show()
        self._unsubscribe: Callable[[], None] | None = events.subscribe(
            TaskChanged,
            self._on_task_changed,
        )

    def _on_task_changed(self, event: TaskChanged) -> None:
        if not self._enabled or not self._available or not event.status.terminal:
            return
        title, body, icon = _notification(event.status, event.task_id, self._translator)
        self._tray.showMessage(title, body, icon, 6000)

    def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._tray.hide()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if enabled and self._available:
            self._tray.show()
        else:
            self._tray.hide()


def _notification(
    status: TaskStatus,
    task_id: str,
    translator: Translator,
) -> tuple[str, str, QSystemTrayIcon.MessageIcon]:
    short_id = task_id[:12]
    if status is TaskStatus.SUCCESS:
        return (
            translator.text("任务已完成", "Task completed"),
            translator.text(
                "任务 {id} 运行成功，产物已写入本机。",
                "Task {id} completed; artifacts were saved locally.",
                id=short_id,
            ),
            QSystemTrayIcon.MessageIcon.Information,
        )
    if status is TaskStatus.CANCELED:
        return (
            translator.text("任务已取消", "Task canceled"),
            translator.text(
                "任务 {id} 已停止。",
                "Task {id} stopped.",
                id=short_id,
            ),
            QSystemTrayIcon.MessageIcon.Information,
        )
    if status is TaskStatus.NEEDS_ATTENTION:
        return (
            translator.text("任务需要处理", "Task needs attention"),
            translator.text(
                "任务 {id} 无法自动恢复，请打开任务中心。",
                "Task {id} could not recover automatically. Open Task Center.",
                id=short_id,
            ),
            QSystemTrayIcon.MessageIcon.Critical,
        )
    return (
        translator.text("任务未完成", "Task did not complete"),
        translator.text(
            "任务 {id} 以 {status} 结束，请查看任务详情。",
            "Task {id} ended as {status}. Review its details.",
            id=short_id,
            status=status.value,
        ),
        QSystemTrayIcon.MessageIcon.Warning,
    )
