"""Schema-driven Provider task playground."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Coroutine, Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from astraweft.application.providers import ProviderService
from astraweft.application.tasks import CreateTask, TaskService
from astraweft.domain.provider import Model, Provider
from astraweft.presentation.design_system import fixed_width_font
from astraweft.presentation.widgets.controls import Button, SelectInput
from astraweft.presentation.widgets.schema_form import SchemaForm, SchemaFormError


class PlaygroundPage(QWidget):
    """Choose a synced model, render its Schema, and run a durable task."""

    task_changed = Signal(str)

    def __init__(self, providers: ProviderService, tasks: TaskService) -> None:
        super().__init__()
        self.setObjectName("PlaygroundPage")
        self._providers_service = providers
        self._tasks_service = tasks
        self._providers: tuple[Provider, ...] = ()
        self._models: tuple[Model, ...] = ()
        self._form: SchemaForm | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._logger = logging.getLogger("astraweft.presentation.playground")

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 27, 30, 24)
        root.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Playground")
        title.setObjectName("ContentTitle")
        subtitle = QLabel("选择模型、填写参数；每次运行都会进入可恢复任务队列")
        subtitle.setObjectName("BodyText")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        refresh = Button("刷新资源", variant="ghost")
        refresh.clicked.connect(self.request_refresh)
        header.addWidget(refresh)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_form_panel())
        splitter.addWidget(self._build_result_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([690, 500])
        root.addWidget(splitter, 1)
        QTimer.singleShot(0, self.request_refresh)

    def _build_form_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 17, 18, 18)
        layout.setSpacing(12)
        self._provider = SelectInput("Provider")
        self._model = SelectInput("模型")
        self._operation = SelectInput("操作")
        self._provider.currentIndexChanged.connect(lambda _index: self._populate_models())
        self._model.currentIndexChanged.connect(lambda _index: self._rebuild_form())
        layout.addWidget(_labeled("Provider", self._provider))
        layout.addWidget(_labeled("模型", self._model))
        layout.addWidget(_labeled("操作", self._operation))
        self._form_host = QFrame()
        self._form_host.setObjectName("SectionCard")
        self._form_layout = QVBoxLayout(self._form_host)
        self._form_layout.setContentsMargins(18, 17, 18, 18)
        self._form_layout.setSpacing(10)
        layout.addWidget(self._form_host)
        self._error = QLabel()
        self._error.setObjectName("FormError")
        self._error.setWordWrap(True)
        self._error.hide()
        layout.addWidget(self._error)
        actions = QHBoxLayout()
        self._run_button = Button("运行任务  →")
        self._run_button.setEnabled(False)
        self._run_button.clicked.connect(lambda: self._start(self._run()))
        actions.addStretch(1)
        actions.addWidget(self._run_button)
        layout.addLayout(actions)
        layout.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    def _build_result_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("ModelDetail")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 17, 18, 18)
        layout.setSpacing(10)
        eyebrow = QLabel("DURABLE RESULT")
        eyebrow.setObjectName("HeroEyebrow")
        self._result_title = QLabel("等待运行")
        self._result_title.setObjectName("CardTitle")
        self._result_meta = QLabel("结果、Task ID、费用与产物会保存在本机")
        self._result_meta.setObjectName("MutedText")
        self._result_meta.setWordWrap(True)
        self._result = QPlainTextEdit()
        self._result.setObjectName("SchemaViewer")
        self._result.setAccessibleName("规范化运行输出")
        self._result.setFont(fixed_width_font())
        self._result.setReadOnly(True)
        self._result.setPlaceholderText("运行后显示规范化输出")
        layout.addWidget(eyebrow)
        layout.addWidget(self._result_title)
        layout.addWidget(self._result_meta)
        layout.addWidget(self._result, 1)
        return panel

    def request_refresh(self) -> None:
        self._start(self._refresh())

    async def _refresh(self) -> None:
        try:
            self._providers = tuple(
                provider
                for provider in await self._providers_service.list_providers()
                if provider.enabled
            )
            self._models = await self._providers_service.list_models()
        except Exception:
            self._logger.exception("playground_resources_failed")
            self._show_error("资源读取失败，请检查 Provider 状态")
            return
        selected = self._provider.currentData()
        self._provider.blockSignals(True)
        self._provider.clear()
        for provider in self._providers:
            self._provider.addItem(provider.name, userData=provider.id)
        for index in range(self._provider.count()):
            if self._provider.itemData(index) == selected:
                self._provider.setCurrentIndex(index)
                break
        self._provider.blockSignals(False)
        self._populate_models()

    def _populate_models(self) -> None:
        provider_id = self._provider.currentData()
        selected = self._model.currentData()
        models = tuple(
            model
            for model in self._models
            if model.provider_id == provider_id
            and model.enabled
            and model.available
            and not model.deprecated
        )
        self._model.blockSignals(True)
        self._model.clear()
        for model in models:
            self._model.addItem(model.display_name, userData=model.id)
        for index in range(self._model.count()):
            if self._model.itemData(index) == selected:
                self._model.setCurrentIndex(index)
                break
        self._model.blockSignals(False)
        self._rebuild_form()

    def _rebuild_form(self) -> None:
        while self._form_layout.count():
            item = self._form_layout.takeAt(0)
            widget = None if item is None else item.widget()
            if widget is not None:
                widget.deleteLater()
        model = self._selected_model()
        self._operation.clear()
        if model is None:
            message = QLabel("没有可用模型；请先在 Provider 页面同步模型目录。")
            message.setObjectName("MutedText")
            message.setWordWrap(True)
            self._form_layout.addWidget(message)
            self._form = None
            self._run_button.setEnabled(False)
            return
        for operation in sorted(model.operations):
            self._operation.addItem(operation, userData=operation)
        try:
            self._form = SchemaForm(
                model.parameter_schema,
                model.parameter_ui_schema,
                initial=model.default_params,
            )
        except SchemaFormError as exc:
            self._form = None
            self._show_error(str(exc))
            self._run_button.setEnabled(False)
            return
        self._form_layout.addWidget(self._form)
        self._run_button.setEnabled(True)

    async def _run(self) -> None:
        model = self._selected_model()
        provider_id = self._provider.currentData()
        operation = self._operation.currentData()
        if (
            model is None
            or self._form is None
            or not isinstance(provider_id, str)
            or not isinstance(operation, str)
        ):
            self._show_error("请选择可用的 Provider 和模型")
            return
        try:
            inputs = self._form.values()
        except SchemaFormError as exc:
            self._show_error(str(exc))
            return
        self._error.hide()
        self._run_button.setEnabled(False)
        self._run_button.setText("运行中…")
        self._result_title.setText("任务正在执行")
        try:
            task = await self._tasks_service.create_and_run(
                CreateTask(
                    provider_id=provider_id,
                    model_id=model.id,
                    operation=operation,
                    inputs=inputs,
                )
            )
            artifacts = await self._tasks_service.list_artifacts(task.id)
            self._result_title.setText(_status_text(task.status.value))
            self._result_meta.setText(f"Task {task.id}  ·  {len(artifacts)} 个产物  ·  已持久化")
            self._result.setPlainText(_json_text(task.normalized_output or {}))
            self.task_changed.emit(task.id)
        except Exception as exc:
            self._logger.exception("playground_task_failed")
            self._show_error(str(exc) or "任务运行失败")
            self._result_title.setText("运行失败")
        finally:
            self._run_button.setText("运行任务  →")
            self._run_button.setEnabled(True)

    def _selected_model(self) -> Model | None:
        model_id = self._model.currentData()
        return next((model for model in self._models if model.id == model_id), None)

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.show()

    def _start(self, operation: Coroutine[Any, Any, object]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            operation.close()
            return
        task = loop.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _labeled(title: str, field: QWidget) -> QWidget:
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    label = QLabel(title)
    label.setObjectName("FormLabel")
    layout.addWidget(label)
    layout.addWidget(field)
    return host


def _json_text(value: object) -> str:
    return json.dumps(_plain_json(value), ensure_ascii=False, indent=2, sort_keys=True)


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(child) for child in value]
    return value


def _status_text(status: str) -> str:
    return {
        "SUCCESS": "运行成功",
        "FAILED": "运行失败",
        "CANCELED": "已取消",
        "TIMED_OUT": "已超时",
        "NEEDS_ATTENTION": "需要处理",
    }.get(status, status)
