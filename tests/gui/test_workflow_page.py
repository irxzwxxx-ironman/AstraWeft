"""Visual workflow draft, publication, canvas, and run observer tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox
from pytestqt.qtbot import QtBot

from astraweft.application.providers import CreateProvider
from astraweft.application.workflows import CreateWorkflow, StartWorkflowRun
from astraweft.bootstrap.container import build_app_context
from astraweft.domain.workflow import WorkflowRunStatus, WorkflowVersionStatus
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.secrets import SecretValue
from astraweft.presentation.pages.workflows import WorkflowPage
from astraweft.presentation.widgets.workflow_dialogs import (
    ConnectionDialog,
    ProviderNodeDialog,
)


@pytest.mark.gui
@pytest.mark.asyncio
async def test_workflow_page_edits_publishes_and_observes_transform_run(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    page = WorkflowPage(
        context.workflow_service,
        context.workflow_execution,
        context.provider_service,
    )
    qtbot.addWidget(page)
    page.show()
    try:
        await page._refresh_list()
        assert page._list_empty.isVisible()

        created = await context.workflow_service.create(CreateWorkflow("GUI Transform"))
        page._load_snapshot(created)
        assert page._stack.currentIndex() == 1
        assert "阻止发布" in page._issue_summary.text()
        assert page._canvas._items == {}

        page._add_transform_node()
        assert page._dirty
        assert page._autosave_timer.isActive()
        assert len(page._canvas._items) == 1
        assert "未保存" in page._editor_meta.text()

        saved = await page._save_draft()
        assert saved.issues == ()
        assert page._issue_summary.text() == "✓ 可以发布"
        published = await context.workflow_service.publish(saved.version.id)
        page._load_snapshot(published)
        assert published.version.status is WorkflowVersionStatus.PUBLISHED
        assert not page._add_transform_button.isEnabled()
        assert page._run_button.isEnabled()

        started = await context.workflow_execution.start(
            StartWorkflowRun(published.version.id, {"text": "AstraWeft"})
        )
        completed = await context.workflow_execution.advance(started.run.id)
        assert completed.run.status is WorkflowRunStatus.SUCCESS
        page._run_snapshot = completed
        page._run_id = completed.run.id
        page._render_observer()
        page._stack.setCurrentIndex(2)
        assert page._run_nodes.model() is not None
        assert page._run_nodes.model().rowCount() == 1
        assert "AstraWeft" in page._run_detail.toPlainText()
        assert len(page._run_canvas._items) == 1

        next_draft = await context.workflow_service.create_draft(published.workflow.id)
        page._load_snapshot(next_draft)
        assert next_draft.version.status is WorkflowVersionStatus.DRAFT
        assert page._add_transform_button.isEnabled()

        await page._refresh_list()
        assert page._list_table.model() is not None
        assert page._list_table.model().rowCount() == 1
    finally:
        await asyncio.sleep(0)
        tasks = tuple(page._tasks)
        if tasks:
            await asyncio.gather(*tasks)
        await context.close()


@pytest.mark.gui
@pytest.mark.asyncio
async def test_workflow_page_provider_edit_connect_history_export_and_cancel(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    page = WorkflowPage(
        context.workflow_service,
        context.workflow_execution,
        context.provider_service,
    )
    qtbot.addWidget(page)
    page.show()
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="GUI Editor Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = next(
            item
            for item in await context.provider_service.sync_models(provider.id)
            if item.remote_model_id == "mock-text-v1"
        )
        created = await context.workflow_service.create(CreateWorkflow("GUI Provider Flow"))
        page._load_snapshot(created)
        with monkeypatch.context() as patch:
            patch.setattr(
                ProviderNodeDialog,
                "exec",
                lambda _self: QDialog.DialogCode.Accepted,
            )
            patch.setattr(
                ProviderNodeDialog,
                "selection",
                lambda _self: (provider.id, model.id, "text.generate", "Generate"),
            )
            await page._add_provider_node()
        page._add_transform_node()
        assert {node.node_key for node in page._nodes} == {"provider_1", "transform_1"}

        page._canvas.select_node("provider_1")
        page._node_name.setText("Generate story")
        page._rename_selected()
        assert page._nodes[0].name == "Generate story"
        page._move_node("provider_1", 44, 55)
        assert (page._nodes[0].position_x, page._nodes[0].position_y) == (44, 55)

        with monkeypatch.context() as patch:
            patch.setattr(
                ConnectionDialog,
                "exec",
                lambda _self: QDialog.DialogCode.Accepted,
            )
            patch.setattr(
                ConnectionDialog,
                "selection",
                lambda _self: ("provider_1", "text", "transform_1", "text"),
            )
            page._connect_nodes()
        assert len(page._edges) == 1
        transform = next(node for node in page._nodes if node.node_key == "transform_1")
        assert "text" not in transform.input_bindings
        page._canvas.select_node("transform_1")
        page._set_selected_output()

        page._add_transform_node()
        page._canvas.select_node("transform_2")
        page._delete_selected()
        assert len(page._nodes) == 2
        page._canvas.select_node("transform_1")
        page._set_selected_output()

        saved = await page._save_draft()
        assert saved.issues == ()
        published = await context.workflow_service.publish(saved.version.id)
        page._load_snapshot(published)
        with monkeypatch.context() as patch:
            patch.setattr(
                QInputDialog,
                "getItem",
                lambda *_args, **_kwargs: (
                    f"v{published.version.version_no} · 已发布 · {published.version.checksum[:10]}",
                    True,
                ),
            )
            await page._open_history()
        assert page._snapshot is not None
        assert page._snapshot.version.id == published.version.id

        await page._start_run(
            published.version.id,
            {"prompt": "GUI run"},
        )
        assert page._run_snapshot is not None
        provider_run = next(
            item for item in page._run_snapshot.node_runs if item.node_key == "provider_1"
        )
        assert provider_run.task_id is not None, (
            provider_run.error_code,
            provider_run.error_message,
            provider_run.resolved_input,
        )
        await context.task_service.run_until_terminal(provider_run.task_id)
        await context.workflow_execution.advance(page._run_snapshot.run.id)
        await page._refresh_observer()
        assert page._run_snapshot.run.status.value == WorkflowRunStatus.SUCCESS.value
        page._show_run_node("transform_1")
        assert "transform_1" in page._run_detail.toPlainText()

        page._stack.setCurrentIndex(0)
        await page._refresh_list()
        page._list_table.selectRow(0)
        exported_path = tmp_path / "exported-workflow.json"
        with monkeypatch.context() as patch:
            patch.setattr(
                QFileDialog,
                "getSaveFileName",
                lambda *_args, **_kwargs: (str(exported_path), "json"),
            )
            await page._export_selected()
        assert exported_path.read_text(encoding="utf-8").startswith("{")
        await page._import_path(exported_path)
        assert page._snapshot is not None
        assert page._snapshot.version.id == published.version.id

        cancel_started = await context.workflow_execution.start(
            StartWorkflowRun(
                published.version.id,
                {"prompt": "cancel"},
            )
        )
        await context.workflow_execution.advance(cancel_started.run.id)
        page._run_id = cancel_started.run.id
        await page._cancel_current_run()
        assert page._run_snapshot is not None
        assert page._run_snapshot.run.status.value == WorkflowRunStatus.CANCELED.value

        page._dirty = True
        page._stack.setCurrentIndex(1)
        with monkeypatch.context() as patch:
            patch.setattr(
                QMessageBox,
                "question",
                lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
            )
            page._show_list()
            assert page._stack.currentIndex() == 1
    finally:
        page._autosave_timer.stop()
        page._observer_timer.stop()
        await asyncio.sleep(0)
        tasks = tuple(page._tasks)
        if tasks:
            await asyncio.gather(*tasks)
        await context.close()
