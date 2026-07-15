"""Persistence-agnostic Provider, credential metadata, and model entities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from astraweft.domain.common import freeze_mapping


class ProviderHealth(StrEnum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class CredentialStoreType(StrEnum):
    KEYRING = "KEYRING"
    SESSION = "SESSION"


class CredentialType(StrEnum):
    API_KEY = "API_KEY"
    OAUTH2 = "OAUTH2"
    SERVICE_ACCOUNT = "SERVICE_ACCOUNT"


@dataclass(frozen=True, slots=True)
class Provider:
    id: str
    plugin_id: str
    plugin_version: str
    name: str
    endpoint: str | None
    enabled: bool
    config: Mapping[str, object]
    credential_id: str | None
    health_status: ProviderHealth
    last_checked_at: datetime | None
    row_version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        if not all((self.id, self.plugin_id, self.plugin_version, self.name.strip())):
            raise ValueError("Provider identity fields must not be empty")
        if self.row_version < 1:
            raise ValueError("row_version must be positive")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "config", freeze_mapping(self.config))
        _require_aware(self.created_at)
        _require_aware(self.updated_at)
        if self.last_checked_at is not None:
            _require_aware(self.last_checked_at)
        if self.deleted_at is not None:
            _require_aware(self.deleted_at)

    def with_health(self, status: ProviderHealth, checked_at: datetime) -> Provider:
        _require_aware(checked_at)
        return replace(
            self,
            health_status=status,
            last_checked_at=checked_at,
            updated_at=checked_at,
            row_version=self.row_version + 1,
        )


@dataclass(frozen=True, slots=True)
class CredentialMetadata:
    id: str
    store_type: CredentialStoreType
    credential_ref: str
    credential_type: CredentialType
    hint: str | None
    metadata: Mapping[str, object]
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.id or not self.credential_ref:
            raise ValueError("credential identity fields must not be empty")
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))
        _require_aware(self.created_at)
        _require_aware(self.updated_at)


@dataclass(frozen=True, slots=True)
class Model:
    id: str
    provider_id: str
    remote_model_id: str
    display_name: str
    modality: str
    operations: frozenset[str]
    parameter_schema: Mapping[str, object]
    parameter_ui_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    capabilities: Mapping[str, object]
    pricing: tuple[Mapping[str, object], ...]
    default_params: Mapping[str, object]
    source_hash: str | None
    enabled: bool
    available: bool
    deprecated: bool
    synced_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not all((self.id, self.provider_id, self.remote_model_id, self.display_name.strip())):
            raise ValueError("model identity fields must not be empty")
        if not self.operations:
            raise ValueError("model operations must not be empty")
        object.__setattr__(self, "display_name", self.display_name.strip())
        object.__setattr__(self, "operations", frozenset(self.operations))
        object.__setattr__(self, "parameter_schema", freeze_mapping(self.parameter_schema))
        object.__setattr__(self, "parameter_ui_schema", freeze_mapping(self.parameter_ui_schema))
        object.__setattr__(self, "output_schema", freeze_mapping(self.output_schema))
        object.__setattr__(self, "capabilities", freeze_mapping(self.capabilities))
        object.__setattr__(self, "pricing", tuple(freeze_mapping(rule) for rule in self.pricing))
        object.__setattr__(self, "default_params", freeze_mapping(self.default_params))
        _require_aware(self.created_at)
        _require_aware(self.updated_at)
        if self.synced_at is not None:
            _require_aware(self.synced_at)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
