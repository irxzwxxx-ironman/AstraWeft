"""Styled table and tab containers."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QTableView, QTabWidget


class DataTable(QTableView):
    """Read-oriented product table with safe selection defaults."""

    def __init__(self, accessible_name: str) -> None:
        super().__init__()
        self.setObjectName("DataTable")
        self.setAccessibleName(accessible_name)
        self.setAlternatingRowColors(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSortingEnabled(True)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )


class TabView(QTabWidget):
    """Keyboard-reachable product tab set."""

    def __init__(self, accessible_name: str) -> None:
        super().__init__()
        self.setObjectName("TabView")
        self.setAccessibleName(accessible_name)
        self.setDocumentMode(True)
