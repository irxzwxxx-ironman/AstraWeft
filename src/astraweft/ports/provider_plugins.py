"""Core-facing Provider plugin discovery boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from astraweft_provider_sdk import (
    PluginManifest,
    ProviderContext,
    ProviderDescriptor,
    ProviderPlugin,
)


class PluginLoadState(StrEnum):
    READY = "READY"
    DISABLED = "DISABLED"
    INCOMPATIBLE = "INCOMPATIBLE"
    LOAD_FAILED = "LOAD_FAILED"
    COLLISION = "COLLISION"


@dataclass(frozen=True, slots=True)
class PluginRecord:
    entry_point_name: str
    distribution_name: str
    state: PluginLoadState
    manifest: PluginManifest | None
    descriptor: ProviderDescriptor | None
    package_hash: str | None
    diagnostic: str | None


class PluginNotAvailableError(LookupError):
    """Requested plugin is missing, disabled, incompatible, or failed to load."""


class ProviderPluginCatalog(Protocol):
    def discover(self) -> tuple[PluginRecord, ...]: ...

    def records(self) -> tuple[PluginRecord, ...]: ...

    def get(self, plugin_id: str) -> ProviderPlugin: ...

    def set_disabled(self, plugin_ids: frozenset[str]) -> tuple[PluginRecord, ...]: ...


class PluginPreferenceStore(Protocol):
    def load_disabled(self) -> frozenset[str]: ...

    def save_disabled(self, plugin_ids: frozenset[str]) -> None: ...


class ProviderContextFactory(Protocol):
    def __call__(
        self,
        plugin_id: str,
        allowed_network: tuple[str, ...],
        endpoint: str | None,
    ) -> ProviderContext: ...
