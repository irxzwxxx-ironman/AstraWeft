"""Focused dialogs used by the visual workflow editor."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from astraweft.application.workflows import WorkflowNodeDraft
from astraweft.domain.comfyui import ComfyUIInstance, ComfyUITemplate
from astraweft.domain.provider import Model, Provider
from astraweft.domain.workflow import ports_from_schema
from astraweft.presentation.i18n import Translator
from astraweft.presentation.widgets.schema_form import SchemaForm, SchemaFormError


class ProviderNodeDialog(QDialog):
    """Choose one configured Provider model and operation without exposing IDs."""

    def __init__(
        self,
        providers: Sequence[Provider],
        models: Sequence[Model],
        parent: QWidget | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator or Translator()
        self.setWindowTitle(self._translator.text("添加 Provider 节点", "Add Provider Node"))
        self.setModal(True)
        self.setMinimumWidth(440)
        self._providers = tuple(providers)
        self._models = tuple(models)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(16)
        title = QLabel(self._translator.text("选择执行资源", "Select Execution Resource"))
        title.setObjectName("ContentTitle")
        root.addWidget(title)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self._provider = QComboBox()
        self._provider.setObjectName("SelectInput")
        self._model = QComboBox()
        self._model.setObjectName("SelectInput")
        self._operation = QComboBox()
        self._operation.setObjectName("SelectInput")
        self._name = QLineEdit()
        self._name.setObjectName("TextInput")
        self._name.setAccessibleName(
            self._translator.text("Provider 节点名称", "Provider node name")
        )
        self._name.setPlaceholderText(
            self._translator.text("例如：生成分镜", "For example: Generate storyboard")
        )
        for provider in self._providers:
            self._provider.addItem(provider.name, provider.id)
        self._provider.currentIndexChanged.connect(self._refresh_models)
        self._model.currentIndexChanged.connect(self._refresh_operations)
        form.addRow("Provider", self._provider)
        form.addRow(self._translator.text("模型", "Model"), self._model)
        form.addRow(self._translator.text("操作", "Operation"), self._operation)
        form.addRow(self._translator.text("节点名称", "Node name"), self._name)
        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        _translate_buttons(buttons, self._translator, ok=("添加", "Add"))
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh_models()

    def selection(self) -> tuple[str, str, str, str]:
        provider_id = self._provider.currentData()
        model_id = self._model.currentData()
        operation = self._operation.currentData()
        if not all(isinstance(value, str) for value in (provider_id, model_id, operation)):
            raise ValueError(
                self._translator.text(
                    "Provider 节点选择不完整",
                    "Provider node selection is incomplete",
                )
            )
        name = self._name.text().strip() or self._model.currentText()
        return provider_id, model_id, operation, name

    def _refresh_models(self) -> None:
        provider_id = self._provider.currentData()
        self._model.clear()
        for model in self._models:
            if model.provider_id == provider_id and model.enabled and model.available:
                self._model.addItem(model.display_name, model.id)
        self._refresh_operations()

    def _refresh_operations(self) -> None:
        model_id = self._model.currentData()
        self._operation.clear()
        model = next((item for item in self._models if item.id == model_id), None)
        if model is None:
            return
        for operation in sorted(model.operations):
            self._operation.addItem(operation, operation)

    def _accept_checked(self) -> None:
        try:
            self.selection()
        except ValueError as exc:
            QMessageBox.warning(
                self,
                self._translator.text("选择不完整", "Incomplete Selection"),
                str(exc),
            )
            return
        self.accept()


class ComfyUINodeDialog(QDialog):
    """Choose one enabled ComfyUI instance and imported API template."""

    def __init__(
        self,
        instances: Sequence[ComfyUIInstance],
        templates: Sequence[ComfyUITemplate],
        parent: QWidget | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator or Translator()
        self.setWindowTitle(self._translator.text("添加 ComfyUI 节点", "Add ComfyUI Node"))
        self.setModal(True)
        self.setMinimumWidth(460)
        self._instances = tuple(instances)
        self._templates = tuple(templates)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(16)
        title = QLabel(self._translator.text("选择 ComfyUI 模板", "Select ComfyUI Template"))
        title.setObjectName("ContentTitle")
        root.addWidget(title)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self._instance = QComboBox()
        self._instance.setObjectName("SelectInput")
        self._template = QComboBox()
        self._template.setObjectName("SelectInput")
        self._name = QLineEdit()
        self._name.setObjectName("TextInput")
        self._name.setAccessibleName(self._translator.text("ComfyUI 节点名称", "ComfyUI node name"))
        self._name.setPlaceholderText(
            self._translator.text("例如：本地渲染", "For example: Local render")
        )
        for instance in self._instances:
            if instance.enabled:
                self._instance.addItem(instance.name, instance.id)
        self._instance.currentIndexChanged.connect(self._refresh_templates)
        self._template.currentIndexChanged.connect(self._default_name)
        form.addRow("ComfyUI", self._instance)
        form.addRow(self._translator.text("API 模板", "API template"), self._template)
        form.addRow(self._translator.text("节点名称", "Node name"), self._name)
        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        _translate_buttons(buttons, self._translator, ok=("添加", "Add"))
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh_templates()

    def selection(self) -> tuple[str, str, str]:
        instance_id = self._instance.currentData()
        template_id = self._template.currentData()
        if not isinstance(instance_id, str) or not isinstance(template_id, str):
            raise ValueError(
                self._translator.text(
                    "请先配置 ComfyUI 并导入 API 模板",
                    "Configure ComfyUI and import an API template first",
                )
            )
        name = self._name.text().strip() or self._template.currentText()
        return instance_id, template_id, name

    def _refresh_templates(self) -> None:
        instance_id = self._instance.currentData()
        self._template.clear()
        for template in self._templates:
            if template.instance_id == instance_id:
                self._template.addItem(template.name, template.id)
        self._default_name()

    def _default_name(self) -> None:
        if not self._name.text().strip():
            self._name.setText(self._template.currentText())

    def _accept_checked(self) -> None:
        try:
            self.selection()
        except ValueError as exc:
            QMessageBox.warning(
                self,
                self._translator.text("选择不完整", "Incomplete Selection"),
                str(exc),
            )
            return
        self.accept()


class ConnectionDialog(QDialog):
    """Create one explicit source-output to target-input edge."""

    def __init__(
        self,
        nodes: Sequence[WorkflowNodeDraft],
        parent: QWidget | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator or Translator()
        self.setWindowTitle(self._translator.text("连接节点", "Connect Nodes"))
        self.setModal(True)
        self.setMinimumWidth(440)
        self._nodes = tuple(nodes)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self._source = QComboBox()
        self._source_port = QComboBox()
        self._target = QComboBox()
        self._target_port = QComboBox()
        for widget in (self._source, self._source_port, self._target, self._target_port):
            widget.setObjectName("SelectInput")
        for node in self._nodes:
            self._source.addItem(node.name, node.node_key)
            self._target.addItem(node.name, node.node_key)
        self._source.currentIndexChanged.connect(self._refresh_ports)
        self._target.currentIndexChanged.connect(self._refresh_ports)
        form.addRow(self._translator.text("源节点", "Source node"), self._source)
        form.addRow(self._translator.text("输出端口", "Output port"), self._source_port)
        form.addRow(self._translator.text("目标节点", "Target node"), self._target)
        form.addRow(self._translator.text("输入端口", "Input port"), self._target_port)
        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        _translate_buttons(buttons, self._translator, ok=("连接", "Connect"))
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh_ports()

    def selection(self) -> tuple[str, str, str, str]:
        values = (
            self._source.currentData(),
            self._source_port.currentData(),
            self._target.currentData(),
            self._target_port.currentData(),
        )
        if not all(isinstance(value, str) and value for value in values):
            raise ValueError(
                self._translator.text("连接端口选择不完整", "Port selection is incomplete")
            )
        source, source_port, target, target_port = values
        if source == target:
            raise ValueError(
                self._translator.text("节点不能连接到自身", "A node cannot connect to itself")
            )
        return source, source_port, target, target_port

    def _refresh_ports(self) -> None:
        self._source_port.clear()
        self._target_port.clear()
        source = self._node(self._source.currentData())
        target = self._node(self._target.currentData())
        if source is not None:
            for port in ports_from_schema(source.output_schema):
                self._source_port.addItem(port.name, port.name)
        if target is not None:
            for port in ports_from_schema(target.input_schema):
                self._target_port.addItem(port.name, port.name)

    def _node(self, key: object) -> WorkflowNodeDraft | None:
        return next((item for item in self._nodes if item.node_key == key), None)

    def _accept_checked(self) -> None:
        try:
            self.selection()
        except ValueError as exc:
            QMessageBox.warning(
                self,
                self._translator.text("无法连接", "Unable to Connect"),
                str(exc),
            )
            return
        self.accept()


class WorkflowRunDialog(QDialog):
    """JSON Schema-driven input form for starting one published run."""

    def __init__(
        self,
        schema: Mapping[str, object],
        parent: QWidget | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator or Translator()
        self.setWindowTitle(self._translator.text("运行工作流", "Run Workflow"))
        self.setModal(True)
        self.setMinimumWidth(500)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        title = QLabel(self._translator.text("本次运行输入", "Run Inputs"))
        title.setObjectName("ContentTitle")
        subtitle = QLabel(
            self._translator.text(
                "输入会随运行记录保存，用于复现与审计。",
                "Inputs are saved with the run record for reproducibility and audit.",
            )
        )
        subtitle.setObjectName("BodyText")
        root.addWidget(title)
        root.addWidget(subtitle)
        self._form = SchemaForm(schema, translator=self._translator)
        root.addWidget(self._form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        _translate_buttons(buttons, self._translator, ok=("开始运行", "Start run"))
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def values(self) -> dict[str, object]:
        return self._form.values()

    def _accept_checked(self) -> None:
        try:
            self.values()
        except SchemaFormError as exc:
            QMessageBox.warning(
                self,
                self._translator.text("输入无效", "Invalid Input"),
                str(exc),
            )
            return
        self.accept()


def _translate_buttons(
    buttons: QDialogButtonBox,
    translator: Translator,
    *,
    ok: tuple[str, str],
) -> None:
    accept = buttons.button(QDialogButtonBox.StandardButton.Ok)
    cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
    if accept is not None:
        accept.setText(translator.text(*ok))
    if cancel is not None:
        cancel.setText(translator.text("取消", "Cancel"))
