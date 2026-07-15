"""Redacted Provider request log page."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Coroutine, Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
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
from astraweft.domain.task import RequestLog
from astraweft.presentation.design_system import fixed_width_font
from astraweft.presentation.widgets.controls import Button
from astraweft.presentation.widgets.data_views import DataTable
from astraweft.presentation.widgets.feedback import EmptyState


class RequestLogsPage(QWidget):
    """Latest redacted request facts; unknown cost is never displayed as zero."""

    def __init__(self, service: TaskService, queries: QueryService | None = None) -> None:
        super().__init__()
        self.setObjectName("RequestLogsPage")
        self._service = service
        self._queries = queries
        self._next_cursor: str | None = None
        self._logs: tuple[RequestLog, ...] = ()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._logger = logging.getLogger("astraweft.presentation.request_logs")
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 27, 30, 24)
        root.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("调用日志")
        title.setObjectName("ContentTitle")
        self._summary = QLabel("正文默认不落库；这里只显示脱敏摘要")
        self._summary.setObjectName("BodyText")
        titles.addWidget(title)
        titles.addWidget(self._summary)
        header.addLayout(titles)
        header.addStretch(1)
        refresh = Button("刷新", variant="ghost")
        refresh.clicked.connect(self.request_refresh)
        self._load_more = Button("加载更多", variant="ghost")
        self._load_more.clicked.connect(lambda: self._start(self._load_next()))
        self._load_more.hide()
        header.addWidget(self._load_more)
        header.addWidget(refresh)
        root.addLayout(header)
        self._table = DataTable("调用日志")
        self._table.clicked.connect(lambda index: self._show_detail(index.row()))
        self._detail = QPlainTextEdit()
        self._detail.setObjectName("SchemaViewer")
        self._detail.setAccessibleName("调用日志详情")
        self._detail.setFont(fixed_width_font())
        self._detail.setReadOnly(True)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._table)
        splitter.addWidget(self._detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self._splitter = splitter
        root.addWidget(splitter, 1)
        self._empty = EmptyState("≡", "还没有调用日志", "从 Playground 运行任务后会自动记录。")
        self._empty.hide()
        root.addWidget(self._empty, 1)
        QTimer.singleShot(0, self.request_refresh)

    def request_refresh(self) -> None:
        self._start(self._refresh())

    async def _refresh(self) -> None:
        try:
            if self._queries is None:
                self._logs = await self._service.list_request_logs(limit=1000)
                self._next_cursor = None
            else:
                page = await self._queries.search_request_logs(limit=100)
                self._logs = page.items
                self._next_cursor = page.next_cursor
        except Exception:
            self._logger.exception("request_logs_load_failed")
            self._summary.setText("调用日志读取失败")
            return
        self._render()

    async def _load_next(self) -> None:
        if self._queries is None or self._next_cursor is None:
            return
        try:
            page = await self._queries.search_request_logs(
                cursor=self._next_cursor,
                limit=100,
            )
        except Exception:
            self._logger.exception("request_logs_next_page_failed")
            self._summary.setText("加载更多日志失败")
            return
        self._logs += page.items
        self._next_cursor = page.next_cursor
        self._render()

    def _render(self) -> None:
        unknown = sum(log.amount_micros is None for log in self._logs)
        self._summary.setText(
            f"显示最近 {len(self._logs)} 条脱敏记录"
            + (f"  ·  {unknown} 条成本未知" if unknown else "")
        )
        self._splitter.setVisible(bool(self._logs))
        self._empty.setVisible(not self._logs)
        self._load_more.setVisible(self._next_cursor is not None)
        model = QStandardItemModel(0, 7, self)
        model.setHorizontalHeaderLabels(
            ["时间", "操作", "结果", "耗时", "成本", "Provider", "Trace ID"]
        )
        for log in self._logs:
            values = (
                log.created_at.astimezone().strftime("%m-%d %H:%M:%S"),
                log.operation,
                log.error_code or "成功",
                f"{log.latency_ms} ms",
                _cost(log),
                log.provider_id,
                log.trace_id,
            )
            items = [QStandardItem(value) for value in values]
            items[0].setData(log.id, Qt.ItemDataRole.UserRole)
            for item in items:
                item.setEditable(False)
            model.appendRow(items)
        self._table.setModel(model)
        header = self._table.horizontalHeader()
        for column in (0, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in (1, 5, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        if self._logs:
            self._table.selectRow(0)
            self._show_detail(0)

    def _show_detail(self, row: int) -> None:
        model = self._table.model()
        if model is None or not 0 <= row < model.rowCount():
            return
        log_id = model.index(row, 0).data(Qt.ItemDataRole.UserRole)
        log = next((item for item in self._logs if item.id == log_id), None)
        if log is None:
            return
        self._detail.setPlainText(
            _json_text(
                {
                    "http": {
                        "method": log.method,
                        "url_template": log.url_template,
                        "status": log.http_status,
                    },
                    "request_summary": log.request_summary,
                    "response_summary": log.response_summary,
                    "usage": log.usage,
                    "cost": _cost(log),
                    "error_code": log.error_code,
                    "trace_id": log.trace_id,
                }
            )
        )

    def _start(self, operation: Coroutine[Any, Any, object]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            operation.close()
            return
        task = loop.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _cost(log: RequestLog) -> str:
    if log.amount_micros is None or log.currency is None:
        return "未知"
    return f"{log.currency} {log.amount_micros / 1_000_000:.6f}"


def _json_text(value: object) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, indent=2, sort_keys=True)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain(child) for child in value]
    return value
