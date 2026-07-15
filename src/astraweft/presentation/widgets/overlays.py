"""Decision dialogs and the global task drawer."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from astraweft.presentation.i18n import Translator
from astraweft.presentation.widgets.controls import Button, IconButton


class ConfirmDialog(QDialog):
    """Explicit high-risk confirmation with cancel as the safe default."""

    def __init__(
        self,
        title: str,
        body: str,
        *,
        confirm_text: str,
        destructive: bool = False,
        parent: QWidget | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        translator = translator or Translator()
        self.setObjectName("ConfirmDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(13)
        heading = QLabel(title)
        heading.setObjectName("DialogTitle")
        message = QLabel(body)
        message.setObjectName("BodyText")
        message.setWordWrap(True)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = Button(translator.text("取消", "Cancel"), variant="ghost")
        confirm = Button(confirm_text, variant="danger" if destructive else "primary")
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(confirm)
        layout.addWidget(heading)
        layout.addWidget(message)
        layout.addLayout(actions)
        cancel.setDefault(True)


class Drawer(QFrame):
    """Right-side transient panel closed by its button or the global Esc action."""

    closed = Signal()

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        translator = translator or Translator()
        self.setObjectName("Drawer")
        self.setFixedWidth(360)
        self.setVisible(False)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(12)
        header = QHBoxLayout()
        heading = QLabel(title)
        heading.setObjectName("DrawerTitle")
        close = IconButton("×", translator.text("关闭抽屉", "Close drawer"))
        close.clicked.connect(self.close_drawer)
        header.addWidget(heading)
        header.addStretch(1)
        header.addWidget(close)
        root.addLayout(header)
        divider = QFrame()
        divider.setObjectName("DrawerDivider")
        divider.setFixedHeight(1)
        root.addWidget(divider)
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self.content_layout, 1)

    def set_content(self, widget: QWidget) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item is None:
                continue
            old_widget = item.widget()
            if old_widget is not None:
                old_widget.deleteLater()
        self.content_layout.addWidget(widget)

    def open_drawer(self) -> None:
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def close_drawer(self) -> None:
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def toggle(self) -> None:
        if self.isVisible():
            self.close_drawer()
        else:
            self.open_drawer()
