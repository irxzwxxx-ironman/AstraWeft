"""Reusable dashboard cards."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class MetricCard(QFrame):
    """Compact KPI card with an accent rail."""

    def __init__(self, label: str, value: str, foot: str, accent: str) -> None:
        super().__init__()
        self.setObjectName("MetricCard")
        self.setMinimumHeight(116)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        rail = QFrame()
        rail.setFixedWidth(3)
        rail.setStyleSheet(f"background-color: {accent}; border-radius: 1px;")
        root.addWidget(rail)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(17, 15, 16, 14)
        layout.setSpacing(4)
        label_widget = QLabel(label)
        label_widget.setObjectName("MetricLabel")
        self._value_widget = QLabel(value)
        self._value_widget.setObjectName("MetricValue")
        self._foot_widget = QLabel(foot)
        self._foot_widget.setObjectName("MetricFoot")
        layout.addWidget(label_widget)
        layout.addWidget(self._value_widget)
        layout.addStretch(1)
        layout.addWidget(self._foot_widget)
        root.addWidget(content, 1)

    def set_value(self, value: str) -> None:
        self._value_widget.setText(value)

    def set_foot(self, foot: str) -> None:
        self._foot_widget.setText(foot)


class SectionCard(QFrame):
    """Titled content card used throughout the app shell."""

    def __init__(self, title: str, meta: str = "") -> None:
        super().__init__()
        self.setObjectName("SectionCard")
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(18, 17, 18, 18)
        self.root_layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(8)
        title_widget = QLabel(title)
        title_widget.setObjectName("SectionTitle")
        header.addWidget(title_widget)
        header.addStretch(1)
        if meta:
            meta_widget = QLabel(meta)
            meta_widget.setObjectName("SectionMeta")
            header.addWidget(meta_widget)
        self.root_layout.addLayout(header)

    def add_widget(self, widget: QWidget, stretch: int = 0) -> None:
        self.root_layout.addWidget(widget, stretch)


class HealthRow(QFrame):
    """One health signal with a dot and value."""

    def __init__(self, name: str, value: str, color: str) -> None:
        super().__init__()
        self.setObjectName("HealthRow")
        self.setFixedHeight(42)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        self._dot = QLabel("●")
        self._dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._set_dot_color(color)
        name_widget = QLabel(name)
        name_widget.setObjectName("HealthName")
        self._value_widget = QLabel(value)
        self._value_widget.setObjectName("HealthValue")
        layout.addWidget(self._dot)
        layout.addWidget(name_widget)
        layout.addStretch(1)
        layout.addWidget(self._value_widget)

    def set_status(self, value: str, color: str) -> None:
        self._value_widget.setText(value)
        self.setAccessibleName(value)
        self._set_dot_color(color)

    def _set_dot_color(self, color: str) -> None:
        self._dot.setStyleSheet(f"color: {color}; font-size: 8px; background: transparent;")
