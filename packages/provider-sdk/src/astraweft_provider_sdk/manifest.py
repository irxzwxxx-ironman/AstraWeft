"""Static Provider manifest parsing and compatibility checks."""

from __future__ import annotations

import re
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from astraweft_provider_sdk.api import PLUGIN_API_VERSION

_PLUGIN_ID = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9][a-z0-9-]*){2,}$")
_ENTRY_POINT = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$")


class ManifestError(ValueError):
    """Static manifest is invalid or incompatible."""


@dataclass(frozen=True, slots=True)
class PluginPublisher:
    name: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class PluginPermissions:
    network: tuple[str, ...] = ()
    filesystem: Literal["none", "plugin_data"] = "none"
    subprocess: bool = False
    user_configured_endpoint: bool = False
    additional_network_hosts_setting: str | None = None


@dataclass(frozen=True, slots=True)
class ManifestCapabilities:
    operations: frozenset[str]
    async_tasks: bool = False
    cancel: bool = False
    model_discovery: bool = False
    usage: bool = False


@dataclass(frozen=True, slots=True)
class PluginManifest:
    manifest_version: int
    plugin_id: str
    name: str
    version: str
    plugin_api: str
    python: str
    entry_point: str
    description: str
    homepage: str | None
    license: str
    publisher: PluginPublisher
    permissions: PluginPermissions
    capabilities: ManifestCapabilities

    def assert_compatible(self, plugin_api_version: str = PLUGIN_API_VERSION) -> None:
        try:
            api_range = SpecifierSet(self.plugin_api)
            python_range = SpecifierSet(self.python)
        except InvalidSpecifier as exc:
            raise ManifestError("manifest contains an invalid compatibility range") from exc
        if Version(plugin_api_version) not in api_range:
            raise ManifestError(
                f"plugin API {self.plugin_api} does not include Core API {plugin_api_version}"
            )
        running_python = Version(
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        )
        if running_python not in python_range:
            raise ManifestError(f"plugin does not support Python {running_python}")


def load_manifest(path: Path) -> PluginManifest:
    """Load and validate static TOML without importing plugin Python code."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(f"unable to read plugin manifest: {path}") from exc
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be a table")

    publisher = _table(data, "publisher")
    permissions = _table(data, "permissions")
    capabilities = _table(data, "capabilities")
    try:
        manifest = PluginManifest(
            manifest_version=_integer(data, "manifest_version"),
            plugin_id=_string(data, "plugin_id"),
            name=_string(data, "name"),
            version=_string(data, "version"),
            plugin_api=_string(data, "plugin_api"),
            python=_string(data, "python"),
            entry_point=_string(data, "entry_point"),
            description=_string(data, "description"),
            homepage=_optional_string(data, "homepage"),
            license=_string(data, "license"),
            publisher=PluginPublisher(
                name=_string(publisher, "name"),
                url=_optional_string(publisher, "url"),
            ),
            permissions=PluginPermissions(
                network=tuple(_string_list(permissions, "network")),
                filesystem=_filesystem_permission(permissions.get("filesystem", "none")),
                subprocess=_boolean(permissions, "subprocess"),
                user_configured_endpoint=_optional_boolean(
                    permissions, "user_configured_endpoint", False
                ),
                additional_network_hosts_setting=_optional_string(
                    permissions, "additional_network_hosts_setting"
                ),
            ),
            capabilities=ManifestCapabilities(
                operations=frozenset(_string_list(capabilities, "operations")),
                async_tasks=_boolean(capabilities, "async_tasks"),
                cancel=_boolean(capabilities, "cancel"),
                model_discovery=_boolean(capabilities, "model_discovery"),
                usage=_boolean(capabilities, "usage"),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestError(f"invalid plugin manifest field: {exc}") from exc

    _validate_manifest(manifest)
    return manifest


def _validate_manifest(manifest: PluginManifest) -> None:
    if manifest.manifest_version != 1:
        raise ManifestError("unsupported manifest_version")
    if not _PLUGIN_ID.fullmatch(manifest.plugin_id):
        raise ManifestError("plugin_id must use reverse-domain format")
    if not _ENTRY_POINT.fullmatch(manifest.entry_point):
        raise ManifestError("entry_point must be module:attribute")
    if not manifest.capabilities.operations:
        raise ManifestError("plugin must declare at least one operation")
    setting = manifest.permissions.additional_network_hosts_setting
    if setting is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", setting):
        raise ManifestError("additional network hosts setting must be a safe field name")
    try:
        Version(manifest.version)
    except InvalidVersion as exc:
        raise ManifestError("plugin version must be valid SemVer-compatible syntax") from exc
    manifest.assert_compatible()


def _table(data: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = data[key]
    if not isinstance(value, Mapping):
        raise TypeError(f"{key} must be a table")
    return value


def _string(data: Mapping[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{key} must be a non-empty string")
    return value


def _optional_string(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _integer(data: Mapping[str, object], key: str) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _boolean(data: Mapping[str, object], key: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _optional_boolean(data: Mapping[str, object], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _string_list(data: Mapping[str, object], key: str) -> list[str]:
    value = data[key]
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise TypeError(f"{key} must be a list of strings")
    return value


def _filesystem_permission(value: object) -> Literal["none", "plugin_data"]:
    if value not in ("none", "plugin_data"):
        raise ValueError("filesystem permission must be none or plugin_data")
    return value
