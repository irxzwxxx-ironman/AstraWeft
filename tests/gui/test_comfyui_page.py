"""ComfyUI instance, template, and visual workflow node GUI tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog, QLabel, QMessageBox
from pytestqt.qtbot import QtBot

from astraweft.application.comfyui import CreateComfyUIInstance, ImportComfyUITemplate
from astraweft.application.workflows import CreateWorkflow
from astraweft.bootstrap.container import build_app_context
from astraweft.domain.comfyui import ComfyUIHealth, ComfyUIInstance
from astraweft.domain.workflow import WorkflowNodeType
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.comfyui import ComfyUIClient, ComfyUIProbe
from astraweft.presentation.pages.comfyui import (
    ComfyUIInstanceDialog,
    ComfyUIPage,
    TemplateImportDialog,
    _extract_prompt,
    _health_badge,
    _input_candidates,
    _object_schema,
    _output_candidates,
    _schema_for_value,
)
from astraweft.presentation.pages.workflows import WorkflowPage
from astraweft.presentation.widgets.workflow_dialogs import ComfyUINodeDialog


class _HealthyProbeClient:
    unavailable = False

    async def probe(self, _instance: ComfyUIInstance) -> ComfyUIProbe:
        if self.unavailable:
            raise OSError("offline")
        return ComfyUIProbe("0.3.50", "3.12", {"node_count": 42}, "a" * 64)

    async def close(self) -> None:
        return None


@pytest.mark.gui
@pytest.mark.asyncio
async def test_comfyui_page_and_workflow_palette_use_imported_template(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    page = ComfyUIPage(context.comfyui_service)
    workflow_page = WorkflowPage(
        context.workflow_service,
        context.workflow_execution,
        context.provider_service,
        context.comfyui_service,
    )
    qtbot.addWidget(page)
    qtbot.addWidget(workflow_page)
    page.show()
    workflow_page.show()
    try:
        await page.refresh()
        assert "还没有 ComfyUI 实例" in [label.text() for label in page.findChildren(QLabel)]
        await page._create(CreateComfyUIInstance("GUI ComfyUI", "http://127.0.0.1:8188"))
        instance = (await context.comfyui_service.list_instances())[0]

        prompt = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello"}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        }
        template_dialog = TemplateImportDialog(prompt, "GUI Poster")
        qtbot.addWidget(template_dialog)
        name, schema, targets, outputs = template_dialog.values()
        assert name == "GUI Poster"
        assert "prompt" in schema["properties"]  # type: ignore[operator]
        assert targets["prompt"] == {"node_id": "1", "input_name": "text"}
        assert outputs == ("9",)

        template = await context.comfyui_service.import_template(
            ImportComfyUITemplate(
                instance.id,
                name,
                prompt,
                schema,
                targets,
                outputs,
            )
        )
        await page.refresh()
        assert any("模板 1 个" in label.text() for label in page.findChildren(QLabel))

        instance_dialog = ComfyUIInstanceDialog(instance)
        qtbot.addWidget(instance_dialog)
        update = instance_dialog.command()
        assert update.instance_id == instance.id  # type: ignore[union-attr]

        choose = ComfyUINodeDialog((instance,), (template,))
        qtbot.addWidget(choose)
        assert choose.selection() == (instance.id, template.id, template.name)

        created = await context.workflow_service.create(CreateWorkflow("GUI Comfy flow"))
        workflow_page._load_snapshot(created)
        with monkeypatch.context() as patch:
            patch.setattr(ComfyUINodeDialog, "exec", lambda _self: QDialog.DialogCode.Accepted)
            patch.setattr(
                ComfyUINodeDialog,
                "selection",
                lambda _self: (instance.id, template.id, "Comfy render"),
            )
            await workflow_page._add_comfyui_node()
        assert len(workflow_page._nodes) == 1
        node = workflow_page._nodes[0]
        assert node.node_type is WorkflowNodeType.COMFYUI
        assert node.config["template_checksum"] == template.checksum
        assert "artifacts" in node.output_schema["properties"]  # type: ignore[operator]
    finally:
        page_tasks = tuple(page._tasks)
        workflow_tasks = tuple(workflow_page._tasks)
        if page_tasks or workflow_tasks:
            await asyncio.gather(*page_tasks, *workflow_tasks)
        await context.close()


@pytest.mark.gui
@pytest.mark.asyncio
async def test_comfyui_page_actions_dialog_validation_and_helpers(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _HealthyProbeClient()
    context = await build_app_context(
        tmp_path,
        secret_store_override=SessionSecretStore(),
        comfyui_client_override=cast(ComfyUIClient, client),
    )
    page = ComfyUIPage(context.comfyui_service)
    qtbot.addWidget(page)
    page.show()
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(str(message)),
    )
    try:
        blank = ComfyUIInstanceDialog()
        qtbot.addWidget(blank)
        with pytest.raises(ValueError, match="不能为空"):
            blank.command()
        blank._accept_checked()
        assert warnings
        blank._name.setText("Dialog Local")
        blank._url.setText("http://localhost:8188")
        assert isinstance(blank.command(), CreateComfyUIInstance)

        empty_template = TemplateImportDialog({}, "")
        qtbot.addWidget(empty_template)
        with pytest.raises(ValueError, match="不能为空"):
            empty_template.values()
        empty_template._accept_checked()
        prompt = {
            "1": {
                "class_type": "TextNode",
                "inputs": {"bad name": 7, "enabled": True, "strength": 1.5},
            },
            "9": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}},
            "bad": "ignored",
        }
        dialog = TemplateImportDialog(prompt, "Helper Template")
        qtbot.addWidget(dialog)
        values = dialog.values()
        properties = values[1]["properties"]
        assert isinstance(properties, Mapping)
        assert next(iter(properties)) == "input"
        dialog._input.setCurrentIndex(0)
        assert dialog.values()[2] == {}
        assert _extract_prompt({"prompt": prompt}) == prompt
        with pytest.raises(ValueError):
            _extract_prompt([])
        with pytest.raises(ValueError):
            _extract_prompt({})
        assert len(_input_candidates(prompt)) == 3
        assert _output_candidates(prompt)[0] == ("9", "PreviewImage")
        assert _schema_for_value(True) == {"type": "boolean"}
        assert _schema_for_value(1) == {"type": "integer"}
        assert _schema_for_value(1.5) == {"type": "number"}
        assert _schema_for_value(None) == {"type": "string"}
        assert _object_schema({}, required=("x",))["required"] == ["x"]
        assert _health_badge(ComfyUIHealth.DEGRADED) == ("warning", "连接降级")

        health_states: list[str] = []
        catalog_events: list[bool] = []
        page.health_changed.connect(health_states.append)
        page.catalog_changed.connect(lambda: catalog_events.append(True))
        await page._create(CreateComfyUIInstance("Action Local", "http://localhost:8188"))
        instance = (await context.comfyui_service.list_instances())[0]
        await page._test(instance.id)
        assert health_states[-1] == "COMFYUI ONLINE"
        await page._toggle(instance.id, False)
        assert (await context.comfyui_service.get_instance(instance.id)).enabled is False
        await page._toggle(instance.id, True)

        monkeypatch.setattr(
            ComfyUIInstanceDialog,
            "exec",
            lambda _self: QDialog.DialogCode.Accepted,
        )
        monkeypatch.setattr(
            TemplateImportDialog,
            "exec",
            lambda _self: QDialog.DialogCode.Accepted,
        )
        await page._edit(instance.id)
        await page._edit("missing")
        assert page._toast is not None

        template_path = tmp_path / "Imported API.json"
        template_path.write_text(
            json.dumps({"prompt": {"1": prompt["1"], "9": prompt["9"]}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileName",
            lambda *_args: (str(template_path), "ComfyUI JSON (*.json)"),
        )
        page._import_template(instance.id)
        if page._tasks:
            await asyncio.gather(*tuple(page._tasks))
        assert catalog_events
        assert len(await context.comfyui_service.list_templates(instance.id)) == 1

        monkeypatch.setattr(
            ComfyUIInstanceDialog,
            "command",
            lambda _self: CreateComfyUIInstance("Opened Local", "http://localhost:8189"),
        )
        page._open_add()
        if page._tasks:
            await asyncio.gather(*tuple(page._tasks))
        assert len(await context.comfyui_service.list_instances()) == 2

        client.unavailable = True
        await page._test(instance.id)
        assert health_states[-1] == "COMFYUI OFFLINE"
        await page._create(CreateComfyUIInstance("Opened Local", "http://localhost:8190"))
        assert page._toast is not None

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args: QMessageBox.StandardButton.Yes,
        )
        page._confirm_delete(instance.id)
        if page._tasks:
            await asyncio.gather(*tuple(page._tasks))
        assert catalog_events[-1] is True

        page._notify("replacement", "info")
        page.resize(900, 600)
        page._position_toast()
        page._import_template("missing")
    finally:
        if page._tasks:
            await asyncio.gather(*tuple(page._tasks), return_exceptions=True)
        await context.close()
