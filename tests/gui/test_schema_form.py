"""Dynamic Provider form rendering and secret-field GUI tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QWidget,
)
from pytestqt.qtbot import QtBot

from astraweft.infrastructure.providers import EntryPointProviderRegistry
from astraweft.ports.provider_plugins import PluginRecord
from astraweft.presentation.i18n import Translator
from astraweft.presentation.pages.providers import ProviderDialog
from astraweft.presentation.widgets.schema_form import (
    SchemaForm,
    SchemaFormError,
    _field_value,
)


def _mock_record() -> PluginRecord:
    registry = EntryPointProviderRegistry()
    return next(
        record
        for record in registry.discover()
        if record.descriptor is not None and record.descriptor.name == "AstraWeft Mock Provider"
    )


def _openai_record() -> PluginRecord:
    registry = EntryPointProviderRegistry()
    return next(
        record
        for record in registry.discover()
        if record.descriptor is not None
        and record.descriptor.plugin_id == "com.openai.api-provider"
    )


@pytest.mark.gui
def test_schema_form_renders_defaults_and_typed_values(qtbot: QtBot) -> None:
    descriptor = _mock_record().descriptor
    assert descriptor is not None
    form = SchemaForm(descriptor.settings_schema, descriptor.settings_ui_schema)
    qtbot.addWidget(form)
    form.show()

    values = form.values()
    assert values == {
        "mode": "healthy",
        "response_mode": "completed",
        "catalog_revision": 1,
        "delay_ms": 0,
    }
    catalog = next(
        field
        for field in form.findChildren(QComboBox)
        if field.accessibleName() == "catalog_revision"
    )
    delay = next(
        field for field in form.findChildren(QSpinBox) if field.accessibleName() == "delay_ms"
    )
    catalog.setCurrentIndex(1)
    delay.setValue(75)
    assert form.values()["catalog_revision"] == 2
    assert form.values()["delay_ms"] == 75


@pytest.mark.gui
def test_schema_form_resolves_optional_plugin_localization_extension(qtbot: QtBot) -> None:
    descriptor = _mock_record().descriptor
    assert descriptor is not None
    form = SchemaForm(
        descriptor.settings_schema,
        descriptor.settings_ui_schema,
        translator=Translator("en_US"),
    )
    qtbot.addWidget(form)

    labels = {label.text() for label in form.findChildren(QLabel)}
    assert "Failure mode" in labels
    assert "Task mode" in labels
    assert "Model catalog revision" in labels
    assert "Simulated latency (ms)" in labels
    assert not labels & {"故障模式", "任务模式", "模型目录版本", "模拟延迟 (毫秒)"}


@pytest.mark.gui
def test_secret_schema_uses_password_field_and_safe_value(qtbot: QtBot) -> None:
    descriptor = _mock_record().descriptor
    assert descriptor is not None
    form = SchemaForm(descriptor.credential_schema, secret_mode=True)
    qtbot.addWidget(form)
    secret = form.findChild(QLineEdit, "TextInput")
    assert secret is not None
    assert secret.echoMode() is QLineEdit.EchoMode.Password

    with pytest.raises(SchemaFormError, match="凭据字段"):
        form.secret_values(required=True)
    secret.setText("TOP_SECRET_CANARY")
    values = form.secret_values(required=True)
    assert values is not None
    assert "TOP_SECRET_CANARY" not in repr(values)
    assert values["api_key"].reveal() == "TOP_SECRET_CANARY"
    assert form.secret_values(required=False) is not None


@pytest.mark.gui
def test_provider_dialog_is_generated_from_descriptor(qtbot: QtBot) -> None:
    record = _mock_record()
    dialog = ProviderDialog((record,))
    qtbot.addWidget(dialog)
    api_key = next(
        field for field in dialog.findChildren(QLineEdit) if field.accessibleName() == "api_key"
    )
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    api_key.setText("mock-valid-key")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted

    command = dialog.create_command()
    assert record.descriptor is not None
    assert command.plugin_id == record.descriptor.plugin_id
    assert command.settings["mode"] == "healthy"
    assert command.credentials["api_key"].reveal() == "mock-valid-key"


@pytest.mark.gui
def test_openai_dialog_stays_local_without_key_and_locks_official_endpoint(qtbot: QtBot) -> None:
    dialog = ProviderDialog((_openai_record(),))
    qtbot.addWidget(dialog)
    endpoint = next(
        field
        for field in dialog.findChildren(QLineEdit)
        if field.text() == "https://api.openai.com/v1"
    )
    api_key = next(
        field for field in dialog.findChildren(QLineEdit) if field.accessibleName() == "api_key"
    )

    assert endpoint.isReadOnly()
    assert "插件固定" in endpoint.toolTip()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    api_key.setText("sk-offline-dialog")
    dialog.accept()

    command = dialog.create_command()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert command.plugin_id == "com.openai.api-provider"
    assert command.settings == {"request_timeout_seconds": 60.0}
    assert command.endpoint == "https://api.openai.com/v1"


def test_core_presentation_has_no_mock_plugin_id_branch() -> None:
    source_root = Path(__file__).parents[2] / "src/astraweft"
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_root.rglob("*.py"))
    assert "dev.astraweft.mock-provider" not in source


@pytest.mark.gui
def test_schema_form_supports_all_primitive_widgets_and_initial_values(qtbot: QtBot) -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "enabled": {
                "type": "boolean",
                "title": "Enabled",
                "description": "Feature flag",
                "default": False,
            },
            "count": {"type": "integer", "minimum": 1, "maximum": 9, "default": 2},
            "ratio": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "notes": {"type": "string", "minLength": 2, "default": "draft"},
            "optional": {"type": "string"},
        },
        "required": ["notes"],
        "additionalProperties": False,
    }
    form = SchemaForm(
        schema,
        {
            "ui:order": ["notes", "enabled", "ratio", "count"],
            "notes": {"ui:widget": "textarea"},
        },
        initial={
            "enabled": True,
            "count": 7,
            "ratio": 0.5,
            "notes": "ready",
            "optional": "",
            "unknown": "ignored",
        },
    )
    qtbot.addWidget(form)

    checkbox = form.findChild(QCheckBox, "SchemaCheckBox")
    integer = form.findChild(QSpinBox, "NumberInput")
    number = form.findChild(QDoubleSpinBox, "NumberInput")
    notes = form.findChild(QPlainTextEdit, "TextArea")
    assert checkbox is not None and checkbox.isChecked()
    assert integer is not None and integer.value() == 7
    assert number is not None and number.value() == 0.5
    assert notes is not None and notes.toPlainText() == "ready"
    assert form.values() == {
        "notes": "ready",
        "enabled": True,
        "ratio": 0.5,
        "count": 7,
    }
    notes.clear()
    with pytest.raises(SchemaFormError, match="notes"):
        form.values()
    with pytest.raises(SchemaFormError, match="未知表单控件"):
        _field_value(QWidget())


@pytest.mark.gui
def test_schema_form_rejects_malformed_and_unsupported_schemas(qtbot: QtBot) -> None:
    with pytest.raises(SchemaFormError, match="properties"):
        SchemaForm({"type": "object", "properties": []})
    with pytest.raises(SchemaFormError, match="字段定义"):
        SchemaForm({"type": "object", "properties": {"bad": 1}})
    with pytest.raises(SchemaFormError, match="暂不支持"):
        SchemaForm({"type": "object", "properties": {"items": {"type": "array"}}})

    numeric_secret = SchemaForm(
        {
            "type": "object",
            "properties": {"pin": {"type": "integer", "default": 1}},
        },
        secret_mode=True,
    )
    qtbot.addWidget(numeric_secret)
    with pytest.raises(SchemaFormError, match="必须是字符串"):
        numeric_secret.secret_values(required=True)

    unordered = SchemaForm(
        {
            "type": "object",
            "properties": {"value": {"type": "string", "default": "ok"}},
            "required": "not-a-list",
        },
        {"ui:order": 42},
    )
    qtbot.addWidget(unordered)
    assert unordered.values(validate=False) == {"value": "ok"}
