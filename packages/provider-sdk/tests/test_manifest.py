"""Static plugin manifest parser tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from astraweft_provider_sdk.manifest import ManifestError, load_manifest

_VALID = """
manifest_version = 1
plugin_id = "com.example.provider"
name = "Example Provider"
version = "1.2.0"
plugin_api = ">=1.0,<2.0"
python = ">=3.12,<3.14"
entry_point = "example_provider.plugin:ExamplePlugin"
description = "Example"
homepage = "https://example.com"
license = "Apache-2.0"

[publisher]
name = "Example"
url = "https://example.com"

[permissions]
network = ["api.example.com"]
filesystem = "plugin_data"
subprocess = false

[capabilities]
operations = ["text.generate"]
async_tasks = false
cancel = false
model_discovery = true
usage = true
"""


def test_load_manifest_parses_and_checks_compatibility(tmp_path: Path) -> None:
    path = tmp_path / "plugin.toml"
    path.write_text(_VALID, encoding="utf-8")

    manifest = load_manifest(path)

    assert manifest.plugin_id == "com.example.provider"
    assert manifest.permissions.network == ("api.example.com",)
    assert manifest.capabilities.operations == frozenset({"text.generate"})
    manifest.assert_compatible("1.9")
    with pytest.raises(ManifestError):
        manifest.assert_compatible("2.0")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('plugin_id = "com.example.provider"', 'plugin_id = "invalid"'),
        ("manifest_version = 1", "manifest_version = 2"),
        ('version = "1.2.0"', 'version = "not version"'),
        ('entry_point = "example_provider.plugin:ExamplePlugin"', 'entry_point = "bad value"'),
        ('operations = ["text.generate"]', "operations = []"),
        ('plugin_api = ">=1.0,<2.0"', 'plugin_api = ">=2.0"'),
        ('filesystem = "plugin_data"', 'filesystem = "all"'),
    ],
)
def test_invalid_manifests_are_rejected(tmp_path: Path, old: str, new: str) -> None:
    path = tmp_path / "plugin.toml"
    path.write_text(_VALID.replace(old, new), encoding="utf-8")

    with pytest.raises(ManifestError):
        load_manifest(path)


def test_unreadable_or_malformed_manifest_is_safe(tmp_path: Path) -> None:
    with pytest.raises(ManifestError):
        load_manifest(tmp_path / "missing.toml")
    malformed = tmp_path / "bad.toml"
    malformed.write_text("[invalid", encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(malformed)
