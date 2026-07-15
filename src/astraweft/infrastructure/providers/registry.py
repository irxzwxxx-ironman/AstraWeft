"""Python entry-point Provider discovery with static manifest validation."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Sequence
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from urllib.parse import unquote, urlparse

from astraweft.ports.provider_plugins import (
    PluginLoadState,
    PluginNotAvailableError,
    PluginRecord,
)
from astraweft_provider_sdk import (
    PluginManifest,
    ProviderPlugin,
    assert_descriptor_matches_manifest,
    load_manifest,
)

ManifestLocator = Callable[[EntryPoint], Path]


class EntryPointProviderRegistry:
    """Discover trusted local plugins without letting one failure stop Core."""

    def __init__(
        self,
        *,
        provider_entry_points: Sequence[EntryPoint] | None = None,
        manifest_locator: ManifestLocator | None = None,
        disabled_plugins: frozenset[str] = frozenset(),
    ) -> None:
        self._provided_entry_points = provider_entry_points
        self._manifest_locator = manifest_locator or _locate_manifest
        self._disabled_plugins = disabled_plugins
        self._records: tuple[PluginRecord, ...] = ()
        self._plugins: dict[str, ProviderPlugin] = {}

    def discover(self) -> tuple[PluginRecord, ...]:
        points = (
            tuple(entry_points(group="astraweft.providers"))
            if self._provided_entry_points is None
            else tuple(self._provided_entry_points)
        )
        candidates = [
            self._load_entry_point(point)
            for point in sorted(points, key=lambda item: (item.name, item.value))
        ]

        ids = [
            record.manifest.plugin_id
            for record, _plugin in candidates
            if record.manifest is not None
        ]
        collisions = {plugin_id for plugin_id, count in Counter(ids).items() if count > 1}
        records: list[PluginRecord] = []
        plugins: dict[str, ProviderPlugin] = {}
        for record, plugin in candidates:
            plugin_id = record.manifest.plugin_id if record.manifest is not None else None
            if plugin_id in collisions:
                records.append(
                    PluginRecord(
                        entry_point_name=record.entry_point_name,
                        distribution_name=record.distribution_name,
                        state=PluginLoadState.COLLISION,
                        manifest=record.manifest,
                        descriptor=record.descriptor,
                        package_hash=record.package_hash,
                        diagnostic="duplicate plugin_id; all colliding plugins were disabled",
                    )
                )
            else:
                if plugin_id is not None and plugin_id in self._disabled_plugins:
                    record = PluginRecord(
                        entry_point_name=record.entry_point_name,
                        distribution_name=record.distribution_name,
                        state=PluginLoadState.DISABLED,
                        manifest=record.manifest,
                        descriptor=record.descriptor,
                        package_hash=record.package_hash,
                        diagnostic="disabled by local user preference",
                    )
                records.append(record)
                if (
                    plugin_id is not None
                    and plugin is not None
                    and record.state is PluginLoadState.READY
                ):
                    plugins[plugin_id] = plugin
        self._records = tuple(records)
        self._plugins = plugins
        return self._records

    def set_disabled(self, plugin_ids: frozenset[str]) -> tuple[PluginRecord, ...]:
        self._disabled_plugins = plugin_ids
        return self.discover()

    def records(self) -> tuple[PluginRecord, ...]:
        return self._records

    def get(self, plugin_id: str) -> ProviderPlugin:
        try:
            return self._plugins[plugin_id]
        except KeyError as exc:
            raise PluginNotAvailableError(f"Provider plugin is not available: {plugin_id}") from exc

    def _load_entry_point(self, point: EntryPoint) -> tuple[PluginRecord, ProviderPlugin | None]:
        distribution = point.dist.name if point.dist is not None else "unknown"
        manifest: PluginManifest | None = None
        package_hash: str | None = None
        try:
            manifest_path = self._manifest_locator(point)
            manifest = load_manifest(manifest_path)
            package_hash = _package_hash(point, manifest_path)
            if point.value != manifest.entry_point:
                raise ValueError("entry point metadata does not match plugin.toml")
            loaded = point.load()
            plugin = loaded() if isinstance(loaded, type) else loaded
            if not isinstance(plugin, ProviderPlugin):
                raise TypeError("entry point does not implement ProviderPlugin")
            assert_descriptor_matches_manifest(plugin.descriptor, manifest)
            return (
                PluginRecord(
                    entry_point_name=point.name,
                    distribution_name=distribution,
                    state=PluginLoadState.READY,
                    manifest=manifest,
                    descriptor=plugin.descriptor,
                    package_hash=package_hash,
                    diagnostic=None,
                ),
                plugin,
            )
        except Exception as exc:
            state = (
                PluginLoadState.INCOMPATIBLE
                if "does not include Core API" in str(exc) or "does not support Python" in str(exc)
                else PluginLoadState.LOAD_FAILED
            )
            return (
                PluginRecord(
                    entry_point_name=point.name,
                    distribution_name=distribution,
                    state=state,
                    manifest=manifest,
                    descriptor=None,
                    package_hash=package_hash,
                    diagnostic=f"{type(exc).__name__}: {exc}",
                ),
                None,
            )


def _locate_manifest(point: EntryPoint) -> Path:
    if point.dist is None:
        raise FileNotFoundError("entry point has no distribution metadata")
    package_name = point.value.partition(":")[0].split(".")[0]
    installed = Path(str(point.dist.locate_file(Path(package_name) / "plugin.toml")))
    if installed.is_file():
        return installed

    direct_url_text = point.dist.read_text("direct_url.json")
    if direct_url_text:
        direct_url = json.loads(direct_url_text)
        url = direct_url.get("url")
        if isinstance(url, str) and url.startswith("file:"):
            parsed = urlparse(url)
            root = Path(unquote(parsed.path))
            editable = root / "src" / package_name / "plugin.toml"
            if editable.is_file():
                return editable
    raise FileNotFoundError("plugin.toml is not present in the distribution")


def _package_hash(point: EntryPoint, manifest_path: Path) -> str:
    digest = hashlib.sha256()
    package_root = manifest_path.parent
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        digest.update(path.relative_to(package_root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    if point.dist is not None:
        metadata = point.dist.read_text("METADATA") or ""
        digest.update(metadata.encode())
    return digest.hexdigest()
