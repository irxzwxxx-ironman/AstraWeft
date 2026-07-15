"""Whole-shell keyboard and accessible-name audit."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from astraweft.bootstrap.container import build_app_context
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.presentation.accessibility import audit_accessibility
from astraweft.presentation.main_window import MainWindow


@pytest.mark.gui
@pytest.mark.asyncio
async def test_full_product_shell_has_named_keyboard_controls(
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
        app_settings=context.settings,
    )
    qtbot.addWidget(window)
    try:
        issues = audit_accessibility(window)
        assert issues == (), "\n".join(str(issue) for issue in issues)
    finally:
        window.close()
        await context.close()
