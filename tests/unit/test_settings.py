"""Configuration and platform path tests."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from astraweft.application.settings import AppSettings, SettingsService
from astraweft.infrastructure.config.paths import resolve_app_paths
from astraweft.infrastructure.config.settings_store import SettingsLoadError, SettingsStore


def test_settings_defaults_are_local_safe_and_immutable() -> None:
    settings = AppSettings()

    assert settings.theme == "dark"
    assert settings.language == "zh_CN"
    assert settings.max_concurrency == 4
    assert settings.request_log_retention_days == 90
    assert settings.backup_retention_count == 7
    assert settings.artifact_trash_retention_days == 30
    assert settings.system_notifications is True
    with pytest.raises(ValidationError):
        settings.max_concurrency = 8


@pytest.mark.parametrize(
    ("field", "value"),
    [("max_concurrency", 0), ("provider_concurrency", 17), ("request_timeout_seconds", 0)],
)
def test_settings_reject_out_of_range_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AppSettings.model_validate({field: value})


def test_isolated_paths_are_predictable_and_created(tmp_path: Path) -> None:
    paths = resolve_app_paths(tmp_path / "workspace")
    paths.ensure()

    assert paths.settings_path == (tmp_path / "workspace" / "config" / "settings.json").resolve()
    assert paths.database_path.parent.is_dir()
    assert paths.artifact_dir.is_dir()
    assert paths.backup_dir.is_dir()
    assert paths.trash_dir.is_dir()
    assert paths.diagnostic_dir.is_dir()
    assert paths.pending_restore_path.parent == paths.data_dir
    assert paths.log_dir.is_dir()


def test_native_paths_have_all_required_locations() -> None:
    paths = resolve_app_paths()

    assert paths.config_dir
    assert paths.data_dir
    assert paths.cache_dir
    assert paths.log_dir
    assert paths.artifact_dir.parent == paths.data_dir
    assert paths.restore_marker_path.parent == paths.data_dir


def test_store_round_trip_is_private_and_newline_terminated(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "settings.json"
    store = SettingsStore(path)
    settings = AppSettings(language="en_US", max_concurrency=7, reduce_motion=True)

    store.save(settings)

    assert store.path == path
    assert store.load(environ={}) == settings
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_settings_service_updates_only_user_preferences(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.save(AppSettings(max_concurrency=9, disabled_provider_plugins=("plugin.one",)))
    service = SettingsService(store)

    updated = await service.update_user_preferences(
        language="en_US",
        system_notifications=False,
        reduce_motion=True,
    )

    assert updated.language == "en_US"
    assert updated.system_notifications is False
    assert updated.reduce_motion is True
    assert updated.max_concurrency == 9
    assert updated.disabled_provider_plugins == ("plugin.one",)
    assert store.load_persisted() == updated


def test_settings_precedence_is_defaults_file_environment_cli(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"theme": "system", "language": "en_US", "max_concurrency": 3}),
        encoding="utf-8",
    )

    settings = SettingsStore(path).load(
        environ={
            "ASTRAWEFT_LANGUAGE": "zh_CN",
            "ASTRAWEFT_MAX_CONCURRENCY": "6",
            "ASTRAWEFT_REDUCE_MOTION": "true",
            "ASTRAWEFT_REQUEST_LOG_RETENTION_DAYS": "30",
            "ASTRAWEFT_BACKUP_RETENTION_COUNT": "12",
            "ASTRAWEFT_ARTIFACT_TRASH_RETENTION_DAYS": "45",
            "ASTRAWEFT_SYSTEM_NOTIFICATIONS": "false",
        },
        cli_overrides={"max_concurrency": 9},
    )

    assert settings.theme == "system"
    assert settings.language == "zh_CN"
    assert settings.max_concurrency == 9
    assert settings.reduce_motion is True
    assert settings.request_log_retention_days == 30
    assert settings.backup_retention_count == 12
    assert settings.artifact_trash_retention_days == 45
    assert settings.system_notifications is False


@pytest.mark.parametrize("content", ["not-json", "[]", '{"unknown": true}'])
def test_store_reports_invalid_persisted_settings(tmp_path: Path, content: str) -> None:
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(SettingsLoadError):
        SettingsStore(path).load(environ={})


def test_store_reports_invalid_environment_override(tmp_path: Path) -> None:
    with pytest.raises(SettingsLoadError):
        SettingsStore(tmp_path / "missing.json").load(
            environ={"ASTRAWEFT_MAX_CONCURRENCY": "hundreds"}
        )
