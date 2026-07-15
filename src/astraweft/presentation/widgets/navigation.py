"""Navigation and compact status components."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from astraweft.presentation.design_system.tokens import Colors


class NavButton(QPushButton):
    """Checkable sidebar destination."""

    def __init__(self, glyph: str, label: str, page_id: str) -> None:
        super().__init__(f"{glyph}    {label}")
        self.page_id = page_id
        self.setObjectName("NavButton")
        self.setAccessibleName(label)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(43)


class StatusPill(QFrame):
    """Status label with a semantic dot."""

    def __init__(self, text: str, color: str = Colors.TEXT_DIM) -> None:
        super().__init__()
        self.setObjectName("StatusPill")
        self.setAccessibleName(text)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 4, 9, 4)
        layout.setSpacing(6)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color}; font-size: 8px; background: transparent;")
        self._label = QLabel(text)
        self._label.setObjectName("PillText")
        layout.addWidget(dot)
        layout.addWidget(self._label)

    def set_text(self, text: str) -> None:
        self._label.setText(text)
        self.setAccessibleName(text)
