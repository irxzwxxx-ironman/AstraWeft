"""Reusable loading, empty, error, and transient feedback states."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from astraweft.presentation.widgets.controls import Button, IconButton

ToastTone = Literal["success", "warning", "danger", "info"]


class EmptyState(QWidget):
    """Honest no-data state with an optional primary action."""

    action_requested = Signal()

    def __init__(
        self,
        glyph: str,
        title: str,
        body: str,
        *,
        action_text: str | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("EmptyState")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 15, 12, 16)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel(glyph)
        icon.setObjectName("EmptyIcon")
        icon.setFixedSize(46, 46)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label = QLabel(title)
        title_label.setObjectName("EmptyTitle")
        body_label = QLabel(body)
        body_label.setObjectName("MutedText")
        body_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_label.setWordWrap(True)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(body_label)
        if action_text:
            action = Button(action_text)
            action.clicked.connect(self.action_requested)
            layout.addWidget(action, 0, Qt.AlignmentFlag.AlignCenter)


class ErrorState(QWidget):
    """Recoverable user error with a copyable trace identifier."""

    retry_requested = Signal()

    def __init__(self, message: str, trace_id: str, *, retry_text: str = "重试") -> None:
        super().__init__()
        self.setObjectName("ErrorState")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("!")
        icon.setObjectName("ErrorIcon")
        icon.setFixedSize(46, 46)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("操作未完成")
        title.setObjectName("EmptyTitle")
        body = QLabel(message)
        body.setObjectName("BodyText")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)

        trace_row = QHBoxLayout()
        trace = QLabel(f"TRACE  {trace_id}")
        trace.setObjectName("TraceText")
        copy = IconButton("⧉", "复制 trace ID")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(trace_id))
        trace_row.addWidget(trace)
        trace_row.addWidget(copy)

        retry = Button(retry_text)
        retry.clicked.connect(self.retry_requested)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(body)
        layout.addLayout(trace_row)
        layout.addWidget(retry, 0, Qt.AlignmentFlag.AlignCenter)


class SkeletonBlock(QFrame):
    """Static skeleton placeholder; intentionally animation-free."""

    def __init__(self, *, height: int = 18) -> None:
        super().__init__()
        self.setObjectName("SkeletonBlock")
        self.setAccessibleName("正在加载")
        self.setFixedHeight(height)


class Toast(QFrame):
    """Non-blocking feedback that can dismiss itself without UI animation."""

    dismissed = Signal()

    def __init__(self, text: str, *, tone: ToastTone = "info", duration_ms: int = 4000) -> None:
        super().__init__()
        if duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        self.setObjectName("Toast")
        self.setProperty("tone", tone)
        self.setAccessibleName(text)
        self._duration_ms = duration_ms
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 10, 8, 10)
        layout.setSpacing(9)
        marker = QLabel("●")
        marker.setObjectName("ToastMarker")
        message = QLabel(text)
        message.setObjectName("ToastText")
        message.setWordWrap(True)
        close = IconButton("×", "关闭通知")
        close.clicked.connect(self.dismiss)
        layout.addWidget(marker)
        layout.addWidget(message, 1)
        layout.addWidget(close)
        self.setMinimumWidth(300)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._duration_ms:
            self._timer.start(self._duration_ms)

    def dismiss(self) -> None:
        if self.isVisible():
            self._timer.stop()
            self.hide()
            self.dismissed.emit()
