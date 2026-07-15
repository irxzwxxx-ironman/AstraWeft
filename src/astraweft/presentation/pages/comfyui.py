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
from astraweft.presentation.i18n import Translator
from astraweft.presentation.widgets.controls import Badge, BadgeTone, Button, TextInput
from astraweft.presentation.widgets.feedback import EmptyState, Toast, ToastTone

_PORT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


class ComfyUIInstanceDialog(QDialog):
    def __init__(
        self,
        instance: ComfyUIInstance | None = None,
        parent: QWidget | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator or Translator()
        self.setObjectName("ComfyUIInstanceDialog")
        self.setWindowTitle(
            self._translator.text("编辑 ComfyUI", "Edit ComfyUI")
            if instance
            else self._translator.text("添加 ComfyUI", "Add ComfyUI")
        )
        self.setModal(True)
        self.setMinimumWidth(520)
        self._instance = instance
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(16)
        title = QLabel(self._translator.text("ComfyUI 执行实例", "ComfyUI Execution Instance"))
        title.setObjectName("ContentTitle")
        hint = QLabel(
            self._translator.text(
                "本机可使用 HTTP；远程实例必须使用 HTTPS。AstraWeft 不需要 ComfyUI API Key。",
                "Local instances may use HTTP; remote instances must use HTTPS. AstraWeft does not require a ComfyUI API key.",
            )
        )
        hint.setObjectName("BodyText")
        hint.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(hint)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self._name = TextInput(
            self._translator.text("实例名称", "Instance name"),
            placeholder=self._translator.text("例如：本机 ComfyUI", "For example: Local ComfyUI"),
        )
        self._url = TextInput(
            self._translator.text("服务地址", "Service endpoint"),
            placeholder="http://127.0.0.1:8188",
        )
        self._enabled = QCheckBox(self._translator.text("启用此实例", "Enable this instance"))
        self._enabled.setChecked(True if instance is None else instance.enabled)
        if instance is not None:
            self._name.setText(instance.name)
            self._url.setText(instance.base_url)
        form.addRow(self._translator.text("名称", "Name"), self._name)
        form.addRow(self._translator.text("地址", "Endpoint"), self._url)
        form.addRow("", self._enabled)
        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok is not None:
            ok.setText(self._translator.text("保存", "Save"))
        if cancel is not None:
            cancel.setText(self._translator.text("取消", "Cancel"))
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def command(self) -> CreateComfyUIInstance | UpdateComfyUIInstance:
        name = self._name.text().strip()
        url = self._url.text().strip()
        if not name or not url:
            raise ValueError(
                self._translator.text(
                    "名称和服务地址不能为空",
                    "Name and service endpoint are required",
                )
            )
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
            QMessageBox.warning(
                self,
                self._translator.text("配置不完整", "Incomplete Configuration"),
                str(exc),
            )
            return
        self.accept()


class TemplateImportDialog(QDialog):
    def __init__(
        self,
        prompt: Mapping[str, object],
        default_name: str,
        parent: QWidget | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator or Translator()
        self.setWindowTitle(
            self._translator.text("导入 ComfyUI API 模板", "Import ComfyUI API Template")
        )
        self.setModal(True)
        self.setMinimumWidth(620)
        self._prompt = prompt
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        title = QLabel(
            self._translator.text(
                "选择工作流入口与成果节点",
                "Select Workflow Input and Output Nodes",
            )
        )
        title.setObjectName("ContentTitle")
        hint = QLabel(
            self._translator.text(
                "请从 ComfyUI 导出 API Format JSON。这里可暴露一个常用输入；更多映射可在后续版本编辑。",
                "Export API Format JSON from ComfyUI. One common input can be exposed here; additional mappings can be edited in a later release.",
            )
        )
        hint.setObjectName("BodyText")
        hint.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(hint)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self._name = TextInput(self._translator.text("模板名称", "Template name"))
        self._name.setText(default_name)
        self._input = QComboBox()
        self._input.setObjectName("SelectInput")
        self._input.addItem(self._translator.text("不暴露动态输入", "No dynamic input"), None)
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
        form.addRow(self._translator.text("模板名称", "Template name"), self._name)
        form.addRow(self._translator.text("动态输入", "Dynamic input"), self._input)
        form.addRow(self._translator.text("成果节点", "Output node"), self._output)
        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok is not None:
            ok.setText(self._translator.text("导入", "Import"))
        if cancel is not None:
            cancel.setText(self._translator.text("取消", "Cancel"))
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def values(
        self,
    ) -> tuple[str, Mapping[str, object], Mapping[str, object], tuple[str, ...]]:
        name = self._name.text().strip()
        output = self._output.currentData()
        if not name or not isinstance(output, str):
            raise ValueError(
                self._translator.text(
                    "模板名称和成果节点不能为空",
                    "Template name and output node are required",
                )
            )
        selected = self._input.currentData()
        if not isinstance(selected, tuple) or len(selected) != 3:
            return name, _object_schema({}), {}, (output,)
        node_id, input_name, value = selected
        if not isinstance(node_id, str) or not isinstance(input_name, str):
            raise ValueError(
                self._translator.text("动态输入映射无效", "The dynamic input mapping is invalid")
            )
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
            QMessageBox.warning(
                self,
                self._translator.text("无法导入", "Unable to Import"),
                str(exc),
            )
            return
        self.accept()


class ComfyUIPage(QWidget):
    catalog_changed = Signal()
    health_changed = Signal(str)

    def __init__(self, service: ComfyUIService, translator: Translator | None = None) -> None:
        super().__init__()
        self.setObjectName("ComfyUIPage")
        self._service = service
        self._translator = translator or Translator()
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
        self._summary = QLabel(
            self._translator.text("读取本地执行实例…", "Loading local execution instances…")
        )
        self._summary.setObjectName("BodyText")
        titles.addWidget(title)
        titles.addWidget(self._summary)
        header.addLayout(titles)
        header.addStretch(1)
        add = Button(self._translator.text("+ 添加 ComfyUI", "+ Add ComfyUI"))
        add.clicked.connect(self._open_add)
        refresh = Button(self._translator.text("刷新", "Refresh"), variant="ghost")
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
            self._handle_error(
                self._translator.text("无法读取 ComfyUI 列表", "Unable to load ComfyUI instances"),
                exc,
            )
            return
        _clear_layout(self._list)
        self._summary.setText(
            self._translator.text(
                "{count} 个实例 · 模板与执行记录保存在本机",
                "{count} instances · templates and execution records are stored locally",
                count=self._translator.integer(len(instances)),
            )
        )
        if not instances:
            empty = EmptyState(
                "◈",
                self._translator.text("还没有 ComfyUI 实例", "No ComfyUI instances yet"),
                self._translator.text(
                    "添加本机 ComfyUI 地址，测试连接后导入 API Format 工作流。",
                    "Add a local ComfyUI endpoint, test the connection, then import an API Format workflow.",
                ),
                action_text=self._translator.text("添加 ComfyUI", "Add ComfyUI"),
            )
            empty.action_requested.connect(self._open_add)
            self._list.addWidget(empty)
            self.health_changed.emit(
                self._translator.text("COMFYUI 未配置", "COMFYUI NOT CONFIGURED")
            )
            return
        if any(item.health is ComfyUIHealth.HEALTHY for item in instances):
            self.health_changed.emit("COMFYUI ONLINE")
        elif any(item.health is ComfyUIHealth.UNAVAILABLE for item in instances):
            self.health_changed.emit("COMFYUI OFFLINE")
        else:
            self.health_changed.emit(self._translator.text("COMFYUI 未测试", "COMFYUI NOT TESTED"))
        for instance in instances:
            card = _ComfyUICard(instance, templates.get(instance.id, ()), self._translator)
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
        dialog = ComfyUIInstanceDialog(parent=self, translator=self._translator)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            command = dialog.command()
            if isinstance(command, CreateComfyUIInstance):
                self._start(self._create(command))

    async def _create(self, command: CreateComfyUIInstance) -> None:
        try:
            await self._service.create_instance(command)
            await self.refresh()
            self._notify(
                self._translator.text("ComfyUI 实例已保存", "ComfyUI instance saved"),
                "success",
            )
        except Exception as exc:
            self._handle_error(self._translator.text("保存失败", "Save failed"), exc)

    async def _edit(self, instance_id: str) -> None:
        try:
            instance = await self._service.get_instance(instance_id)
            dialog = ComfyUIInstanceDialog(instance, self, self._translator)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            command = dialog.command()
            if isinstance(command, UpdateComfyUIInstance):
                await self._service.update_instance(command)
            await self.refresh()
            self._notify(
                self._translator.text("ComfyUI 设置已更新", "ComfyUI settings updated"),
                "success",
            )
        except Exception as exc:
            self._handle_error(self._translator.text("更新失败", "Update failed"), exc)

    async def _test(self, instance_id: str) -> None:
        try:
            result = await self._service.test_connection(instance_id)
            await self.refresh()
            tone: ToastTone = "success" if result.health is ComfyUIHealth.HEALTHY else "danger"
            detail = (
                self._translator.text(
                    " · {count} 个节点",
                    " · {count} nodes",
                    count=self._translator.integer(result.node_count),
                )
                if result.node_count is not None
                else ""
            )
            self._notify(f"{result.message}{detail}", tone)
        except Exception as exc:
            self._handle_error(
                self._translator.text("连接测试失败", "Connection test failed"),
                exc,
            )

    def _import_template(self, instance_id: str) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            self._translator.text("导入 ComfyUI API Format", "Import ComfyUI API Format"),
            "",
            "ComfyUI JSON (*.json);;JSON (*.json)",
        )
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            prompt = _extract_prompt(raw, self._translator)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            self._handle_error(
                self._translator.text("无法读取模板", "Unable to read template"),
                exc,
            )
            return
        dialog = TemplateImportDialog(prompt, Path(path).stem, self, self._translator)
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
            self._notify(
                self._translator.text("ComfyUI 模板已导入", "ComfyUI template imported"),
                "success",
            )
        except Exception as exc:
            self._handle_error(
                self._translator.text("模板导入失败", "Template import failed"),
                exc,
            )

    async def _toggle(self, instance_id: str, enabled: bool) -> None:
        try:
            await self._service.set_enabled(instance_id, enabled)
            await self.refresh()
            self._notify(
                self._translator.text("ComfyUI 已启用", "ComfyUI enabled")
                if enabled
                else self._translator.text("ComfyUI 已停用", "ComfyUI disabled"),
                "info",
            )
        except Exception as exc:
            self._handle_error(
                self._translator.text("更新状态失败", "Unable to update state"),
                exc,
            )

    def _confirm_delete(self, instance_id: str) -> None:
        answer = QMessageBox.question(
            self,
            self._translator.text("删除 ComfyUI", "Delete ComfyUI"),
            self._translator.text(
                "确认删除这个实例？既有执行记录和本地产物会保留。",
                "Delete this instance? Existing execution records and local artifacts are retained.",
            ),
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
            self._notify(
                self._translator.text("ComfyUI 实例已删除", "ComfyUI instance deleted"),
                "success",
            )
        except Exception as exc:
            self._handle_error(self._translator.text("删除失败", "Delete failed"), exc)

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
        self._toast = Toast(text, tone=tone, translator=self._translator)
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
        translator: Translator | None = None,
    ) -> None:
        super().__init__()
        self._translator = translator or Translator()
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
        tone, label = _health_badge(instance.health, self._translator)
        header.addWidget(Badge(label, tone=tone))
        header.addWidget(
            Badge(
                self._translator.text("已启用", "Enabled")
                if instance.enabled
                else self._translator.text("已停用", "Disabled"),
                tone="info" if instance.enabled else "neutral",
            )
        )
        root.addLayout(header)
        details = QLabel(
            self._translator.text(
                "版本  {version}  ·  模板 {count} 个",
                "Version  {version}  ·  {count} templates",
                version=instance.version or self._translator.text("未知", "Unknown"),
                count=self._translator.integer(len(templates)),
            )
            + (f"  ·  {', '.join(item.name for item in templates[:4])}" if templates else "")
        )
        details.setObjectName("BodyText")
        details.setWordWrap(True)
        root.addWidget(details)
        actions = QHBoxLayout()
        edit = Button(self._translator.text("编辑", "Edit"), variant="ghost")
        test = Button(self._translator.text("测试连接", "Test connection"), variant="ghost")
        import_button = Button(self._translator.text("导入 API 模板", "Import API template"))
        toggle = Button(
            self._translator.text("停用", "Disable")
            if instance.enabled
            else self._translator.text("启用", "Enable"),
            variant="ghost",
        )
        delete = Button(self._translator.text("删除", "Delete"), variant="danger")
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


def _health_badge(
    status: ComfyUIHealth, translator: Translator | None = None
) -> tuple[BadgeTone, str]:
    translator = translator or Translator()
    return cast(
        tuple[BadgeTone, str],
        {
            ComfyUIHealth.UNKNOWN: ("neutral", translator.text("未测试", "Not tested")),
            ComfyUIHealth.HEALTHY: (
                "success",
                translator.text("连接正常", "Healthy"),
            ),
            ComfyUIHealth.DEGRADED: (
                "warning",
                translator.text("连接降级", "Degraded"),
            ),
            ComfyUIHealth.UNAVAILABLE: (
                "danger",
                translator.text("连接不可用", "Unavailable"),
            ),
        }[status],
    )


def _extract_prompt(value: object, translator: Translator | None = None) -> Mapping[str, object]:
    translator = translator or Translator()
    if not isinstance(value, Mapping):
        raise ValueError(
            translator.text(
                "模板根节点必须是 JSON 对象",
                "The template root must be a JSON object",
            )
        )
    prompt = value.get("prompt")
    candidate = prompt if isinstance(prompt, Mapping) else value
    if not candidate:
        raise ValueError(translator.text("模板没有节点", "The template has no nodes"))
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
