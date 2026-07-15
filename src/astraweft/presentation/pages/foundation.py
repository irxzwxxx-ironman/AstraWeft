"""Honest empty states for modules scheduled after the Phase 1 foundation."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget


class FoundationPage(QWidget):
    """Explain a not-yet-configured module without presenting fake data."""

    def __init__(
        self,
        glyph: str,
        title: str,
        body: str,
        action: str,
        *,
        enabled: bool = False,
    ) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 32)
        root.addStretch(1)

        card = QFrame()
        card.setObjectName("FoundationCard")
        card.setMaximumWidth(660)
        card.setMinimumHeight(330)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(44, 42, 44, 42)
        layout.setSpacing(13)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel(glyph)
        icon.setObjectName("FoundationGlyph")
        icon.setFixedSize(62, 62)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label = QLabel(title)
        title_label.setObjectName("FoundationTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_label = QLabel(body)
        body_label.setObjectName("BodyText")
        body_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_label.setWordWrap(True)
        body_label.setMaximumWidth(500)
        button = QPushButton(action)
        button.setObjectName("PrimaryButton")
        button.setEnabled(enabled)
        button.setMinimumWidth(170)

        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(5)
        layout.addWidget(title_label)
        layout.addWidget(body_label)
        layout.addSpacing(10)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)

        root.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)
        root.addStretch(1)
