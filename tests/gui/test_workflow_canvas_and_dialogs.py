"""Workflow canvas painting, movement, and focused editor dialog tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QDialog, QMessageBox
from pytestqt.qtbot import QtBot

from astraweft.application.providers import CreateProvider
from astraweft.application.workflows import WorkflowNodeDraft
from astraweft.bootstrap.container import build_app_context
from astraweft.domain.workflow import WorkflowNodeType
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.secrets import SecretValue
from astraweft.presentation.widgets.workflow_canvas import (
    CanvasEdge,
    CanvasNode,
    WorkflowCanvas,
)
from astraweft.presentation.widgets.workflow_dialogs import (
    ConnectionDialog,
    ProviderNodeDialog,
    WorkflowRunDialog,
)


@pytest.mark.gui
@pytest.mark.asyncio
async def test_provider_connection_and_run_dialogs_validate_user_choices(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Dialog Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        models = await context.provider_service.sync_models(provider.id)
        dialog = ProviderNodeDialog((provider,), models)
        qtbot.addWidget(dialog)
        assert dialog._provider.count() == 1
        assert dialog._model.count() == 2
        provider_id, model_id, operation, name = dialog.selection()
        assert provider_id == provider.id
        assert model_id
        assert operation
        assert name
        dialog._name.setText("  Visual node  ")
        assert dialog.selection()[3] == "Visual node"
        dialog._model.clear()
        warnings: list[str] = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda _parent, _title, message: warnings.append(str(message)),
        )
        dialog._accept_checked()
        assert warnings == ["Provider 节点选择不完整"]

        schema = _text_schema()
        first = WorkflowNodeDraft(
            "first",
            WorkflowNodeType.TRANSFORM,
            "First",
            None,
            None,
            None,
            schema,
            schema,
            {"text": {"kind": "constant", "value": "x"}},
            {"kind": "project", "outputs": {"text": "text"}},
        )
        second = replace(first, node_key="second", name="Second")
        connection = ConnectionDialog((first, second))
        qtbot.addWidget(connection)
        connection._target.setCurrentIndex(1)
        assert connection.selection() == ("first", "text", "second", "text")
        connection._target.setCurrentIndex(0)
        connection._accept_checked()
        assert warnings[-1] == "节点不能连接到自身"
        connection._target.setCurrentIndex(1)
        connection._accept_checked()
        assert connection.result() == QDialog.DialogCode.Accepted

        run_dialog = WorkflowRunDialog(schema)
        qtbot.addWidget(run_dialog)
        run_dialog._form.set_values({"text": "hello"})
        assert run_dialog.values() == {"text": "hello"}
        run_dialog._accept_checked()
        assert run_dialog.result() == QDialog.DialogCode.Accepted
    finally:
        await context.close()


@pytest.mark.gui
def test_workflow_canvas_moves_selects_fits_and_paints_statuses(qtbot: QtBot) -> None:
    canvas = WorkflowCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(900, 600)
    canvas.show()
    nodes = (
        CanvasNode("a", "Alpha", "PROVIDER_MODEL", _text_schema(), _text_schema(), 0, 0, "RUNNING"),
        CanvasNode("b", "Beta", "TRANSFORM", _text_schema(), _text_schema(), 330, 40, "SUCCESS"),
        CanvasNode("c", "Gamma", "TRANSFORM", _text_schema(), _text_schema(), 660, 180, "FAILED"),
        CanvasNode("d", "Delta", "TRANSFORM", _text_schema(), _text_schema(), 330, 270, "PENDING"),
    )
    edges = (
        CanvasEdge("a", "text", "b", "text"),
        CanvasEdge("b", "text", "c", "text"),
    )
    moved: list[tuple[str, int, int]] = []
    selected: list[str] = []
    canvas.node_moved.connect(lambda key, x, y: moved.append((key, x, y)))
    canvas.node_selected.connect(selected.append)
    canvas.set_graph(nodes, edges, editable=True)

    assert len(canvas._items) == 4
    assert len(canvas._edge_items) == 2
    canvas.select_node("b")
    assert canvas.selected_key() == "b"
    assert selected[-1] == "b"
    canvas._items["a"].setPos(QPointF(48, 64))
    assert moved[-1] == ("a", 48, 64)
    canvas.fit_graph()
    canvas.centerOn(canvas._items["a"])

    image = QPixmap(canvas.size())
    image.fill()
    painter = QPainter(image)
    canvas.render(painter)
    painter.end()
    assert not image.isNull()

    canvas.set_graph((), (), editable=False)
    assert canvas.selected_key() is None
    canvas.fit_graph()


def _text_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
