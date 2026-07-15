"""Provider plugin enablement is persistent and exposes compatibility impact."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from astraweft.application.providers import CreateProvider
from astraweft.bootstrap.container import build_app_context
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.provider_plugins import PluginLoadState, PluginNotAvailableError
from astraweft.ports.secrets import SecretValue


@pytest.mark.integration
@pytest.mark.asyncio
async def test_plugin_disable_preview_persistence_and_reenable(tmp_path: Path) -> None:
    plugin_id = "dev.astraweft.mock-provider"
    first = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await first.provider_service.create(
            CreateProvider(
                plugin_id=plugin_id,
                name="Managed Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        models = await first.provider_service.sync_models(provider.id)
        entry = next(
            item
            for item in await first.provider_service.plugin_management()
            if item.record.manifest is not None and item.record.manifest.plugin_id == plugin_id
        )
        assert entry.provider_count == 1
        assert entry.enabled_provider_count == 1
        assert entry.model_count == len(models)

        disabled = await first.provider_service.set_plugin_enabled(plugin_id, enabled=False)
        assert disabled.state is PluginLoadState.DISABLED
        with pytest.raises(PluginNotAvailableError):
            await first.provider_service.concurrency_limit(provider.id)
        settings = json.loads(first.paths.settings_path.read_text(encoding="utf-8"))
        assert settings["disabled_provider_plugins"] == [plugin_id]
    finally:
        await first.close()

    second = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        disabled = next(
            record
            for record in second.provider_service.plugin_records()
            if record.manifest is not None and record.manifest.plugin_id == plugin_id
        )
        assert disabled.state is PluginLoadState.DISABLED

        enabled = await second.provider_service.set_plugin_enabled(plugin_id, enabled=True)
        assert enabled.state is PluginLoadState.READY
        refreshed = await second.provider_service.refresh_plugin_catalog()
        assert (
            next(
                record.state
                for record in refreshed
                if record.manifest is not None and record.manifest.plugin_id == plugin_id
            )
            is PluginLoadState.READY
        )
        assert await second.provider_service.concurrency_limit(provider.id) >= 1
    finally:
        await second.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_plugin_management_rejects_unknown_identity(tmp_path: Path) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        with pytest.raises(LookupError, match="不存在"):
            await context.provider_service.set_plugin_enabled("missing.plugin", enabled=False)
    finally:
        await context.close()
