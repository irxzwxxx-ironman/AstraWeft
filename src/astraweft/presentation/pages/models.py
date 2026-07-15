"""Synced Provider model catalog page."""

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

from astraweft.application.providers import ProviderService
from astraweft.domain.provider import Model, Provider
from astraweft.presentation.design_system import fixed_width_font
from astraweft.presentation.widgets.controls import Button, SelectInput
from astraweft.presentation.widgets.data_views import DataTable, TabView
from astraweft.presentation.widgets.feedback import EmptyState


class ModelsPage(QWidget):
    """Read-oriented catalog showing availability separately from user enablement."""

    def __init__(self, service: ProviderService) -> None:
        super().__init__()
        self.setObjectName("ModelsPage")
        self._service = service
        self._tasks: set[asyncio.Task[Any]] = set()
        self._models: tuple[Model, ...] = ()
        self._providers: tuple[Provider, ...] = ()
        self._logger = logging.getLogger("astraweft.presentation.models")

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 27, 30, 24)
        root.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("模型目录")
        title.setObjectName("ContentTitle")
        subtitle = QLabel("能力、参数 Schema、价格和可用状态均来自 Provider 同步")
        subtitle.setObjectName("BodyText")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        self._filter = SelectInput("按 Provider 筛选")
        self._filter.setMinimumWidth(210)
        self._filter.currentIndexChanged.connect(lambda _index: self._render())
        refresh = Button("刷新", variant="ghost")
        refresh.clicked.connect(self.request_refresh)
        header.addWidget(self._filter)
        header.addWidget(refresh)
        root.addLayout(header)

        self._summary = QLabel("等待模型同步")
        self._summary.setObjectName("CatalogSummary")
        root.addWidget(self._summary)

        self._table = DataTable("Provider 模型目录")
        self._table.clicked.connect(lambda index: self._show_detail(index.row()))
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setObjectName("ModelCatalogSplitter")
        self._splitter.addWidget(self._table)
        self._splitter.addWidget(self._build_detail())
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setSizes([760, 430])
        root.addWidget(self._splitter, 1)
        self._empty = EmptyState(
            "◉",
            "模型目录为空",
            "先在 Provider 页面测试连接并同步模型。",
        )
        self._empty.hide()
        root.addWidget(self._empty, 1)
        QTimer.singleShot(0, self.request_refresh)

    def request_refresh(self) -> None:
        self._start(self._refresh())

    async def _refresh(self) -> None:
        try:
            self._providers = await self._service.list_providers()
            self._models = await self._service.list_models()
        except Exception:
            self._logger.exception("model_catalog_load_failed")
            self._summary.setText("模型目录读取失败")
            return
        selected = self._filter.currentData()
        self._filter.blockSignals(True)
        self._filter.clear()
        self._filter.addItem("全部 Provider", userData=None)
        for provider in self._providers:
            self._filter.addItem(provider.name, userData=provider.id)
        for index in range(self._filter.count()):
            if self._filter.itemData(index) == selected:
                self._filter.setCurrentIndex(index)
                break
        self._filter.blockSignals(False)
        self._render()

    def _render(self) -> None:
        provider_filter = self._filter.currentData()
        filtered = tuple(
            model
            for model in self._models
            if provider_filter is None or model.provider_id == provider_filter
        )
        self._splitter.setVisible(bool(filtered))
        self._empty.setVisible(not filtered)
        available = sum(model.available and model.enabled for model in filtered)
        unavailable = sum(not model.available for model in filtered)
        self._summary.setText(
            f"{len(filtered)} 个模型  ·  {available} 个已启用且可用"
            + (f"  ·  {unavailable} 个远端已下线" if unavailable else "")
        )
        provider_names = {provider.id: provider.name for provider in self._providers}
        table = QStandardItemModel(0, 6, self)
        table.setHorizontalHeaderLabels(
            ["模型", "Provider", "模态", "操作能力", "状态", "最近同步"]
        )
        for model in filtered:
            status = "可用" if model.available else "远端下线"
            if model.available and not model.enabled:
                status = "已停用"
            values = (
                model.display_name,
                provider_names.get(model.provider_id, model.provider_id),
                model.modality,
                " · ".join(sorted(model.operations)),
                status,
                model.synced_at.astimezone().strftime("%Y-%m-%d %H:%M") if model.synced_at else "—",
            )
            items = [QStandardItem(value) for value in values]
            items[0].setData(model.id, Qt.ItemDataRole.UserRole)
            for item in items:
                item.setEditable(False)
            if not model.available:
                for item in items:
                    item.setForeground(Qt.GlobalColor.gray)
            table.appendRow(items)
        self._table.setModel(table)
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        if filtered:
            self._table.selectRow(0)
            self._show_detail(0)

    def _build_detail(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("ModelDetail")
        panel.setMinimumWidth(360)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 17, 18, 18)
        layout.setSpacing(10)
        eyebrow = QLabel("MODEL DETAIL")
        eyebrow.setObjectName("HeroEyebrow")
        self._detail_title = QLabel("选择一个模型")
        self._detail_title.setObjectName("CardTitle")
        self._detail_meta = QLabel()
        self._detail_meta.setObjectName("MutedText")
        self._detail_meta.setWordWrap(True)
        self._detail_operations = QLabel()
        self._detail_operations.setObjectName("BodyText")
        self._detail_operations.setWordWrap(True)
        layout.addWidget(eyebrow)
        layout.addWidget(self._detail_title)
        layout.addWidget(self._detail_meta)
        layout.addWidget(self._detail_operations)

        tabs = TabView("模型 Schema 与价格详情")
        tabs.tabBar().setUsesScrollButtons(False)
        self._parameter_schema = _read_only_json("模型参数 Schema")
        self._output_schema = _read_only_json("模型输出 Schema")
        self._capabilities = _read_only_json("模型能力")
        self._pricing = _read_only_json("模型定价")
        tabs.addTab(self._parameter_schema, "参数")
        tabs.addTab(self._output_schema, "输出")
        tabs.addTab(self._capabilities, "能力")
        tabs.addTab(self._pricing, "价格")
        layout.addWidget(tabs, 1)
        return panel

    def _show_detail(self, row: int) -> None:
        table_model = self._table.model()
        if table_model is None or not 0 <= row < table_model.rowCount():
            return
        model_id = table_model.index(row, 0).data(Qt.ItemDataRole.UserRole)
        model = next((item for item in self._models if item.id == model_id), None)
        if model is None:
            return
        availability = "可用" if model.available else "远端已下线"
        user_state = "用户已启用" if model.enabled else "用户已停用"
        self._detail_title.setText(model.display_name)
        self._detail_meta.setText(
            f"{model.remote_model_id}  ·  {model.modality}  ·  {availability}  ·  {user_state}"
        )
        self._detail_operations.setText("操作能力  " + "  ·  ".join(sorted(model.operations)))
        self._parameter_schema.setPlainText(_json_text(model.parameter_schema))
        self._output_schema.setPlainText(_json_text(model.output_schema))
        self._capabilities.setPlainText(_json_text(model.capabilities))
        self._pricing.setPlainText(_json_text(model.pricing))

    def _start(self, operation: Coroutine[Any, Any, object]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            operation.close()
            return
        task = loop.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _read_only_json(accessible_name: str) -> QPlainTextEdit:
    viewer = QPlainTextEdit()
    viewer.setObjectName("SchemaViewer")
    viewer.setAccessibleName(accessible_name)
    viewer.setFont(fixed_width_font())
    viewer.setReadOnly(True)
    viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    return viewer


def _json_text(value: object) -> str:
    return json.dumps(_plain_json(value), ensure_ascii=False, indent=2, sort_keys=True)


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(child) for child in value]
    return value
