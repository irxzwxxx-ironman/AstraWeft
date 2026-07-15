"""Provider persistence and transaction interfaces."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from astraweft.domain.provider import CredentialMetadata, Model, Provider


class ProviderRepository(Protocol):
    async def add(self, provider: Provider) -> None: ...

    async def get(self, provider_id: str, *, include_deleted: bool = False) -> Provider | None: ...

    async def list(self, *, include_deleted: bool = False) -> tuple[Provider, ...]: ...

    async def update(self, provider: Provider, *, expected_version: int) -> None: ...


class CredentialRepository(Protocol):
    async def add(self, credential: CredentialMetadata) -> None: ...

    async def get(self, credential_id: str) -> CredentialMetadata | None: ...

    async def delete(self, credential_id: str) -> None: ...


class ModelRepository(Protocol):
    async def get_for_provider(self, provider_id: str) -> tuple[Model, ...]: ...

    async def upsert(self, model: Model) -> None: ...

    async def mark_unavailable_except(
        self,
        provider_id: str,
        remote_model_ids: frozenset[str],
        *,
        synced_at: datetime,
    ) -> None: ...

    async def update_user_preferences(
        self,
        model_id: str,
        *,
        display_name: str,
        default_params: dict[str, object],
        enabled: bool,
        updated_at: datetime,
    ) -> None: ...


class ProviderUnitOfWork(Protocol):
    @property
    def providers(self) -> ProviderRepository: ...

    @property
    def credentials(self) -> CredentialRepository: ...

    @property
    def models(self) -> ModelRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    def publish_after_commit(self, event: object) -> None: ...


class ProviderUnitOfWorkFactory(Protocol):
    def __call__(self) -> ProviderUnitOfWork: ...
