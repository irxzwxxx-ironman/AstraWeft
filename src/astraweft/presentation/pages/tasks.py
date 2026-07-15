"""Durable task center and attempt details."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Coroutine, Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from astraweft.application.query import QueryService
from astraweft.application.tasks import TaskService
from astraweft.domain.task import Task, TaskStatus
from astraweft.presentation.design_system import fixed_width_font
from astraweft.presentation.i18n import Translator
from astraweft.presentation.widgets.controls import Button
from astraweft.presentation.widgets.data_views import DataTable
from astraweft.presentation.widgets.feedback import EmptyState


class TaskCenterPage(QWidget):
    """Latest 1,000 tasks with attempts, output, cancel, and clear next action."""

    def __init__(
        self,
        service: TaskService,
        queries: QueryService | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("TaskCenterPage")
        self._service = service
        self._queries = queries
        self._translator = translator or Translator()
        self._next_cursor: str | None = None
        self._items: tuple[Task, ...] = ()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._logger = logging.getLogger("astraweft.presentation.tasks")

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 27, 30, 24)
        root.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel(self._translator.text("任务中心", "Task Center"))
        title.setObjectName("ContentTitle")
        self._summary = QLabel(self._translator.text("读取任务队列…", "Loading task queue…"))
        self._summary.setObjectName("BodyText")
        titles.addWidget(title)
        titles.addWidget(self._summary)
        header.addLayout(titles)
        header.addStretch(1)
        self._cancel = Button(self._translator.text("取消任务", "Cancel task"), variant="danger")
        self._cancel.setEnabled(False)
        self._cancel.clicked.connect(lambda: self._start(self._cancel_selected()))
        refresh = Button(self._translator.text("刷新", "Refresh"), variant="ghost")
        refresh.clicked.connect(self.request_refresh)
        self._load_more = Button(self._translator.text("加载更多", "Load more"), variant="ghost")
        self._load_more.clicked.connect(lambda: self._start(self._load_next()))
        self._load_more.hide()
        header.addWidget(self._cancel)
        header.addWidget(self._load_more)
        header.addWidget(refresh)
        root.addLayout(header)

        self._table = DataTable(self._translator.text("任务列表", "Task list"))
        self._table.clicked.connect(lambda index: self._show_detail(index.row()))
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._table)
        self._splitter.addWidget(self._build_detail())
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setSizes([760, 440])
        root.addWidget(self._splitter, 1)
        self._empty = EmptyState(
            "◷",
            self._translator.text("队列当前为空", "The queue is empty"),
            self._translator.text(
                "从 Playground 运行第一个模型任务。",
                "Run your first model task from the Playground.",
            ),
        )
        self._empty.hide()
        root.addWidget(self._empty, 1)
        QTimer.singleShot(0, self.request_refresh)

    def _build_detail(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("ModelDetail")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 17, 18, 18)
        layout.setSpacing(10)
        eyebrow = QLabel("TASK DETAIL")
        eyebrow.setObjectName("HeroEyebrow")
        self._detail_title = QLabel(self._translator.text("选择一个任务", "Select a task"))
        self._detail_title.setObjectName("CardTitle")
        self._detail_meta = QLabel()
        self._detail_meta.setObjectName("MutedText")
        self._detail_meta.setWordWrap(True)
        self._attempts = QLabel()
        self._attempts.setObjectName("BodyText")
        self._attempts.setWordWrap(True)
        self._output = QPlainTextEdit()
        self._output.setObjectName("SchemaViewer")
        self._output.setAccessibleName(
            self._translator.text("任务规范化输出", "Normalized task output")
        )
        self._output.setFont(fixed_width_font())
        self._output.setReadOnly(True)
        layout.addWidget(eyebrow)
        layout.addWidget(self._detail_title)
        layout.addWidget(self._detail_meta)
        layout.addWidget(self._attempts)
        layout.addWidget(self._output, 1)
        return panel

    def request_refresh(self) -> None:
        self._start(self._refresh())

    async def _refresh(self) -> None:
        selected = self._selected_task_id()
        try:
            if self._queries is None:
                self._items = await self._service.list_tasks(limit=1000)
                self._next_cursor = None
            else:
                page = await self._queries.search_tasks(limit=100)
                self._items = page.items
                self._next_cursor = page.next_cursor
        except Exception:
            self._logger.exception("task_center_load_failed")
            self._summary.setText(self._translator.text("任务读取失败", "Unable to load tasks"))
            return
        await self._render(selected)

    async def _load_next(self) -> None:
        if self._queries is None or self._next_cursor is None:
            return
        selected = self._selected_task_id()
        try:
            page = await self._queries.search_tasks(cursor=self._next_cursor, limit=100)
        except Exception:
            self._logger.exception("task_center_next_page_failed")
            self._summary.setText(
                self._translator.text("加载更多任务失败", "Unable to load more tasks")
            )
            return
        self._items += page.items
        self._next_cursor = page.next_cursor
        await self._render(selected)

    async def _render(self, selected: str | None) -> None:
        running = sum(not task.status.terminal for task in self._items)
        attention = sum(task.status is TaskStatus.NEEDS_ATTENTION for task in self._items)
        summary = self._translator.text(
            "显示最近 {total} 个任务  ·  {running} 个进行中",
            "Showing {total} recent tasks  ·  {running} in progress",
            total=self._translator.integer(len(self._items)),
            running=self._translator.integer(running),
        )
        if attention:
            summary += self._translator.text(
                "  ·  {count} 个需要处理",
                "  ·  {count} need attention",
                count=self._translator.integer(attention),
            )
        self._summary.setText(summary)
        self._splitter.setVisible(bool(self._items))
        self._empty.setVisible(not self._items)
        self._load_more.setVisible(self._next_cursor is not None)
        self._cancel.setEnabled(False)
        model = QStandardItemModel(0, 6, self)
        model.setHorizontalHeaderLabels(
            [
                self._translator.text("状态", "Status"),
                self._translator.text("操作", "Operation"),
                self._translator.text("进度", "Progress"),
                self._translator.text("优先级", "Priority"),
                self._translator.text("更新时间", "Updated"),
                "Task ID",
            ]
        )
        selected_row = 0
        for row, task in enumerate(self._items):
            values = (
                _status_text(task.status, self._translator),
                task.operation,
                "—" if task.progress is None else f"{task.progress}%",
                str(task.priority),
                task.updated_at.astimezone().strftime("%m-%d %H:%M:%S"),
                task.id,
            )
            items = [QStandardItem(value) for value in values]
            items[0].setData(task.id, Qt.ItemDataRole.UserRole)
            for item in items:
                item.setEditable(False)
            model.appendRow(items)
            if task.id == selected:
                selected_row = row
        self._table.setModel(model)
        view = self._table.horizontalHeader()
        view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        view.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        view.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        if self._items:
            self._table.selectRow(selected_row)
            await self._show_detail_async(selected_row)

    def _show_detail(self, row: int) -> None:
        self._start(self._show_detail_async(row))

    async def _show_detail_async(self, row: int) -> None:
        task_id = self._task_id_at(row)
        task = next((item for item in self._items if item.id == task_id), None)
        if task is None:
            return
        attempts = await self._service.list_attempts(task.id)
        self._detail_title.setText(
            f"{_status_text(task.status, self._translator)} · {task.operation}"
        )
        self._detail_meta.setText(
            self._translator.text(
                "{task_id}\nProvider {provider_id}  ·  版本 {version}",
                "{task_id}\nProvider {provider_id}  ·  version {version}",
                task_id=task.id,
                provider_id=task.provider_id,
                version=task.row_version,
            )
        )
        self._attempts.setText(
            self._translator.text("执行记录  ", "Attempts  ")
            + (
                "  ·  ".join(
                    f"#{attempt.attempt_no} {attempt.phase.value} / {attempt.status.value}"
                    for attempt in attempts
                )
                or self._translator.text("暂无", "None")
            )
        )
        self._output.setPlainText(_json_text(task.normalized_output or {}))
        self._cancel.setEnabled(not task.status.terminal)

    async def _cancel_selected(self) -> None:
        task_id = self._selected_task_id()
        if task_id is None:
            return
        try:
            await self._service.cancel(task_id)
        except Exception:
            self._logger.exception("task_cancel_failed")
        await self._refresh()

    def _selected_task_id(self) -> str | None:
        indexes = self._table.selectionModel().selectedRows() if self._table.model() else []
        return self._task_id_at(indexes[0].row()) if indexes else None

    def _task_id_at(self, row: int) -> str | None:
        model = self._table.model()
        if model is None or not 0 <= row < model.rowCount():
            return None
        value = model.index(row, 0).data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, str) else None

    def _start(self, operation: Coroutine[Any, Any, object]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            operation.close()
            return
        task = loop.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _status_text(status: TaskStatus, translator: Translator | None = None) -> str:
    translator = translator or Translator()
    return {
        TaskStatus.CREATED: translator.text("已创建", "Created"),
        TaskStatus.QUEUED: translator.text("排队中", "Queued"),
        TaskStatus.SUBMITTING: translator.text("提交中", "Submitting"),
        TaskStatus.RUNNING: translator.text("运行中", "Running"),
        TaskStatus.POLLING: translator.text("轮询中", "Polling"),
        TaskStatus.RETRY_WAIT: translator.text("等待重试", "Waiting to retry"),
        TaskStatus.CANCELING: translator.text("取消中", "Canceling"),
        TaskStatus.RECOVERING: translator.text("恢复中", "Recovering"),
        TaskStatus.SUCCESS: translator.text("成功", "Succeeded"),
        TaskStatus.FAILED: translator.text("失败", "Failed"),
        TaskStatus.CANCELED: translator.text("已取消", "Canceled"),
        TaskStatus.TIMED_OUT: translator.text("已超时", "Timed out"),
        TaskStatus.NEEDS_ATTENTION: translator.text("需要处理", "Needs attention"),
    }[status]


def _json_text(value: object) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, indent=2, sort_keys=True)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain(child) for child in value]
    return value
