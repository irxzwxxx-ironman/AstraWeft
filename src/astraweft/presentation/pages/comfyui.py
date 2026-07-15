"""ComfyUI instance and API-template management page."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Coroutine, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from astraweft.application.comfyui import (
    ComfyUIService,
    CreateComfyUIInstance,
    ImportComfyUITemplate,
    UpdateComfyUIInstance,
)
from astraweft.domain.comfyui import ComfyUIHealth, ComfyUIInstance, ComfyUITemplate
from astraweft.presentation.widgets.controls import Badge, BadgeTone, Button, TextInput
from astraweft.presentation.widgets.feedback import EmptyState, Toast, ToastTone

_PORT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


class ComfyUIInstanceDialog(QDialog):
    def __init__(
        self,
        instance: ComfyUIInstance | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ComfyUIInstanceDialog")
        self.setWindowTitle("编辑 ComfyUI" if instance else "添加 ComfyUI")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._instance = instance
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(16)
        title = QLabel("ComfyUI 执行实例")
        title.setObjectName("ContentTitle")
        hint = QLabel("本机可使用 HTTP；远程实例必须使用 HTTPS。AstraWeft 不需要 ComfyUI API Key。")
        hint.setObjectName("BodyText")
        hint.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(hint)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self._name = TextInput("实例名称", placeholder="例如：本机 ComfyUI")
        self._url = TextInput("服务地址", placeholder="http://127.0.0.1:8188")
        self._enabled = QCheckBox("启用此实例")
        self._enabled.setChecked(True if instance is None else instance.enabled)
        if instance is not None:
            self._name.setText(instance.name)
            self._url.setText(instance.base_url)
        form.addRow("名称", self._name)
        form.addRow("地址", self._url)
        form.addRow("", self._enabled)
        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def command(self) -> CreateComfyUIInstance | UpdateComfyUIInstance:
        name = self._name.text().strip()
        url = self._url.text().strip()
        if not name or not url:
            raise ValueError("名称和服务地址不能为空")
        if self._instance is None:
            return CreateComfyUIInstance(name, url, self._enabled.isChecked())
        return UpdateComfyUIInstance(
            self._instance.id,
            name,
            url,
            self._enabled.isChecked(),
        )

    def _accept_checked(self) -> None:
        try:
            self.command()
        except ValueError as exc:
            QMessageBox.warning(self, "配置不完整", str(exc))
            return
        self.accept()


class TemplateImportDialog(QDialog):
    def __init__(
        self,
        prompt: Mapping[str, object],
        default_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("导入 ComfyUI API 模板")
        self.setModal(True)
        self.setMinimumWidth(620)
        self._prompt = prompt
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        title = QLabel("选择工作流入口与成果节点")
        title.setObjectName("ContentTitle")
        hint = QLabel(
            "请从 ComfyUI 导出 API Format JSON。这里可暴露一个常用输入；更多映射可在后续版本编辑。"
        )
        hint.setObjectName("BodyText")
        hint.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(hint)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self._name = TextInput("模板名称")
        self._name.setText(default_name)
        self._input = QComboBox()
        self._input.setObjectName("SelectInput")
        self._input.addItem("不暴露动态输入", None)
        for node_id, input_name, class_type, value in _input_candidates(prompt):
            self._input.addItem(
                f"{node_id} · {class_type} · {input_name}",
                (node_id, input_name, value),
            )
        if self._input.count() > 1:
            self._input.setCurrentIndex(1)
        self._output = QComboBox()
        self._output.setObjectName("SelectInput")
        candidates = _output_candidates(prompt)
        for node_id, class_type in candidates:
            self._output.addItem(f"{node_id} · {class_type}", node_id)
        form.addRow("模板名称", self._name)
        form.addRow("动态输入", self._input)
        form.addRow("成果节点", self._output)
        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导入")
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def values(
        self,
    ) -> tuple[str, Mapping[str, object], Mapping[str, object], tuple[str, ...]]:
        name = self._name.text().strip()
        output = self._output.currentData()
        if not name or not isinstance(output, str):
            raise ValueError("模板名称和成果节点不能为空")
        selected = self._input.currentData()
        if not isinstance(selected, tuple) or len(selected) != 3:
            return name, _object_schema({}), {}, (output,)
        node_id, input_name, value = selected
        if not isinstance(node_id, str) or not isinstance(input_name, str):
            raise ValueError("动态输入映射无效")
        port = "prompt" if input_name == "text" else input_name
        if not _PORT.fullmatch(port):
            port = "input"
        schema = _schema_for_value(value)
        return (
            name,
            _object_schema({port: schema}, required=(port,)),
            {port: {"node_id": node_id, "input_name": input_name}},
            (output,),
        )

    def _accept_checked(self) -> None:
        try:
            self.values()
        except ValueError as exc:
            QMessageBox.warning(self, "无法导入", str(exc))
            return
        self.accept()


class ComfyUIPage(QWidget):
    catalog_changed = Signal()
    health_changed = Signal(str)

    def __init__(self, service: ComfyUIService) -> None:
        super().__init__()
        self.setObjectName("ComfyUIPage")
        self._service = service
        self._tasks: set[asyncio.Task[Any]] = set()
        self._toast: Toast | None = None
        self._logger = logging.getLogger("astraweft.presentation.comfyui")
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 27, 30, 24)
        root.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("ComfyUI")
        title.setObjectName("ContentTitle")
        self._summary = QLabel("读取本地执行实例…")
        self._summary.setObjectName("BodyText")
        titles.addWidget(title)
        titles.addWidget(self._summary)
        header.addLayout(titles)
        header.addStretch(1)
        add = Button("+ 添加 ComfyUI")
        add.clicked.connect(self._open_add)
        refresh = Button("刷新", variant="ghost")
        refresh.clicked.connect(self.request_refresh)
        header.addWidget(add)
        header.addWidget(refresh)
        root.addLayout(header)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._content = QWidget()
        self._list = QVBoxLayout(self._content)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(12)
        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll, 1)
        QTimer.singleShot(0, self.request_refresh)

    def request_refresh(self) -> None:
        self._start(self.refresh())

    async def refresh(self) -> None:
        try:
            instances = await self._service.list_instances()
            templates = {
                instance.id: await self._service.list_templates(instance.id)
                for instance in instances
            }
        except Exception as exc:
            self._handle_error("无法读取 ComfyUI 列表", exc)
            return
        _clear_layout(self._list)
        self._summary.setText(f"{len(instances)} 个实例 · 模板与执行记录保存在本机")
        if not instances:
            empty = EmptyState(
                "◈",
                "还没有 ComfyUI 实例",
                "添加本机 ComfyUI 地址，测试连接后导入 API Format 工作流。",
                action_text="添加 ComfyUI",
            )
            empty.action_requested.connect(self._open_add)
            self._list.addWidget(empty)
            self.health_changed.emit("COMFYUI 未配置")
            return
        if any(item.health is ComfyUIHealth.HEALTHY for item in instances):
            self.health_changed.emit("COMFYUI ONLINE")
        elif any(item.health is ComfyUIHealth.UNAVAILABLE for item in instances):
            self.health_changed.emit("COMFYUI OFFLINE")
        else:
            self.health_changed.emit("COMFYUI 未测试")
        for instance in instances:
            card = _ComfyUICard(instance, templates.get(instance.id, ()))
            card.edit_requested.connect(lambda iid=instance.id: self._start(self._edit(iid)))
            card.test_requested.connect(lambda iid=instance.id: self._start(self._test(iid)))
            card.import_requested.connect(lambda iid=instance.id: self._import_template(iid))
            card.toggle_requested.connect(
                lambda enabled, iid=instance.id: self._start(self._toggle(iid, enabled))
            )
            card.delete_requested.connect(lambda iid=instance.id: self._confirm_delete(iid))
            self._list.addWidget(card)
        self._list.addStretch(1)

    def _open_add(self) -> None:
        dialog = ComfyUIInstanceDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            command = dialog.command()
            if isinstance(command, CreateComfyUIInstance):
                self._start(self._create(command))

    async def _create(self, command: CreateComfyUIInstance) -> None:
        try:
            await self._service.create_instance(command)
            await self.refresh()
            self._notify("ComfyUI 实例已保存", "success")
        except Exception as exc:
            self._handle_error("保存失败", exc)

    async def _edit(self, instance_id: str) -> None:
        try:
            instance = await self._service.get_instance(instance_id)
            dialog = ComfyUIInstanceDialog(instance, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            command = dialog.command()
            if isinstance(command, UpdateComfyUIInstance):
                await self._service.update_instance(command)
            await self.refresh()
            self._notify("ComfyUI 设置已更新", "success")
        except Exception as exc:
            self._handle_error("更新失败", exc)

    async def _test(self, instance_id: str) -> None:
        try:
            result = await self._service.test_connection(instance_id)
            await self.refresh()
            tone: ToastTone = "success" if result.health is ComfyUIHealth.HEALTHY else "danger"
            detail = f" · {result.node_count} 个节点" if result.node_count is not None else ""
            self._notify(f"{result.message}{detail}", tone)
        except Exception as exc:
            self._handle_error("连接测试失败", exc)

    def _import_template(self, instance_id: str) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "导入 ComfyUI API Format",
            "",
            "ComfyUI JSON (*.json);;JSON (*.json)",
        )
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            prompt = _extract_prompt(raw)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            self._handle_error("无法读取模板", exc)
            return
        dialog = TemplateImportDialog(prompt, Path(path).stem, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, input_schema, targets, output_nodes = dialog.values()
        self._start(
            self._save_template(
                ImportComfyUITemplate(
                    instance_id,
                    name,
                    prompt,
                    input_schema,
                    targets,
                    output_nodes,
                )
            )
        )

    async def _save_template(self, command: ImportComfyUITemplate) -> None:
        try:
            await self._service.import_template(command)
            await self.refresh()
            self.catalog_changed.emit()
            self._notify("ComfyUI 模板已导入", "success")
        except Exception as exc:
            self._handle_error("模板导入失败", exc)

    async def _toggle(self, instance_id: str, enabled: bool) -> None:
        try:
            await self._service.set_enabled(instance_id, enabled)
            await self.refresh()
            self._notify("ComfyUI 已启用" if enabled else "ComfyUI 已停用", "info")
        except Exception as exc:
            self._handle_error("更新状态失败", exc)

    def _confirm_delete(self, instance_id: str) -> None:
        answer = QMessageBox.question(
            self,
            "删除 ComfyUI",
            "确认删除这个实例？既有执行记录和本地产物会保留。",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer is QMessageBox.StandardButton.Yes:
            self._start(self._delete(instance_id))

    async def _delete(self, instance_id: str) -> None:
        try:
            await self._service.delete_instance(instance_id)
            await self.refresh()
            self.catalog_changed.emit()
            self._notify("ComfyUI 实例已删除", "success")
        except Exception as exc:
            self._handle_error("删除失败", exc)

    def _start(self, operation: Coroutine[Any, Any, object]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            operation.close()
            return
        task = loop.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _handle_error(self, prefix: str, error: Exception) -> None:
        self._logger.warning(
            "comfyui_ui_operation_failed",
            exc_info=(type(error), error, error.__traceback__),
        )
        self._notify(f"{prefix}：{str(error).strip() or type(error).__name__}", "danger")

    def _notify(self, text: str, tone: ToastTone) -> None:
        if self._toast is not None:
            self._toast.deleteLater()
        self._toast = Toast(text, tone=tone)
        self._toast.setParent(self)
        self._position_toast()
        self._toast.show()
        self._toast.raise_()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._position_toast()

    def _position_toast(self) -> None:
        if self._toast is None:
            return
        self._toast.adjustSize()
        self._toast.move(max(16, self.width() - self._toast.width() - 24), 22)


class _ComfyUICard(QFrame):
    edit_requested = Signal()
    test_requested = Signal()
    import_requested = Signal()
    toggle_requested = Signal(bool)
    delete_requested = Signal()

    def __init__(
        self,
        instance: ComfyUIInstance,
        templates: Sequence[ComfyUITemplate],
    ) -> None:
        super().__init__()
        self.setObjectName("ProviderCard")
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        header = QHBoxLayout()
        mark = QLabel("CU")
        mark.setObjectName("ProviderMark")
        mark.setFixedSize(42, 42)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        names = QVBoxLayout()
        name = QLabel(instance.name)
        name.setObjectName("CardTitle")
        address = QLabel(instance.base_url)
        address.setObjectName("MutedText")
        names.addWidget(name)
        names.addWidget(address)
        header.addWidget(mark)
        header.addLayout(names)
        header.addStretch(1)
        tone, label = _health_badge(instance.health)
        header.addWidget(Badge(label, tone=tone))
        header.addWidget(
            Badge(
                "已启用" if instance.enabled else "已停用",
                tone="info" if instance.enabled else "neutral",
            )
        )
        root.addLayout(header)
        details = QLabel(
            f"版本  {instance.version or '未知'}  ·  模板 {len(templates)} 个"
            + (f"  ·  {', '.join(item.name for item in templates[:4])}" if templates else "")
        )
        details.setObjectName("BodyText")
        details.setWordWrap(True)
        root.addWidget(details)
        actions = QHBoxLayout()
        edit = Button("编辑", variant="ghost")
        test = Button("测试连接", variant="ghost")
        import_button = Button("导入 API 模板")
        toggle = Button("停用" if instance.enabled else "启用", variant="ghost")
        delete = Button("删除", variant="danger")
        edit.clicked.connect(self.edit_requested)
        test.clicked.connect(self.test_requested)
        import_button.clicked.connect(self.import_requested)
        toggle.clicked.connect(lambda: self.toggle_requested.emit(not instance.enabled))
        delete.clicked.connect(self.delete_requested)
        actions.addWidget(edit)
        actions.addWidget(test)
        actions.addWidget(import_button)
        actions.addStretch(1)
        actions.addWidget(toggle)
        actions.addWidget(delete)
        root.addLayout(actions)


def _health_badge(status: ComfyUIHealth) -> tuple[BadgeTone, str]:
    return cast(
        tuple[BadgeTone, str],
        {
            ComfyUIHealth.UNKNOWN: ("neutral", "未测试"),
            ComfyUIHealth.HEALTHY: ("success", "连接正常"),
            ComfyUIHealth.DEGRADED: ("warning", "连接降级"),
            ComfyUIHealth.UNAVAILABLE: ("danger", "连接不可用"),
        }[status],
    )


def _extract_prompt(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("模板根节点必须是 JSON 对象")
    prompt = value.get("prompt")
    candidate = prompt if isinstance(prompt, Mapping) else value
    if not candidate:
        raise ValueError("模板没有节点")
    return {str(key): child for key, child in candidate.items()}


def _input_candidates(
    prompt: Mapping[str, object],
) -> tuple[tuple[str, str, str, object], ...]:
    candidates: list[tuple[str, str, str, object]] = []
    for node_id, raw_node in prompt.items():
        if not isinstance(raw_node, Mapping):
            continue
        class_type = raw_node.get("class_type")
        inputs = raw_node.get("inputs")
        if not isinstance(class_type, str) or not isinstance(inputs, Mapping):
            continue
        for input_name, value in inputs.items():
            if isinstance(input_name, str) and (
                isinstance(value, (str, int, float, bool)) or value is None
            ):
                candidates.append((node_id, input_name, class_type, value))
    candidates.sort(key=lambda item: (item[1] not in {"text", "prompt"}, item[0], item[1]))
    return tuple(candidates)


def _output_candidates(prompt: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    candidates: list[tuple[str, str]] = []
    for node_id, raw_node in prompt.items():
        class_type = raw_node.get("class_type") if isinstance(raw_node, Mapping) else None
        if isinstance(class_type, str):
            candidates.append((node_id, class_type))
    candidates.sort(
        key=lambda item: (
            not any(token in item[1].lower() for token in ("save", "preview", "output")),
            item[0],
        )
    )
    return tuple(candidates)


def _schema_for_value(value: object) -> Mapping[str, object]:
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    return {"type": "string"}


def _object_schema(
    properties: Mapping[str, object],
    *,
    required: tuple[str, ...] = (),
) -> Mapping[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
