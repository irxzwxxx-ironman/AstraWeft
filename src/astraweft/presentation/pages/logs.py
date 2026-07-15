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
from astraweft.presentation.i18n import Translator
from astraweft.presentation.widgets.controls import Button
from astraweft.presentation.widgets.data_views import DataTable
from astraweft.presentation.widgets.feedback import EmptyState


class RequestLogsPage(QWidget):
    """Latest redacted request facts; unknown cost is never displayed as zero."""

    def __init__(
        self,
        service: TaskService,
        queries: QueryService | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("RequestLogsPage")
        self._service = service
        self._queries = queries
        self._translator = translator or Translator()
        self._next_cursor: str | None = None
        self._logs: tuple[RequestLog, ...] = ()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._logger = logging.getLogger("astraweft.presentation.request_logs")
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 27, 30, 24)
        root.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel(self._translator.text("调用日志", "Request Logs"))
        title.setObjectName("ContentTitle")
        self._summary = QLabel(
            self._translator.text(
                "正文默认不落库；这里只显示脱敏摘要",
                "Request and response bodies are not stored by default; only redacted summaries appear here",
            )
        )
        self._summary.setObjectName("BodyText")
        titles.addWidget(title)
        titles.addWidget(self._summary)
        header.addLayout(titles)
        header.addStretch(1)
        refresh = Button(self._translator.text("刷新", "Refresh"), variant="ghost")
        refresh.clicked.connect(self.request_refresh)
        self._load_more = Button(self._translator.text("加载更多", "Load more"), variant="ghost")
        self._load_more.clicked.connect(lambda: self._start(self._load_next()))
        self._load_more.hide()
        header.addWidget(self._load_more)
        header.addWidget(refresh)
        root.addLayout(header)
        self._table = DataTable(self._translator.text("调用日志", "Request logs"))
        self._table.clicked.connect(lambda index: self._show_detail(index.row()))
        self._detail = QPlainTextEdit()
        self._detail.setObjectName("SchemaViewer")
        self._detail.setAccessibleName(self._translator.text("调用日志详情", "Request log detail"))
        self._detail.setFont(fixed_width_font())
        self._detail.setReadOnly(True)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._table)
        splitter.addWidget(self._detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self._splitter = splitter
        root.addWidget(splitter, 1)
        self._empty = EmptyState(
            "≡",
            self._translator.text("还没有调用日志", "No request logs yet"),
            self._translator.text(
                "从 Playground 运行任务后会自动记录。",
                "Logs are recorded automatically after a task runs from the Playground.",
            ),
        )
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
            self._summary.setText(
                self._translator.text("调用日志读取失败", "Unable to load request logs")
            )
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
            self._summary.setText(
                self._translator.text("加载更多日志失败", "Unable to load more request logs")
            )
            return
        self._logs += page.items
        self._next_cursor = page.next_cursor
        self._render()

    def _render(self) -> None:
        unknown = sum(log.amount_micros is None for log in self._logs)
        summary = self._translator.text(
            "显示最近 {count} 条脱敏记录",
            "Showing {count} recent redacted records",
            count=self._translator.integer(len(self._logs)),
        )
        if unknown:
            summary += self._translator.text(
                "  ·  {count} 条成本未知",
                "  ·  {count} with unknown cost",
                count=self._translator.integer(unknown),
            )
        self._summary.setText(summary)
        self._splitter.setVisible(bool(self._logs))
        self._empty.setVisible(not self._logs)
        self._load_more.setVisible(self._next_cursor is not None)
        model = QStandardItemModel(0, 7, self)
        model.setHorizontalHeaderLabels(
            [
                self._translator.text("时间", "Time"),
                self._translator.text("操作", "Operation"),
                self._translator.text("结果", "Result"),
                self._translator.text("耗时", "Latency"),
                self._translator.text("成本", "Cost"),
                "Provider",
                "Trace ID",
            ]
        )
        for log in self._logs:
            values = (
                log.created_at.astimezone().strftime("%m-%d %H:%M:%S"),
                log.operation,
                log.error_code or self._translator.text("成功", "Succeeded"),
                f"{log.latency_ms} ms",
                _cost(log, self._translator),
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
                    "cost": _cost(log, self._translator),
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


def _cost(log: RequestLog, translator: Translator | None = None) -> str:
    translator = translator or Translator()
    if log.amount_micros is None or log.currency is None:
        return translator.text("未知", "Unknown")
    return translator.money(log.currency, log.amount_micros)


def _json_text(value: object) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, indent=2, sort_keys=True)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain(child) for child in value]
    return value
