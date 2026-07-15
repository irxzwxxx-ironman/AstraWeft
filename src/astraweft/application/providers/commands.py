"""Secret-safe Provider command values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from astraweft.ports.secrets import SecretValue


@dataclass(frozen=True, slots=True)
class CreateProvider:
    plugin_id: str
    name: str
    settings: Mapping[str, object]
    credentials: Mapping[str, SecretValue] = field(repr=False)
    endpoint: str | None = None
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class UpdateProvider:
    provider_id: str
    name: str
    settings: Mapping[str, object]
    endpoint: str | None
    enabled: bool
    credentials: Mapping[str, SecretValue] | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class UpdateModelPreferences:
    provider_id: str
    model_id: str
    display_name: str
    default_params: Mapping[str, object]
    enabled: bool
