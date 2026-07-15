"""Offline OpenAI Provider contract fixtures."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from astraweft_provider_sdk import HttpResponse, ProviderContext, SecretValue


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    json_body: Mapping[str, object] | None
    timeout_seconds: float | None
    idempotency_key: str | None
    trace_id: str | None


class FakeHttp:
    def __init__(self) -> None:
        self.responses: list[HttpResponse] = []
        self.requests: list[RecordedRequest] = []

    def queue(
        self,
        status: int,
        payload: Mapping[str, object] | bytes,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        body = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        )
        self.responses.append(
            HttpResponse(
                status=status,
                headers={key.lower(): value for key, value in (headers or {}).items()},
                body=body,
            )
        )

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
    ) -> HttpResponse:
        self.requests.append(
            RecordedRequest(
                method,
                url,
                dict(headers or {}),
                None if json_body is None else dict(json_body),
                timeout_seconds,
                idempotency_key,
                trace_id,
            )
        )
        if not self.responses:
            raise AssertionError("offline HTTP response queue is empty")
        return self.responses.pop(0)


class FakeSecrets:
    def __init__(self, value: str = "sk-offline-test") -> None:
        self.value = value

    async def get(self, credential_ref: str, field: str) -> SecretValue:
        assert credential_ref == "credential-ref"
        assert field == "api_key"
        return SecretValue(self.value)


class FakeClock:
    def __init__(self) -> None:
        self.tick = 10.0

    def now(self) -> datetime:
        return datetime(2026, 7, 15, tzinfo=UTC)

    def monotonic(self) -> float:
        self.tick += 0.025
        return self.tick


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
def fake_http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture
def provider_context(tmp_path: Path, fake_http: FakeHttp) -> ProviderContext:
    return ProviderContext(
        http=fake_http,
        secrets=FakeSecrets(),
        logger=SilentLogger(),
        clock=FakeClock(),
        plugin_data=TempData(tmp_path),
        core_version="0.1-test",
        plugin_api_version="1.0",
    )
