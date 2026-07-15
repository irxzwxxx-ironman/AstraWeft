"""Secret storage abstractions that never expose values through repr or str."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class SecretStoreError(RuntimeError):
    """Base error for credential storage failures."""


class SecretNotFoundError(SecretStoreError):
    """Raised when a credential reference or field does not exist."""


@dataclass(frozen=True, slots=True)
class SecretValue:
    """A value that requires an explicit reveal operation."""

    _value: str = field(repr=False)

    def reveal(self) -> str:
        """Return the secret for immediate request construction."""
        return self._value

    def __str__(self) -> str:
        return "••••••••"

    def __repr__(self) -> str:
        return "SecretValue(••••••••)"


class SecretStore(Protocol):
    """Asynchronous boundary for operating-system or session credentials."""

    @property
    def persistent(self) -> bool: ...

    async def set(self, credential_ref: str, field_name: str, value: SecretValue) -> None: ...

    async def get(self, credential_ref: str, field_name: str) -> SecretValue: ...

    async def delete(self, credential_ref: str, field_name: str) -> None: ...
