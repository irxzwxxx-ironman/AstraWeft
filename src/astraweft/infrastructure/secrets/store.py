"""SecretStore implementations with a safe in-memory fallback."""

from __future__ import annotations

import asyncio
import logging

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


class ResilientSecretStore:
    """Use an OS keyring until it fails, then stay in process memory.

    A keyring backend can advertise a positive priority while still rejecting
    access for the current executable.  This is common for unsigned macOS
    development bundles.  Once an operational failure is observed, all
    subsequent access is routed to the session store for the lifetime of this
    process so credentials are never written to a plaintext fallback.
    """

    def __init__(
        self,
        primary: SecretStore,
        fallback: SecretStore | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback or SessionSecretStore()
        if self._fallback.persistent:
            raise ValueError("secret fallback must be non-persistent")
        self._degraded = False
        self._lock = asyncio.Lock()
        self._logger = logging.getLogger("astraweft.secrets")

    @property
    def persistent(self) -> bool:
        return self._primary.persistent and not self._degraded

    async def set(self, credential_ref: str, field_name: str, value: SecretValue) -> None:
        async with self._lock:
            if self._degraded:
                await self._fallback.set(credential_ref, field_name, value)
                return
            try:
                await self._primary.set(credential_ref, field_name, value)
            except SecretStoreError as exc:
                self._degrade("set", exc)
                await self._fallback.set(credential_ref, field_name, value)

    async def get(self, credential_ref: str, field_name: str) -> SecretValue:
        async with self._lock:
            if self._degraded:
                return await self._fallback.get(credential_ref, field_name)
            try:
                return await self._primary.get(credential_ref, field_name)
            except SecretNotFoundError:
                raise
            except SecretStoreError as exc:
                self._degrade("get", exc)
                return await self._fallback.get(credential_ref, field_name)

    async def delete(self, credential_ref: str, field_name: str) -> None:
        async with self._lock:
            if self._degraded:
                await self._fallback.delete(credential_ref, field_name)
                raise SecretStoreError(
                    "unable to confirm credential deletion while the operating system keyring "
                    "is unavailable"
                )
            try:
                await self._primary.delete(credential_ref, field_name)
            except SecretStoreError as exc:
                self._degrade("delete", exc)
                await self._fallback.delete(credential_ref, field_name)
                raise

    def _degrade(self, operation: str, error: SecretStoreError) -> None:
        self._degraded = True
        self._logger.warning(
            "keyring_unavailable_session_fallback",
            extra={
                "operation": operation,
                "error_type": type(error.__cause__ or error).__name__,
            },
        )


def create_secret_store() -> SecretStore:
    """Use a persistent backend when available, otherwise session memory."""
    try:
        backend = keyring.get_keyring()
        priority = backend.priority
    except (KeyringError, RuntimeError):
        return SessionSecretStore()
    if priority <= 0:
        return SessionSecretStore()
    return ResilientSecretStore(KeyringSecretStore())
