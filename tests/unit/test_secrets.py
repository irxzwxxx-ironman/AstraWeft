"""Secret boundary and keyring adapter tests."""

from __future__ import annotations

from typing import Any

import pytest
from keyring.errors import KeyringError, PasswordDeleteError

from astraweft.infrastructure.secrets.store import (
    KeyringSecretStore,
    SessionSecretStore,
    create_secret_store,
)
from astraweft.ports.secrets import SecretNotFoundError, SecretStoreError, SecretValue


def test_secret_value_requires_explicit_reveal() -> None:
    value = SecretValue("actual-value")

    assert value.reveal() == "actual-value"
    assert "actual-value" not in str(value)
    assert "actual-value" not in repr(value)


@pytest.mark.asyncio
async def test_session_store_round_trip_and_missing_value() -> None:
    store = SessionSecretStore()
    assert store.persistent is False

    await store.set("provider", "api_key", SecretValue("value"))
    assert (await store.get("provider", "api_key")).reveal() == "value"
    await store.delete("provider", "api_key")
    await store.delete("provider", "api_key")

    with pytest.raises(SecretNotFoundError):
        await store.get("provider", "api_key")
    with pytest.raises(ValueError):
        await store.set("", "api_key", SecretValue("value"))


@pytest.mark.asyncio
async def test_keyring_store_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    values: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(
        "keyring.set_password",
        lambda service, account, value: values.__setitem__((service, account), value),
    )
    monkeypatch.setattr(
        "keyring.get_password", lambda service, account: values.get((service, account))
    )
    monkeypatch.setattr(
        "keyring.delete_password", lambda service, account: values.pop((service, account), None)
    )
    store = KeyringSecretStore()
    assert store.persistent is True

    await store.set("openai", "api_key", SecretValue("key"))
    assert (await store.get("openai", "api_key")).reveal() == "key"
    await store.delete("openai", "api_key")
    with pytest.raises(SecretNotFoundError):
        await store.get("openai", "api_key")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["set", "get", "delete"])
async def test_keyring_errors_are_translated(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    def fail(*_args: Any) -> None:
        raise KeyringError("backend failed")

    monkeypatch.setattr(f"keyring.{operation}_password", fail)
    store = KeyringSecretStore()

    with pytest.raises(SecretStoreError):
        if operation == "set":
            await store.set("provider", "token", SecretValue("value"))
        elif operation == "get":
            await store.get("provider", "token")
        else:
            await store.delete("provider", "token")


@pytest.mark.asyncio
async def test_missing_keyring_delete_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_args: Any) -> None:
        raise PasswordDeleteError("missing")

    monkeypatch.setattr("keyring.delete_password", missing)

    await KeyringSecretStore().delete("provider", "token")


@pytest.mark.parametrize("priority", [0, -1])
def test_factory_falls_back_for_unusable_backend(
    monkeypatch: pytest.MonkeyPatch, priority: int
) -> None:
    backend = type("Backend", (), {"priority": priority})()
    monkeypatch.setattr("keyring.get_keyring", lambda: backend)

    assert isinstance(create_secret_store(), SessionSecretStore)


def test_factory_uses_persistent_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = type("Backend", (), {"priority": 5})()
    monkeypatch.setattr("keyring.get_keyring", lambda: backend)

    assert isinstance(create_secret_store(), KeyringSecretStore)


def test_factory_handles_backend_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> None:
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr("keyring.get_keyring", fail)

    assert isinstance(create_secret_store(), SessionSecretStore)
