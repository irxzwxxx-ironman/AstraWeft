"""GUI task surfaces against the real durable Mock runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from astraweft.application.providers import CreateProvider
from astraweft.bootstrap.container import build_app_context
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.secrets import SecretValue
from astraweft.presentation.pages.artifacts import ArtifactsPage
from astraweft.presentation.pages.costs import CostAnalysisPage
from astraweft.presentation.pages.dashboard import DashboardPage
from astraweft.presentation.pages.logs import RequestLogsPage
from astraweft.presentation.pages.playground import PlaygroundPage
from astraweft.presentation.pages.tasks import TaskCenterPage


@pytest.mark.gui
@pytest.mark.asyncio
async def test_playground_task_log_and_artifact_pages_share_real_local_data(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await build_app_context(
        tmp_path,
        secret_store_override=SessionSecretStore(),
    )
    playground = PlaygroundPage(context.provider_service, context.task_service)
    task_page = TaskCenterPage(context.task_service, context.query_service)
    logs_page = RequestLogsPage(context.task_service, context.query_service)
    artifacts_page = ArtifactsPage(
        context.task_service,
        context.paths.artifact_dir,
        context.query_service,
    )
    dashboard = DashboardPage(
        context.presentation_status(),
        context.provider_service,
        context.task_service,
        context.query_service,
    )
    costs_page = CostAnalysisPage(context.query_service)
    for page in (playground, task_page, logs_page, artifacts_page, dashboard, costs_page):
        qtbot.addWidget(page)
        page.show()
    try:
        await task_page._refresh()
        assert not task_page._cancel.isEnabled()
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="GUI Runtime",
                settings={"response_mode": "accepted", "catalog_revision": 2},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        await context.provider_service.sync_models(provider.id)
        await playground._refresh()
        video = next(
            model for model in playground._models if model.remote_model_id == "mock-video-v1"
        )
        for index in range(playground._model.count()):
            if playground._model.itemData(index) == video.id:
                playground._model.setCurrentIndex(index)
                break
        assert playground._form is not None
        playground._form.set_values({"prompt": "GUI local artifact"})
        await playground._run()

        await task_page._refresh()
        await logs_page._refresh()
        await artifacts_page._refresh()
        await dashboard._refresh()
        await costs_page._refresh()

        assert task_page._table.model() is not None
        assert task_page._table.model().rowCount() == 1
        assert "成功" in task_page._summary.text() or "进行中" in task_page._summary.text()
        assert logs_page._table.model() is not None
        assert logs_page._table.model().rowCount() == 3
        assert artifacts_page._table.model() is not None
        assert artifacts_page._table.model().rowCount() == 1
        assert "1 个已校验产物" in artifacts_page._summary.text()
        assert playground._result_title.text() == "运行成功"
        assert dashboard._calls._value_widget.text() == "3"
        assert dashboard._success._value_widget.text() == "100.0%"
        assert dashboard._cost._value_widget.text().startswith("USD ")
        assert "1 个已配置" in dashboard._provider_summary.text()
        assert dashboard._hero_destination == "playground"
        assert "Playground" in dashboard._hero_action.text()
        assert "1 个已配置" in dashboard._provider_health._value_widget.text()
        assert not task_page._cancel.isEnabled()
        assert costs_page._table.model() is not None
        assert costs_page._table.model().rowCount() == 1
        assert costs_page._known._value_widget.text().startswith("USD ")
        assert costs_page._unknown._value_widget.text() == "2"
        artifacts_page._show_detail(0)
        assert "VIDEO" in artifacts_page._preview.text()
        assert '"file_exists": true' in artifacts_page._metadata.toPlainText()

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args: QMessageBox.StandardButton.Yes,
        )
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda *_args: QMessageBox.StandardButton.Yes,
        )
        artifact = artifacts_page._artifacts[0]
        await artifacts_page._apply_lifecycle(artifact)
        assert not await context.task_service.list_artifacts()

        artifacts_page._toggle_trash()
        await artifacts_page._refresh()
        trashed = artifacts_page._artifacts[0]
        await artifacts_page._apply_lifecycle(trashed)
        assert await context.task_service.list_artifacts()

        artifacts_page._toggle_trash()
        await artifacts_page._refresh()
        active = artifacts_page._artifacts[0]
        await artifacts_page._apply_lifecycle(active)
        artifacts_page._toggle_trash()
        await artifacts_page._refresh()
        await artifacts_page._purge_artifact(artifacts_page._artifacts[0])
        assert not await context.task_service.list_trashed_artifacts()
    finally:
        await asyncio.sleep(0)
        pending = tuple(artifacts_page._tasks)
        if pending:
            await asyncio.gather(*pending)
        await context.close()
