"""English localization coverage for the complete product shell and dialogs."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QLabel,
    QLineEdit,
    QTableView,
    QTabWidget,
    QWidget,
)
from pytestqt.qtbot import QtBot

from astraweft.bootstrap.container import build_app_context
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.presentation.i18n import Translator
from astraweft.presentation.main_window import MainWindow
from astraweft.presentation.pages.artifacts import ArtifactsPage
from astraweft.presentation.pages.comfyui import (
    ComfyUIInstanceDialog,
    ComfyUIPage,
    TemplateImportDialog,
)
from astraweft.presentation.pages.costs import CostAnalysisPage
from astraweft.presentation.pages.dashboard import DashboardPage
from astraweft.presentation.pages.logs import RequestLogsPage
from astraweft.presentation.pages.models import ModelsPage
from astraweft.presentation.pages.playground import PlaygroundPage
from astraweft.presentation.pages.providers import (
    PluginManagerDialog,
    ProviderDialog,
    ProviderPage,
)
from astraweft.presentation.pages.settings import SettingsPage
from astraweft.presentation.pages.tasks import TaskCenterPage
from astraweft.presentation.pages.workflows import WorkflowPage
from astraweft.presentation.widgets.feedback import ErrorState, SkeletonBlock, Toast
from astraweft.presentation.widgets.overlays import Drawer
from astraweft.presentation.widgets.schema_form import SchemaForm, SchemaFormError
from astraweft.presentation.widgets.workflow_canvas import WorkflowCanvas
from astraweft.presentation.widgets.workflow_dialogs import (
    ComfyUINodeDialog,
    ConnectionDialog,
    ProviderNodeDialog,
    WorkflowRunDialog,
)


class _LocalizedPage(Protocol):
    _translator: Translator
    _tasks: set[asyncio.Task[Any]]


@pytest.mark.gui
@pytest.mark.asyncio
async def test_english_core_task_journey_uses_one_translator(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    window = MainWindow(
        context.presentation_status(),
        context.provider_service,
        context.task_service,
        context.workflow_service,
        context.workflow_execution,
        context.comfyui_service,
        context.maintenance_service,
        context.query_service,
        context.events,
        context.settings_service,
        system_notifications=False,
        language="en_US",
        app_settings=context.settings,
    )
    qtbot.addWidget(window)
    window.show()
    try:
        expected = {
            "dashboard": (
                DashboardPage,
                "Your creative workspace is ready",
                "Calls today",
            ),
            "providers": (
                ProviderPage,
                "Provider Connections",
                "Plugins are discovered through entry points",
            ),
            "models": (ModelsPage, "Model Catalog", "The model catalog is empty"),
            "playground": (
                PlaygroundPage,
                "Playground",
                "Results, task ID, cost, and artifacts are saved locally",
            ),
            "tasks": (TaskCenterPage, "Task Center", "The queue is empty"),
            "artifacts": (
                ArtifactsPage,
                "Local Artifact Library",
                "Files are verified with SHA-256",
            ),
            "logs": (RequestLogsPage, "Request Logs", "No request logs yet"),
            "comfyui": (
                ComfyUIPage,
                "ComfyUI",
                "Loading local execution instances",
            ),
            "workflows": (
                WorkflowPage,
                "Workflows",
                "Create a blank workflow",
            ),
            "settings": (
                SettingsPage,
                "Settings and Local Data",
                "Backup and restore",
            ),
            "costs": (CostAnalysisPage, "Cost Analysis", "Known cost"),
        }
        for page_id, (page_type, title, empty_copy) in expected.items():
            page = _window_page(window, page_id)
            assert isinstance(page, page_type)
            assert cast(_LocalizedPage, page)._translator is window._translator
            labels = "\n".join(label.text() for label in page.findChildren(QLabel))
            assert title in labels
            assert empty_copy in labels

        pages = [_window_page(window, page_id) for page_id in expected]
        await asyncio.sleep(0)
        pending = tuple(
            task for page in pages for task in cast(_LocalizedPage, page)._tasks if not task.done()
        )
        if pending:
            await asyncio.gather(*pending)

        providers = window._stack.widget(window._page_indexes["providers"])
        dashboard = window._stack.widget(window._page_indexes["dashboard"])
        models = window._stack.widget(window._page_indexes["models"])
        playground = window._stack.widget(window._page_indexes["playground"])
        tasks = window._stack.widget(window._page_indexes["tasks"])
        artifacts = window._stack.widget(window._page_indexes["artifacts"])
        logs = window._stack.widget(window._page_indexes["logs"])
        comfyui = window._stack.widget(window._page_indexes["comfyui"])
        workflows = window._stack.widget(window._page_indexes["workflows"])
        settings = window._stack.widget(window._page_indexes["settings"])
        costs = window._stack.widget(window._page_indexes["costs"])
        assert isinstance(dashboard, DashboardPage)
        assert isinstance(providers, ProviderPage)
        assert isinstance(models, ModelsPage)
        assert isinstance(playground, PlaygroundPage)
        assert isinstance(tasks, TaskCenterPage)
        assert isinstance(artifacts, ArtifactsPage)
        assert isinstance(logs, RequestLogsPage)
        assert isinstance(comfyui, ComfyUIPage)
        assert isinstance(workflows, WorkflowPage)
        assert isinstance(settings, SettingsPage)
        assert isinstance(costs, CostAnalysisPage)
        await dashboard._refresh()
        await providers.refresh()
        await models._refresh()
        await playground._refresh()
        await tasks._refresh()
        await artifacts._refresh()
        await logs._refresh()
        await comfyui.refresh()
        await workflows._refresh_list()
        await settings._refresh_health()
        await costs._refresh()
        assert "No providers yet" in " ".join(
            label.text() for label in providers.findChildren(QLabel)
        )
        assert models._summary.text() == "0 models  ·  0 enabled and available"
        assert tasks._summary.text() == "Showing 0 recent tasks  ·  0 in progress"
        assert artifacts._summary.text() == "Showing 0 recent verified artifacts  ·  0 B"
        assert logs._summary.text() == "Showing 0 recent redacted records"
        assert comfyui._summary.text().startswith("0 instances")
        assert window._comfyui_status.accessibleName() == "COMFYUI NOT CONFIGURED"
        assert workflows._list_summary.text().startswith("0 workflows")
        assert "tables" in settings._health.text()
        assert costs._summary.text().startswith("Last 30 days")

        offenders = {
            page_id: _han_copy(page)
            for page_id, page in ((page_id, _window_page(window, page_id)) for page_id in expected)
            if _han_copy(page)
        }
        assert offenders == {}
        assert "No models are available." in " ".join(
            label.text() for label in playground.findChildren(QLabel)
        )
    finally:
        window.close()
        await context.close()


@pytest.mark.gui
@pytest.mark.asyncio
async def test_english_dialogs_and_shared_states_have_no_fixed_chinese_copy(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    translator = Translator("en_US")
    plugin_manager = PluginManagerDialog(
        context.provider_service,
        translator=translator,
    )
    prompt = {"1": {"class_type": "SaveImage", "inputs": {}}}
    dialogs = (
        ProviderDialog(context.provider_service.plugin_records(), translator=translator),
        plugin_manager,
        ComfyUIInstanceDialog(translator=translator),
        TemplateImportDialog(prompt, "Poster", translator=translator),
        ProviderNodeDialog((), (), translator=translator),
        ComfyUINodeDialog((), (), translator=translator),
        ConnectionDialog((), translator=translator),
        WorkflowRunDialog(
            {"type": "object", "properties": {}},
            translator=translator,
        ),
    )
    shared = (
        ErrorState("Network unavailable", "trace-1", translator=translator),
        SkeletonBlock(translator=translator),
        Toast("Saved", duration_ms=0, translator=translator),
        Drawer("Task Overview", translator=translator),
        WorkflowCanvas(translator),
    )
    for widget in (*dialogs, *shared):
        qtbot.addWidget(widget)
    try:
        await plugin_manager.refresh()
        offenders = {
            type(widget).__name__: _han_copy(widget)
            for widget in (*dialogs, *shared)
            if _han_copy(widget)
        }
        assert offenders == {}
        with pytest.raises(SchemaFormError, match="properties must be an object"):
            SchemaForm(
                {"type": "object", "properties": []},
                translator=translator,
            )
    finally:
        pending = tuple(task for task in plugin_manager._tasks if not task.done())
        if pending:
            await asyncio.gather(*pending)
        await context.close()


def test_translator_keeps_locale_selection_explicit() -> None:
    assert Translator("en_US").text("模型", "Model") == "Model"
    assert Translator("unsupported").text("模型", "Model") == "模型"


def _window_page(window: MainWindow, page_id: str) -> QWidget:
    page = window._stack.widget(window._page_indexes[page_id])
    assert page is not None
    return page


def _han_copy(root: QWidget) -> tuple[str, ...]:
    """Return unexpected visible/accessibility Han copy from an English page."""

    allowed = {"中文 (简体)"}
    candidates: list[str] = []
    for widget in (root, *root.findChildren(QWidget)):
        accessible_name = widget.accessibleName().strip()
        if accessible_name:
            candidates.append(accessible_name)
        if isinstance(widget, QLabel):
            candidates.append(widget.text())
        if isinstance(widget, QAbstractButton):
            candidates.append(widget.text())
        if isinstance(widget, QLineEdit):
            candidates.append(widget.placeholderText())
        if isinstance(widget, QComboBox):
            candidates.extend(widget.itemText(index) for index in range(widget.count()))
        if isinstance(widget, QTabWidget):
            candidates.extend(widget.tabText(index) for index in range(widget.count()))
        if isinstance(widget, QTableView) and widget.model() is not None:
            candidates.extend(
                str(widget.model().headerData(column, Qt.Orientation.Horizontal) or "")
                for column in range(widget.model().columnCount())
            )
    return tuple(
        sorted(
            {
                text.strip()
                for text in candidates
                if text.strip() not in allowed and re.search(r"[\u3400-\u9fff]", text)
            }
        )
    )
