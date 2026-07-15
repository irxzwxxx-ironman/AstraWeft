"""Mock Provider contract fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from astraweft_provider_sdk import (
    HttpResponse,
    ProviderContext,
    SecretValue,
    UnsupportedOperationError,
)


class FakeSecrets:
    def __init__(self, value: str = "mock-valid-key") -> None:
        self.value = value

    async def get(self, credential_ref: str, field: str) -> SecretValue:
        assert credential_ref
        assert field == "api_key"
        return SecretValue(self.value)


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 15, tzinfo=UTC)

    def monotonic(self) -> float:
        return 1.0


class NoHttp:
    async def request(self, *_args: object, **_kwargs: object) -> HttpResponse:
        raise UnsupportedOperationError("Mock Provider has no network permission")


class SilentLogger:
    def debug(self, message: str, **context: object) -> None:
        del message, context

    def info(self, message: str, **context: object) -> None:
        del message, context

    def warning(self, message: str, **context: object) -> None:
        del message, context

    def error(self, message: str, **context: object) -> None:
        del message, context


class TempData:
    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, relative_path: str) -> Path:
        return self._root / relative_path


@pytest.fixture
def provider_context(tmp_path: Path) -> ProviderContext:
    return ProviderContext(
        http=NoHttp(),
        secrets=FakeSecrets(),
        logger=SilentLogger(),
        clock=FakeClock(),
        plugin_data=TempData(tmp_path),
        core_version="0.1-test",
        plugin_api_version="1.0",
    )
