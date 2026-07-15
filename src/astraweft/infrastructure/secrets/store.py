"""SecretStore implementations with a safe in-memory fallback."""

from __future__ import annotations

import asyncio

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from astraweft.ports.secrets import SecretNotFoundError, SecretStore, SecretStoreError, SecretValue

_SERVICE_NAME = "AstraWeft"


def _account_name(credential_ref: str, field_name: str) -> str:
    if not credential_ref or not field_name:
        raise ValueError("credential_ref and field_name are required")
    return f"{credential_ref}:{field_name}"


class SessionSecretStore:
    """Process-lifetime fallback that never writes secrets to disk."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], SecretValue] = {}

    @property
    def persistent(self) -> bool:
        return False

    async def set(self, credential_ref: str, field_name: str, value: SecretValue) -> None:
        _account_name(credential_ref, field_name)
        self._values[(credential_ref, field_name)] = value

    async def get(self, credential_ref: str, field_name: str) -> SecretValue:
        try:
            return self._values[(credential_ref, field_name)]
        except KeyError as exc:
            raise SecretNotFoundError("credential field was not found") from exc

    async def delete(self, credential_ref: str, field_name: str) -> None:
        self._values.pop((credential_ref, field_name), None)


class KeyringSecretStore:
    """Cross-platform OS credential store adapter."""

    @property
    def persistent(self) -> bool:
        return True

    async def set(self, credential_ref: str, field_name: str, value: SecretValue) -> None:
        account = _account_name(credential_ref, field_name)
        try:
            await asyncio.to_thread(keyring.set_password, _SERVICE_NAME, account, value.reveal())
        except KeyringError as exc:
            raise SecretStoreError("unable to store credential in the operating system") from exc

    async def get(self, credential_ref: str, field_name: str) -> SecretValue:
        account = _account_name(credential_ref, field_name)
        try:
            value = await asyncio.to_thread(keyring.get_password, _SERVICE_NAME, account)
        except KeyringError as exc:
            raise SecretStoreError("unable to read credential from the operating system") from exc
        if value is None:
            raise SecretNotFoundError("credential field was not found")
        return SecretValue(value)

    async def delete(self, credential_ref: str, field_name: str) -> None:
        account = _account_name(credential_ref, field_name)
        try:
            await asyncio.to_thread(keyring.delete_password, _SERVICE_NAME, account)
        except PasswordDeleteError:
            return
        except KeyringError as exc:
            raise SecretStoreError("unable to delete credential from the operating system") from exc


def create_secret_store() -> SecretStore:
    """Use a persistent backend when available, otherwise session memory."""
    try:
        backend = keyring.get_keyring()
        priority = backend.priority
    except (KeyringError, RuntimeError):
        return SessionSecretStore()
    if priority <= 0:
        return SessionSecretStore()
    return KeyringSecretStore()
