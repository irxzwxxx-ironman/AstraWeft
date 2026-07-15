"""Persist user-controlled Provider plugin enablement without storing secrets."""

from __future__ import annotations

from astraweft.infrastructure.config.settings_store import SettingsStore


class SettingsPluginPreferenceStore:
    def __init__(self, settings: SettingsStore) -> None:
        self._settings = settings

    def load_disabled(self) -> frozenset[str]:
        loaded = self._settings.load(environ={})
        return frozenset(loaded.disabled_provider_plugins)

    def save_disabled(self, plugin_ids: frozenset[str]) -> None:
        loaded = self._settings.load(environ={})
        updated = loaded.model_copy(update={"disabled_provider_plugins": tuple(sorted(plugin_ids))})
        self._settings.save(updated)
