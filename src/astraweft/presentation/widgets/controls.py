"""Accessible buttons, inputs, selectors, and badges."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton

ButtonVariant = Literal["primary", "ghost", "danger"]
BadgeTone = Literal["neutral", "success", "warning", "danger", "info"]


class Button(QPushButton):
    """Standard text action with a semantic visual variant."""

    def __init__(self, text: str, *, variant: ButtonVariant = "primary") -> None:
        super().__init__(text)
        object_names = {
            "primary": "PrimaryButton",
            "ghost": "GhostButton",
            "danger": "DangerButton",
        }
        self.setObjectName(object_names[variant])
        self.setAccessibleName(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(36)


class IconButton(QPushButton):
    """Compact glyph action that always has a non-visual name."""

    def __init__(self, glyph: str, accessible_name: str, *, tooltip: str | None = None) -> None:
        if not accessible_name.strip():
            raise ValueError("IconButton requires an accessible name")
        super().__init__(glyph)
        self.setObjectName("IconButton")
        self.setAccessibleName(accessible_name)
        self.setToolTip(tooltip or accessible_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(34, 34)


class TextInput(QLineEdit):
    """Single-line input with an explicit accessible label."""

    def __init__(self, accessible_name: str, *, placeholder: str = "") -> None:
        if not accessible_name.strip():
            raise ValueError("TextInput requires an accessible name")
        super().__init__()
        self.setObjectName("TextInput")
        self.setAccessibleName(accessible_name)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)


class SelectInput(QComboBox):
    """Styled selector with keyboard and screen-reader labeling."""

    def __init__(self, accessible_name: str) -> None:
        if not accessible_name.strip():
            raise ValueError("SelectInput requires an accessible name")
        super().__init__()
        self.setObjectName("SelectInput")
        self.setAccessibleName(accessible_name)
        self.setMinimumHeight(36)


class Badge(QLabel):
    """Text-plus-color semantic status indicator."""

    def __init__(self, text: str, *, tone: BadgeTone = "neutral") -> None:
        super().__init__(text)
        self.setObjectName("Badge")
        self.setProperty("tone", tone)
        self.setAccessibleName(text)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
