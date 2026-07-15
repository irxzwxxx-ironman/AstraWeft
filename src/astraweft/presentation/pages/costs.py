"""Provider and model cost analysis with explicit unknown-cost accounting."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QLabel, QVBoxLayout, QWidget

from astraweft.application.query import QueryService
from astraweft.ports.query import CostBreakdown
from astraweft.presentation.design_system.tokens import Colors
from astraweft.presentation.i18n import Translator
from astraweft.presentation.widgets.cards import MetricCard
from astraweft.presentation.widgets.controls import Button, SelectInput
from astraweft.presentation.widgets.data_views import DataTable
from astraweft.presentation.widgets.feedback import EmptyState


class CostAnalysisPage(QWidget):
    """Read-only cost view grouped by Provider, model, and currency."""

    def __init__(self, queries: QueryService, translator: Translator | None = None) -> None:
        super().__init__()
        self.setObjectName("CostAnalysisPage")
        self._queries = queries
        self._translator = translator or Translator()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._logger = logging.getLogger("astraweft.presentation.costs")

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 27, 30, 24)
        root.setSpacing(18)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel(self._translator.text("成本分析", "Cost Analysis"))
        title.setObjectName("ContentTitle")
        self._summary = QLabel(
            self._translator.text(
                "仅汇总 Provider 明确返回的价格；未知成本始终单独显示",
                "Only confirmed Provider prices are totaled; unknown costs remain separate.",
            )
        )
        self._summary.setObjectName("BodyText")
        titles.addWidget(title)
        titles.addWidget(self._summary)
        header.addLayout(titles)
        header.addStretch(1)
        self._period = SelectInput(self._translator.text("成本统计周期", "Cost reporting period"))
        for chinese, english, days in (
            ("最近 7 天", "Last 7 days", 7),
            ("最近 30 天", "Last 30 days", 30),
            ("最近 90 天", "Last 90 days", 90),
            ("全部时间", "All time", None),
        ):
            self._period.addItem(self._translator.text(chinese, english), days)
        self._period.setCurrentIndex(1)
        self._period.currentIndexChanged.connect(self.request_refresh)
        refresh = Button(self._translator.text("刷新", "Refresh"), variant="ghost")
        refresh.clicked.connect(self.request_refresh)
        header.addWidget(self._period)
        header.addWidget(refresh)
        root.addLayout(header)

        metrics = QHBoxLayout()
        metrics.setSpacing(13)
        self._known = MetricCard(
            self._translator.text("已知成本", "Known cost"),
            "…",
            self._translator.text("按币种独立汇总", "Totals remain separate by currency"),
            Colors.SUCCESS,
        )
        self._priced_calls = MetricCard(
            self._translator.text("已定价调用", "Priced calls"),
            "…",
            self._translator.text("正在读取调用记录", "Loading request logs"),
            Colors.CYAN,
        )
        self._unknown = MetricCard(
            self._translator.text("未知成本", "Unknown cost"),
            "…",
            self._translator.text("不会计为 0", "Never counted as zero"),
            Colors.WARNING,
        )
        for card in (self._known, self._priced_calls, self._unknown):
            metrics.addWidget(card, 1)
        root.addLayout(metrics)

        self._table = DataTable(
            self._translator.text(
                "按 Provider 和模型分组的成本",
                "Costs grouped by Provider and model",
            )
        )
        root.addWidget(self._table, 1)
        self._empty = EmptyState(
            "¢",
            self._translator.text("还没有已知成本", "No known costs yet"),
            self._translator.text(
                "执行支持定价的 Provider 调用后，这里会按模型和币种汇总。",
                "Calls from Providers that report pricing will be grouped by model and currency.",
            ),
        )
        self._empty.hide()
        root.addWidget(self._empty, 1)
        QTimer.singleShot(0, self.request_refresh)

    def request_refresh(self) -> None:
        self._start(self._refresh())

    async def _refresh(self) -> None:
        days = self._period.currentData()
        if days is not None and not isinstance(days, int):
            return
        try:
            breakdown = await self._queries.get_cost_breakdown(days=days)
        except Exception:
            self._logger.exception("cost_analysis_load_failed")
            self._summary.setText(
                self._translator.text("成本记录读取失败", "Could not load cost records")
            )
            return
        self._render(breakdown)

    def _render(self, breakdown: CostBreakdown) -> None:
        period = (
            self._translator.text("全部时间", "All time")
            if breakdown.period_days is None
            else self._translator.text(
                "最近 {days} 天",
                "Last {days} days",
                days=breakdown.period_days,
            )
        )
        self._summary.setText(
            self._translator.text(
                "{period} 共 {total} 次调用  ·  {known} 次已定价  ·  {unknown} 次成本未知",
                "{period}: {total} calls  ·  {known} priced  ·  {unknown} unknown",
                period=period,
                total=self._translator.integer(breakdown.total_calls),
                known=self._translator.integer(breakdown.known_cost_calls),
                unknown=self._translator.integer(breakdown.unknown_cost_calls),
            )
        )
        totals: dict[str, int] = {}
        for row in breakdown.rows:
            totals[row.currency] = totals.get(row.currency, 0) + row.amount_micros
        if len(totals) == 1:
            currency, amount = next(iter(totals.items()))
            self._known.set_value(self._translator.money(currency, amount))
        elif totals:
            self._known.set_value(self._translator.text("多币种", "Multiple currencies"))
        else:
            self._known.set_value("—")
        self._known.set_foot(
            "  ·  ".join(
                self._translator.money(currency, amount)
                for currency, amount in sorted(totals.items())
            )
            or self._translator.text("暂无 Provider 已知价格", "No confirmed Provider prices")
        )
        self._priced_calls.set_value(str(breakdown.known_cost_calls))
        self._priced_calls.set_foot(
            self._translator.text(
                "占 {total} 次调用",
                "Of {total} calls",
                total=self._translator.integer(breakdown.total_calls),
            )
        )
        self._unknown.set_value(str(breakdown.unknown_cost_calls))
        self._unknown.set_foot(
            self._translator.text(
                "保持未知" if breakdown.unknown_cost_calls else "本周期无未知成本",
                "Remains unknown"
                if breakdown.unknown_cost_calls
                else "No unknown costs in this period",
            )
        )

        self._table.setVisible(bool(breakdown.rows))
        self._empty.setVisible(not breakdown.rows)
        model = QStandardItemModel(0, 5, self)
        model.setHorizontalHeaderLabels(
            [
                "Provider",
                self._translator.text("模型", "Model"),
                self._translator.text("调用数", "Calls"),
                self._translator.text("币种", "Currency"),
                self._translator.text("已知成本", "Known cost"),
            ]
        )
        for row in breakdown.rows:
            values = (
                row.provider_name,
                row.model_name
                or row.model_id
                or self._translator.text("未指定模型", "No model specified"),
                str(row.call_count),
                row.currency,
                self._translator.money(row.currency, row.amount_micros),
            )
            items = [QStandardItem(value) for value in values]
            for item in items:
                item.setEditable(False)
            model.appendRow(items)
        self._table.setModel(model)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

    def _start(self, operation: Coroutine[Any, Any, object]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            operation.close()
            return
        task = loop.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
