"""Provider entry-point discovery isolation and collision tests."""

from __future__ import annotations

from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from astraweft.infrastructure.providers import EntryPointProviderRegistry
from astraweft.ports.provider_plugins import (
    PluginLoadState,
    PluginNotAvailableError,
)


def _manifest(
    path: Path, *, entry_point: str, plugin_id: str, python: str = ">=3.12,<3.14"
) -> Path:
    path.write_text(
        f'''manifest_version = 1
plugin_id = "{plugin_id}"
name = "Test Provider"
version = "0.1.0"
plugin_api = ">=1.0,<2.0"
python = "{python}"
entry_point = "{entry_point}"
description = "test"
homepage = "https://example.invalid"
license = "Apache-2.0"

[publisher]
name = "Tests"

[permissions]
network = []
filesystem = "none"
subprocess = false

[capabilities]
operations = ["text.generate"]
async_tasks = false
cancel = false
model_discovery = false
usage = false
''',
        encoding="utf-8",
    )
    return path


def test_workspace_provider_plugins_are_discoverable_without_credentials() -> None:
    registry = EntryPointProviderRegistry()
    records = registry.discover()

    ready = [record for record in records if record.state is PluginLoadState.READY]
    by_id = {record.manifest.plugin_id: record for record in ready if record.manifest is not None}
    assert {"dev.astraweft.mock-provider", "com.openai.api-provider"} <= by_id.keys()
    assert all(by_id[plugin_id].package_hash is not None for plugin_id in by_id)


def test_broken_and_incompatible_plugins_do_not_stop_discovery(tmp_path: Path) -> None:
    broken = EntryPoint(
        name="broken", value="missing_provider_module:Plugin", group="astraweft.providers"
    )
    incompatible = EntryPoint(
        name="future", value="missing_future_module:Plugin", group="astraweft.providers"
    )
    manifests = {
        "broken": _manifest(
            tmp_path / "broken.toml",
            entry_point=broken.value,
            plugin_id="dev.astraweft.broken-provider",
        ),
        "future": _manifest(
            tmp_path / "future.toml",
            entry_point=incompatible.value,
            plugin_id="dev.astraweft.future-provider",
            python=">=99",
        ),
    }
    registry = EntryPointProviderRegistry(
        provider_entry_points=(broken, incompatible),
        manifest_locator=lambda point: manifests[point.name],
    )

    records = {record.entry_point_name: record for record in registry.discover()}
    assert records["broken"].state is PluginLoadState.LOAD_FAILED
    assert records["future"].state is PluginLoadState.INCOMPATIBLE
    assert "ModuleNotFoundError" in (records["broken"].diagnostic or "")


def test_duplicate_plugin_ids_disable_every_colliding_entry_point() -> None:
    point_one = EntryPoint(
        name="mock-one",
        value="astraweft_mock_provider.plugin:MockProviderPlugin",
        group="astraweft.providers",
    )
    point_two = EntryPoint(
        name="mock-two",
        value="astraweft_mock_provider.plugin:MockProviderPlugin",
        group="astraweft.providers",
    )
    manifest = Path(__file__).parents[2] / "plugins/mock/src/astraweft_mock_provider/plugin.toml"
    registry = EntryPointProviderRegistry(
        provider_entry_points=(point_one, point_two),
        manifest_locator=lambda _point: manifest,
    )

    records = registry.discover()
    assert {record.state for record in records} == {PluginLoadState.COLLISION}


def test_user_disabled_plugin_is_discovered_but_not_executable() -> None:
    plugin_id = "dev.astraweft.mock-provider"
    registry = EntryPointProviderRegistry(disabled_plugins=frozenset({plugin_id}))

    records = registry.discover()
    disabled = next(
        record
        for record in records
        if record.manifest is not None and record.manifest.plugin_id == plugin_id
    )

    assert disabled.state is PluginLoadState.DISABLED
    assert disabled.descriptor is not None
    assert disabled.package_hash is not None
    with pytest.raises(PluginNotAvailableError):
        registry.get(plugin_id)

    refreshed = registry.set_disabled(frozenset())
    enabled = next(
        record
        for record in refreshed
        if record.manifest is not None and record.manifest.plugin_id == plugin_id
    )
    assert enabled.state is PluginLoadState.READY
    assert registry.get(plugin_id).descriptor.plugin_id == plugin_id
