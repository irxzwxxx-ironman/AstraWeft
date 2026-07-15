"""Structural protocols implemented by Core and Provider plugins."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from astraweft_provider_sdk.types import (
    CancelResult,
    HealthCheckResult,
    ProviderContext,
    ProviderDescriptor,
    ProviderModel,
    ProviderRequest,
    RemoteTaskSnapshot,
    SecretValue,
    SubmissionResult,
)


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SecretResolver(Protocol):
    """Async secret lookup prevents Keyring calls from blocking the event loop."""

    async def get(self, credential_ref: str, field: str) -> SecretValue: ...


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
        timeout_seconds: float | None = None,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> HttpResponse: ...


class PluginLogger(Protocol):
    def debug(self, message: str, **context: object) -> None: ...

    def info(self, message: str, **context: object) -> None: ...

    def warning(self, message: str, **context: object) -> None: ...

    def error(self, message: str, **context: object) -> None: ...


class PluginDataDirectory(Protocol):
    @property
    def root(self) -> Path: ...

    def path_for(self, relative_path: str) -> Path: ...


@runtime_checkable
class ProviderClient(Protocol):
    async def health_check(self) -> HealthCheckResult: ...

    async def list_models(self) -> tuple[ProviderModel, ...]: ...

    async def submit(self, request: ProviderRequest) -> SubmissionResult: ...

    async def get_task(self, remote_task_id: str) -> RemoteTaskSnapshot: ...

    async def cancel_task(self, remote_task_id: str) -> CancelResult: ...

    async def close(self) -> None: ...


@runtime_checkable
class ProviderPlugin(Protocol):
    @property
    def descriptor(self) -> ProviderDescriptor: ...

    def create_client(
        self,
        context: ProviderContext,
        settings: Mapping[str, object],
        credential_ref: str | None,
    ) -> ProviderClient: ...

    def migrate_settings(
        self,
        from_version: str,
        settings: Mapping[str, object],
    ) -> Mapping[str, object]: ...
