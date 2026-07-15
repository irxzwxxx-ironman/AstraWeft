"""Validated settings and application-level preference updates."""

from __future__ import annotations

import asyncio
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AppSettings(BaseModel):
    """Non-secret user and runtime settings.

    Secrets are deliberately excluded and belong in the SecretStore port.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    theme: Literal["dark", "system"] = "dark"
    language: Literal["zh_CN", "en_US"] = "zh_CN"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_concurrency: int = Field(default=4, ge=1, le=32)
    provider_concurrency: int = Field(default=2, ge=1, le=16)
    request_timeout_seconds: float = Field(default=60.0, ge=1.0, le=3600.0)
    reduce_motion: bool = False
    system_notifications: bool = True
    request_log_retention_days: Literal[7, 30, 90, 0] = 90
    backup_retention_count: int = Field(default=7, ge=1, le=30)
    artifact_trash_retention_days: int = Field(default=30, ge=1, le=365)
    disabled_provider_plugins: tuple[str, ...] = ()

    @field_validator("request_log_retention_days", mode="before")
    @classmethod
    def _parse_request_log_retention(cls, value: object) -> object:
        if isinstance(value, str) and value.isdecimal():
            return int(value)
        return value

    @field_validator("disabled_provider_plugins")
    @classmethod
    def _normalize_disabled_plugins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not plugin_id.strip() for plugin_id in value):
            raise ValueError("disabled plugin IDs must not be empty")
        return tuple(sorted(set(value)))


class SettingsPersistence(Protocol):
    def load_persisted(self) -> AppSettings: ...

    def save(self, settings: AppSettings) -> None: ...


class SettingsService:
    """Persist non-secret user preferences without exposing infrastructure to GUI code."""

    def __init__(self, persistence: SettingsPersistence) -> None:
        self._persistence = persistence

    async def update_user_preferences(
        self,
        *,
        language: Literal["zh_CN", "en_US"],
        system_notifications: bool,
        reduce_motion: bool,
    ) -> AppSettings:
        current = await asyncio.to_thread(self._persistence.load_persisted)
        updated = current.model_copy(
            update={
                "language": language,
                "system_notifications": system_notifications,
                "reduce_motion": reduce_motion,
            }
        )
        await asyncio.to_thread(self._persistence.save, updated)
        return updated
