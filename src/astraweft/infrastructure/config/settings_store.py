"""Atomic settings persistence with explicit precedence."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from astraweft.application.settings import AppSettings


class SettingsLoadError(RuntimeError):
    """Raised when persisted or overridden settings are invalid."""


_ENV_FIELDS = {
    "ASTRAWEFT_THEME": "theme",
    "ASTRAWEFT_LANGUAGE": "language",
    "ASTRAWEFT_LOG_LEVEL": "log_level",
    "ASTRAWEFT_MAX_CONCURRENCY": "max_concurrency",
    "ASTRAWEFT_PROVIDER_CONCURRENCY": "provider_concurrency",
    "ASTRAWEFT_REQUEST_TIMEOUT_SECONDS": "request_timeout_seconds",
    "ASTRAWEFT_REDUCE_MOTION": "reduce_motion",
    "ASTRAWEFT_SYSTEM_NOTIFICATIONS": "system_notifications",
    "ASTRAWEFT_REQUEST_LOG_RETENTION_DAYS": "request_log_retention_days",
    "ASTRAWEFT_BACKUP_RETENTION_COUNT": "backup_retention_count",
    "ASTRAWEFT_ARTIFACT_TRASH_RETENTION_DAYS": "artifact_trash_retention_days",
}


class SettingsStore:
    """Load defaults < file < environment < CLI overrides."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(
        self,
        *,
        cli_overrides: Mapping[str, object] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> AppSettings:
        data: dict[str, object] = {}
        if self._path.exists():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SettingsLoadError(f"无法读取设置文件：{self._path}") from exc
            if not isinstance(loaded, dict):
                raise SettingsLoadError("设置文件顶层必须是 JSON object")
            data.update(loaded)

        environment = os.environ if environ is None else environ
        for env_name, field_name in _ENV_FIELDS.items():
            if env_name in environment:
                data[field_name] = environment[env_name]

        if cli_overrides:
            data.update(cli_overrides)

        try:
            return AppSettings.model_validate(data)
        except ValidationError as exc:
            raise SettingsLoadError("设置值未通过校验") from exc

    def save(self, settings: AppSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(settings.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary_path = Path(handle.name)
            temporary_path.chmod(0o600)
            temporary_path.replace(self._path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def load_persisted(self) -> AppSettings:
        """Load the file without environment or CLI overlays for safe user edits."""
        return self.load(environ={})
