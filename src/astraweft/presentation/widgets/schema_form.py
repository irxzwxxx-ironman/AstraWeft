"""JSON Schema driven Qt forms shared by Provider configuration surfaces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from jsonschema import Draft202012Validator
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from astraweft.ports.secrets import SecretValue
from astraweft.presentation.widgets.controls import TextInput


class SchemaFormError(ValueError):
    """A dynamic form is incomplete or invalid."""


class SchemaForm(QWidget):
    """Render the safe primitive subset of Draft 2020-12 used by Provider plugins."""

    def __init__(
        self,
        schema: Mapping[str, object],
        ui_schema: Mapping[str, object] | None = None,
        *,
        initial: Mapping[str, object] | None = None,
        secret_mode: bool = False,
    ) -> None:
        super().__init__()
        self.setObjectName("SchemaForm")
        self._schema = schema
        self._ui_schema = ui_schema or {}
        self._secret_mode = secret_mode
        self._fields: dict[str, QWidget] = {}
        self._required = _string_set(schema.get("required", ()))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(form)

        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise SchemaFormError("Schema properties 必须是对象")
        for name in _field_order(properties, self._ui_schema):
            field_schema = properties[name]
            if not isinstance(name, str) or not isinstance(field_schema, Mapping):
                raise SchemaFormError("Schema 字段定义无效")
            field = self._build_field(name, field_schema)
            self._fields[name] = field
            title = field_schema.get("title", name)
            label = QLabel(f"{title}{' *' if name in self._required else ''}")
            label.setObjectName("FormLabel")
            form.addRow(label, field)
            description = field_schema.get("description")
            if isinstance(description, str) and description:
                hint = QLabel(description)
                hint.setObjectName("FormHint")
                hint.setWordWrap(True)
                form.addRow("", hint)

        if initial:
            self.set_values(initial)

    def values(self, *, validate: bool = True) -> dict[str, object]:
        output: dict[str, object] = {}
        for name, field in self._fields.items():
            value = _field_value(field)
            if isinstance(value, str) and not value and name not in self._required:
                continue
            output[name] = value
        if validate:
            error = next(
                Draft202012Validator(cast(Mapping[str, Any], self._schema)).iter_errors(output),
                None,
            )
            if error is not None:
                if self._secret_mode:
                    raise SchemaFormError("凭据字段不完整或格式不正确")
                path = ".".join(str(part) for part in error.absolute_path) or "设置"
                raise SchemaFormError(f"{path}：{error.message}")
        return output

    def secret_values(self, *, required: bool) -> dict[str, SecretValue] | None:
        if required:
            raw = self.values(validate=True)
        else:
            raw = self.values(validate=False)
            if not any(value != "" for value in raw.values()):
                return None
            raw = self.values(validate=True)
        secrets: dict[str, SecretValue] = {}
        for name, value in raw.items():
            if not isinstance(value, str):
                raise SchemaFormError("凭据字段必须是字符串")
            secrets[name] = SecretValue(value)
        return secrets

    def set_values(self, values: Mapping[str, object]) -> None:
        for name, value in values.items():
            field = self._fields.get(name)
            if field is not None:
                _set_field_value(field, value)

    def _build_field(self, name: str, schema: Mapping[str, object]) -> QWidget:
        enum = schema.get("enum")
        if isinstance(enum, Sequence) and not isinstance(enum, (str, bytes, bytearray)):
            select = QComboBox()
            select.setObjectName("SelectInput")
            select.setAccessibleName(name)
            for option in enum:
                select.addItem(str(option), userData=option)
            default = schema.get("default")
            for index in range(select.count()):
                if select.itemData(index) == default:
                    select.setCurrentIndex(index)
                    break
            return select

        value_type = schema.get("type", "string")
        if value_type == "boolean":
            checkbox = QCheckBox("启用")
            checkbox.setObjectName("SchemaCheckBox")
            checkbox.setAccessibleName(name)
            checkbox.setChecked(bool(schema.get("default", False)))
            return checkbox
        if value_type == "integer":
            spin = QSpinBox()
            spin.setObjectName("NumberInput")
            spin.setAccessibleName(name)
            spin.setRange(
                _integer(schema.get("minimum"), -1_000_000_000),
                _integer(schema.get("maximum"), 1_000_000_000),
            )
            spin.setValue(_integer(schema.get("default"), 0))
            return spin
        if value_type == "number":
            spin_float = QDoubleSpinBox()
            spin_float.setObjectName("NumberInput")
            spin_float.setAccessibleName(name)
            spin_float.setDecimals(4)
            spin_float.setRange(
                _number(schema.get("minimum"), -1e12), _number(schema.get("maximum"), 1e12)
            )
            spin_float.setValue(_number(schema.get("default"), 0.0))
            return spin_float
        if value_type != "string":
            raise SchemaFormError(f"暂不支持的 Schema 类型：{value_type}")

        ui_options = self._ui_schema.get(name, {})
        widget_name = ui_options.get("ui:widget") if isinstance(ui_options, Mapping) else None
        if widget_name == "textarea":
            area = QPlainTextEdit()
            area.setObjectName("TextArea")
            area.setAccessibleName(name)
            area.setFixedHeight(88)
            default = schema.get("default")
            if isinstance(default, str):
                area.setPlainText(default)
            return area
        line = TextInput(name)
        default = schema.get("default")
        if isinstance(default, str):
            line.setText(default)
        if self._secret_mode or schema.get("x-astraweft-secret") is True:
            line.setEchoMode(QLineEdit.EchoMode.Password)
            line.setClearButtonEnabled(False)
            line.setPlaceholderText("输入后将保存到系统密钥环")
        return line


def _field_order(
    properties: Mapping[object, object], ui_schema: Mapping[str, object]
) -> tuple[str, ...]:
    names = tuple(name for name in properties if isinstance(name, str))
    requested = ui_schema.get("ui:order", ())
    if not isinstance(requested, Sequence) or isinstance(requested, (str, bytes, bytearray)):
        return names
    ordered = [name for name in requested if isinstance(name, str) and name in properties]
    ordered.extend(name for name in names if name not in ordered)
    return tuple(ordered)


def _field_value(field: QWidget) -> object:
    if isinstance(field, QComboBox):
        return field.currentData()
    if isinstance(field, QCheckBox):
        return field.isChecked()
    if isinstance(field, QSpinBox):
        return field.value()
    if isinstance(field, QDoubleSpinBox):
        return field.value()
    if isinstance(field, QPlainTextEdit):
        return field.toPlainText()
    if isinstance(field, QLineEdit):
        return field.text()
    raise SchemaFormError("未知表单控件")


def _set_field_value(field: QWidget, value: object) -> None:
    if isinstance(field, QComboBox):
        for index in range(field.count()):
            if field.itemData(index) == value:
                field.setCurrentIndex(index)
                return
    elif isinstance(field, QCheckBox) and isinstance(value, bool):
        field.setChecked(value)
    elif isinstance(field, QSpinBox) and isinstance(value, int) and not isinstance(value, bool):
        field.setValue(value)
    elif isinstance(field, QDoubleSpinBox) and isinstance(value, (int, float)):
        field.setValue(float(value))
    elif isinstance(field, QPlainTextEdit) and isinstance(value, str):
        field.setPlainText(value)
    elif isinstance(field, QLineEdit) and isinstance(value, str):
        field.setText(value)


def _string_set(value: object) -> frozenset[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, str))


def _integer(value: object, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _number(value: object, fallback: float) -> float:
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else fallback
    )
