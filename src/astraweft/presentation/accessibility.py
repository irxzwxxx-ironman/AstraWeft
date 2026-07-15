"""Automated checks for the keyboard and accessible-name baseline."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QHeaderView,
    QLineEdit,
    QListView,
    QPlainTextEdit,
    QWidget,
)


@dataclass(frozen=True, slots=True)
class AccessibilityIssue:
    code: str
    widget_type: str
    object_name: str

    def __str__(self) -> str:
        identity = self.object_name or "<unnamed>"
        return f"{self.code}: {self.widget_type} {identity}"


def audit_accessibility(root: QWidget) -> tuple[AccessibilityIssue, ...]:
    """Return actionable issues for user-facing, enabled interactive controls."""
    issues: list[AccessibilityIssue] = []
    interactive = (QAbstractButton, QLineEdit, QComboBox, QAbstractItemView, QPlainTextEdit)
    for widget in root.findChildren(QWidget):
        if not isinstance(widget, interactive) or not widget.isEnabled():
            continue
        if _is_internal_control(widget):
            continue
        identity = widget.objectName()
        label = widget.accessibleName().strip()
        if isinstance(widget, QAbstractButton):
            label = label or widget.text().strip()
        if not label:
            issues.append(
                AccessibilityIssue("missing-accessible-name", type(widget).__name__, identity)
            )
        if widget.focusPolicy() == Qt.FocusPolicy.NoFocus:
            issues.append(
                AccessibilityIssue("not-keyboard-focusable", type(widget).__name__, identity)
            )
    return tuple(issues)


def _is_internal_control(widget: QWidget) -> bool:
    if isinstance(widget, QHeaderView) or widget.objectName() == "qt_tableview_cornerbutton":
        return True
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(widget, QListView) and isinstance(parent, QComboBox):
            return True
        if isinstance(widget, QLineEdit) and isinstance(parent, (QComboBox, QAbstractSpinBox)):
            return True
        if isinstance(widget, QAbstractButton) and isinstance(parent, (QComboBox, QLineEdit)):
            return True
        parent = parent.parentWidget()
    return False


__all__ = ["AccessibilityIssue", "audit_accessibility"]
