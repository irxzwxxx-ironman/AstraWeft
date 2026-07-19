"""Offline Custom REST Provider fixtures."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

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
        payload: object,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
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
    values: ClassVar[dict[str, str]] = {
        "api_key": "KEY_SECRET_CANARY",
        "api_secret": "SECOND_SECRET_CANARY",
        "username": "user",
        "password": "pass",
    }

    async def get(self, credential_ref: str, field: str) -> SecretValue:
        assert credential_ref == "credential-ref"
        return SecretValue(self.values[field])


class FakeClock:
    def __init__(self) -> None:
        self.tick = 1.0

    def now(self) -> datetime:
        return datetime(2026, 7, 16, tzinfo=UTC)

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
        endpoint="https://gateway.example.test/api",
    )


@pytest.fixture
def definition() -> dict[str, object]:
    output = {
        "data": {"request_id": "/id"},
        "artifacts": [
            {
                "kind": "image",
                "source": "url",
                "pointer": "/result/url",
                "mime_type": "image/png",
            }
        ],
    }
    async_output = {
        "data": {"request_id": "/id"},
        "artifacts": [
            {
                "kind": "video",
                "source": "url",
                "pointer": "/result/url",
                "mime_type": "video/mp4",
            }
        ],
    }
    return {
        "health": {"method": "GET", "path": "/health"},
        "models": [
            {
                "id": "multi-model",
                "name": "Multi endpoint model",
                "modality": "MULTIMODAL",
                "operations": ["image.generate", "video.generate"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "seed": {"type": "integer"},
                    },
                    "required": ["prompt"],
                    "additionalProperties": False,
                },
                "input_ui_schema": {"prompt": {"ui:widget": "textarea"}},
                "requests": {
                    "image.generate": {
                        "submit": {
                            "method": "POST",
                            "path": "/images/${model_id}",
                            "headers": {"X-Provider-Secret": "${secret.api_secret}"},
                            "query": {"seed": "${input.seed}"},
                            "body": {
                                "prompt": "${input.prompt}",
                                "model": "${model_id}",
                            },
                        },
                        "response": {"mode": "sync", "output": output},
                    },
                    "video.generate": {
                        "submit": {
                            "method": "POST",
                            "path": "/videos",
                            "body": {"prompt": "${input.prompt}"},
                        },
                        "response": {
                            "mode": "async",
                            "task_id": "/id",
                            "poll": {
                                "method": "GET",
                                "path": "/jobs/${remote_task_id}",
                            },
                            "cancel": {
                                "method": "DELETE",
                                "path": "/jobs/${remote_task_id}",
                                "terminal": True,
                            },
                            "state": "/status",
                            "states": {
                                "queued": ["queued"],
                                "running": ["running"],
                                "succeeded": ["done"],
                                "failed": ["failed"],
                                "canceled": ["canceled"],
                            },
                            "progress": "/progress",
                            "poll_after_seconds": 1.5,
                            "output": async_output,
                        },
                    },
                },
            }
        ],
    }


@pytest.fixture
def settings(definition: dict[str, object]) -> dict[str, object]:
    return {
        "auth_mode": "bearer",
        "auth_header_name": "X-API-Key",
        "auth_prefix": "",
        "request_timeout_seconds": 30,
        "additional_allowed_hosts": ["assets.example.test"],
        "definition": definition,
    }
