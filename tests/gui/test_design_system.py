"""Reusable design-system component behavior tests."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from astraweft.presentation.design_system.theme import apply_theme, system_reduce_motion
from astraweft.presentation.widgets import (
    Badge,
    Button,
    ConfirmDialog,
    DataTable,
    Drawer,
    EmptyState,
    ErrorState,
    IconButton,
    SelectInput,
    SkeletonBlock,
    TabView,
    TextInput,
    Toast,
)


@pytest.mark.gui
def test_controls_are_keyboard_reachable_and_accessibly_named(qtbot: QtBot) -> None:
    widgets: list[QWidget] = [
        Button("保存"),
        Button("取消", variant="ghost"),
        Button("删除", variant="danger"),
        IconButton("×", "关闭"),
        TextInput("名称", placeholder="输入名称"),
        SelectInput("模型"),
        Badge("在线", tone="success"),
    ]
    for widget in widgets:
        qtbot.addWidget(widget)
        assert widget.accessibleName()
        if isinstance(widget, (QPushButton, TextInput, SelectInput)):
            assert widget.focusPolicy() != Qt.FocusPolicy.NoFocus

    with pytest.raises(ValueError):
        IconButton("×", "")
    with pytest.raises(ValueError):
        TextInput("")
    with pytest.raises(ValueError):
        SelectInput(" ")


@pytest.mark.gui
def test_feedback_states_copy_retry_and_dismiss(qtbot: QtBot) -> None:
    empty = EmptyState("○", "没有内容", "创建后会显示在这里", action_text="创建")
    error = ErrorState("网络暂不可用", "trace-123")
    skeleton = SkeletonBlock(height=22)
    toast = Toast("设置已保存", tone="success", duration_ms=0)
    for widget in (empty, error, skeleton, toast):
        qtbot.addWidget(widget)
        widget.show()

    requested: list[str] = []
    empty.action_requested.connect(lambda: requested.append("create"))
    error.retry_requested.connect(lambda: requested.append("retry"))
    empty_buttons = empty.findChildren(QPushButton)
    assert empty_buttons
    empty_buttons[0].click()
    error_buttons = error.findChildren(QPushButton)
    assert len(error_buttons) == 2
    error_buttons[0].click()
    error_buttons[1].click()
    assert QApplication.clipboard().text() == "trace-123"
    assert requested == ["create", "retry"]

    dismissed: list[bool] = []
    toast.dismissed.connect(lambda: dismissed.append(True))
    toast.dismiss()
    assert dismissed == [True]
    assert toast.isHidden()
    with pytest.raises(ValueError):
        Toast("invalid", duration_ms=-1)


@pytest.mark.gui
def test_drawer_dialog_table_and_tabs_have_safe_defaults(qtbot: QtBot) -> None:
    drawer = Drawer("任务速览")
    drawer.set_content(EmptyState("○", "空", "没有任务"))
    qtbot.addWidget(drawer)
    drawer.open_drawer()
    assert drawer.isVisible()
    drawer.toggle()
    assert drawer.isHidden()
    drawer.toggle()
    drawer.close_drawer()

    dialog = ConfirmDialog(
        "删除产物？",
        "文件将进入回收站。",
        confirm_text="删除",
        destructive=True,
    )
    qtbot.addWidget(dialog)
    cancel = next(button for button in dialog.findChildren(QPushButton) if button.text() == "取消")
    assert cancel.isDefault()

    table = DataTable("任务列表")
    tabs = TabView("调用详情")
    tabs.addTab(QWidget(), "诊断")
    qtbot.addWidget(table)
    qtbot.addWidget(tabs)
    assert table.accessibleName() == "任务列表"
    assert tabs.count() == 1


@pytest.mark.gui
def test_theme_records_system_motion_preference(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    del qtbot
    application = QApplication.instance()
    assert isinstance(application, QApplication)
    assert isinstance(system_reduce_motion(), bool)
    monkeypatch.setattr(
        "astraweft.presentation.design_system.theme.system_reduce_motion", lambda: True
    )

    apply_theme(application, theme="system", reduce_motion=False)

    assert application.property("astraweftTheme") == "system"
    assert application.property("astraweftReduceMotion") is True
    assert "QScrollBar:horizontal" in application.styleSheet()
    assert "QCheckBox::indicator" in application.styleSheet()
    assert "QFrame#SurfaceCard" in application.styleSheet()
