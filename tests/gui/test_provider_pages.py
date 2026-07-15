"""Provider and model page lifecycle tests against the real local services."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox
from pytestqt.qtbot import QtBot

from astraweft.application.providers import CreateProvider, ProviderService, UpdateProvider
from astraweft.bootstrap.container import build_app_context
from astraweft.domain.provider import Provider
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.secrets import SecretValue
from astraweft.presentation.pages.models import ModelsPage
from astraweft.presentation.pages.providers import (
    PluginManagerDialog,
    ProviderDialog,
    ProviderPage,
    _plugin_state,
)

_PLUGIN_ID = "dev.astraweft.mock-provider"


async def _settle(page: ProviderPage | ModelsPage | PluginManagerDialog) -> None:
    await asyncio.sleep(0)
    tasks = tuple(page._tasks)
    if tasks:
        await asyncio.gather(*tasks)
    await asyncio.sleep(0)


@pytest.mark.gui
@pytest.mark.asyncio
async def test_provider_and_model_pages_run_the_full_local_lifecycle(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await build_app_context(
        tmp_path,
        secret_store_override=SessionSecretStore(),
    )
    page = ProviderPage(context.provider_service)
    models_page = ModelsPage(context.provider_service)
    qtbot.addWidget(page)
    qtbot.addWidget(models_page)
    page.show()
    models_page.show()
    try:
        await page.refresh()
        assert "还没有 Provider" in [label.text() for label in page.findChildren(QLabel)]

        first_command = CreateProvider(
            plugin_id=_PLUGIN_ID,
            name="GUI Mock",
            settings={"mode": "healthy", "catalog_revision": 1},
            credentials={"api_key": SecretValue("mock-valid-key")},
        )
        await page._create(first_command)
        provider = (await context.provider_service.list_providers())[0]
        await page._test_connection(provider.id)
        await page._sync(provider.id)
        assert len(await context.provider_service.list_models(provider.id)) == 2

        await models_page._refresh()
        table_model = models_page._table.model()
        assert table_model is not None
        assert table_model.rowCount() == 2
        models_page._filter.setCurrentIndex(1)
        models_page._render()

        await context.provider_service.update(
            UpdateProvider(
                provider_id=provider.id,
                name=provider.name,
                settings={"mode": "protocol_error", "catalog_revision": 1},
                endpoint=None,
                enabled=True,
            )
        )
        await page._test_connection(provider.id)
        assert page._toast is not None
        assert "连接测试失败" in page._toast.accessibleName()

        await context.provider_service.update(
            UpdateProvider(
                provider_id=provider.id,
                name=provider.name,
                settings={"mode": "healthy", "catalog_revision": 2},
                endpoint="  ",
                enabled=True,
            )
        )
        await page._sync(provider.id)
        await models_page._refresh()
        table_model = models_page._table.model()
        assert table_model is not None
        assert table_model.rowCount() == 3
        assert "远端已下线" in models_page._summary.text()

        await page._toggle(provider.id, False)
        await page._toggle(provider.id, True)

        plugin_dialog = PluginManagerDialog(context.provider_service)
        qtbot.addWidget(plugin_dialog)
        await plugin_dialog.refresh()
        assert "3 个已发现" in plugin_dialog._summary.text()
        management = await context.provider_service.plugin_management()
        mock_entry = next(
            entry
            for entry in management
            if entry.record.manifest is not None and entry.record.manifest.plugin_id == _PLUGIN_ID
        )
        assert _plugin_state(mock_entry.record.state) == ("success", "可用")
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args: QMessageBox.StandardButton.No,
        )
        plugin_dialog._confirm_toggle(mock_entry, False)
        assert context.provider_service.plugin_records() == tuple(
            entry.record for entry in management
        )
        await plugin_dialog._toggle(_PLUGIN_ID, False)
        assert _plugin_state(
            next(
                record.state
                for record in context.provider_service.plugin_records()
                if record.manifest is not None and record.manifest.plugin_id == _PLUGIN_ID
            )
        ) == ("neutral", "已停用")
        await plugin_dialog._toggle(_PLUGIN_ID, True)
        await plugin_dialog._rescan()

        with monkeypatch.context() as patch:
            patch.setattr(PluginManagerDialog, "exec", lambda _self: QDialog.DialogCode.Rejected)
            page._open_plugins()
            await _settle(page)

        with monkeypatch.context() as patch:
            patch.setattr(
                ProviderDialog,
                "exec",
                lambda _self: QDialog.DialogCode.Accepted,
            )
            page._open_edit(provider.id)
            await _settle(page)

        second_command = CreateProvider(
            plugin_id=_PLUGIN_ID,
            name="Second Mock",
            settings={},
            credentials={"api_key": SecretValue("mock-valid-key")},
        )
        with monkeypatch.context() as patch:
            patch.setattr(
                ProviderDialog,
                "exec",
                lambda _self: QDialog.DialogCode.Accepted,
            )
            patch.setattr(ProviderDialog, "create_command", lambda _self: second_command)
            page._open_add()
            await _settle(page)
        providers = await context.provider_service.list_providers()
        assert {item.name for item in providers} == {"GUI Mock", "Second Mock"}

        second = next(item for item in providers if item.name == "Second Mock")
        with monkeypatch.context() as patch:
            patch.setattr(
                QMessageBox,
                "question",
                lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
            )
            page._confirm_delete(second.id)
            await _settle(page)

        await page._create(first_command)
        assert page._toast is not None
        assert "保存 Provider 失败" in page._toast.accessibleName()

        async def fail_list(_self: ProviderService) -> tuple[Provider, ...]:
            raise RuntimeError("catalog unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(ProviderService, "list_providers", fail_list)
            await page.refresh()
            await models_page._refresh()
            assert page._toast is not None
            assert page._toast.accessibleName() == "无法读取 Provider 列表"
            assert models_page._summary.text() == "模型目录读取失败"

        await page._delete(provider.id)
        assert await context.provider_service.list_providers() == ()
    finally:
        await _settle(page)
        await _settle(models_page)
        if "plugin_dialog" in locals():
            await _settle(plugin_dialog)
        await context.close()
