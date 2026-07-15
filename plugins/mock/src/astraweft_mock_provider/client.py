"""Deterministic Mock Provider client and fault injector."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from astraweft_mock_provider.schemas import (
    ARTIFACT_OUTPUT_SCHEMA,
    IMAGE_PARAMETER_SCHEMA,
    PARAMETER_UI_SCHEMA,
    TEXT_OUTPUT_SCHEMA,
    TEXT_PARAMETER_SCHEMA,
    VIDEO_PARAMETER_SCHEMA,
)
from astraweft_provider_sdk import (
    CancelResult,
    HealthCheckResult,
    PluginConfigurationError,
    PricingRule,
    ProviderAuthenticationError,
    ProviderContext,
    ProviderModel,
    ProviderOutput,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderRequest,
    ProviderTaskFailedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RemoteArtifact,
    RemoteTaskSnapshot,
    SubmissionResult,
    Usage,
)

_VALID_KEY = "mock-valid-key"
_STORE_LOCK = threading.Lock()


class MockProviderClient:
    """Deterministic client whose remote-task simulator survives Core restarts."""

    def __init__(
        self,
        context: ProviderContext,
        settings: Mapping[str, object],
        credential_ref: str | None,
    ) -> None:
        self._context = context
        self._settings = dict(settings)
        self._credential_ref = credential_ref
        self._task_store = context.plugin_data.path_for("remote-tasks.json")
        self._closed = False

    async def health_check(self) -> HealthCheckResult:
        started = self._context.clock.monotonic()
        await self._before_operation()
        latency_ms = max(0, int((self._context.clock.monotonic() - started) * 1000))
        return HealthCheckResult(
            status="healthy",
            latency_ms=latency_ms,
            message="Mock Provider is ready",
            details={"network": "disabled", "billing": "disabled"},
        )

    async def list_models(self) -> tuple[ProviderModel, ...]:
        await self._before_operation()
        revision = self._integer_setting("catalog_revision", 1)
        common = (
            ProviderModel(
                remote_model_id="mock-text-v1",
                display_name="Mock Text" if revision == 1 else "Mock Text Updated",
                modality="TEXT",
                operations=frozenset({"text.generate"}),
                parameter_schema=TEXT_PARAMETER_SCHEMA,
                parameter_ui_schema=PARAMETER_UI_SCHEMA,
                output_schema=TEXT_OUTPUT_SCHEMA,
                capabilities={"streaming": False, "max_context": 8192},
                pricing=(PricingRule("request", 1_000, "USD", "2026-01-01T00:00:00Z"),),
            ),
        )
        if revision == 1:
            return (
                *common,
                ProviderModel(
                    remote_model_id="mock-image-v1",
                    display_name="Mock Image",
                    modality="IMAGE",
                    operations=frozenset({"image.generate"}),
                    parameter_schema=IMAGE_PARAMETER_SCHEMA,
                    parameter_ui_schema=PARAMETER_UI_SCHEMA,
                    output_schema=ARTIFACT_OUTPUT_SCHEMA,
                    capabilities={"sizes": [512, 1024]},
                    pricing=(PricingRule("image", 5_000, "USD"),),
                ),
            )
        return (
            *common,
            ProviderModel(
                remote_model_id="mock-video-v1",
                display_name="Mock Video",
                modality="VIDEO",
                operations=frozenset({"video.generate"}),
                parameter_schema=VIDEO_PARAMETER_SCHEMA,
                parameter_ui_schema=PARAMETER_UI_SCHEMA,
                output_schema=ARTIFACT_OUTPUT_SCHEMA,
                capabilities={"async": True, "max_duration": 10},
                pricing=(PricingRule("second", 2_000, "USD"),),
            ),
        )

    async def submit(self, request: ProviderRequest) -> SubmissionResult:
        await self._before_operation()
        if self._mode() == "task_failed":
            raise ProviderTaskFailedError("Mock task failed", provider_code="MOCK_TASK_FAILED")
        if request.operation not in {"text.generate", "image.generate", "video.generate"}:
            raise ProviderProtocolError("Mock request uses an unsupported operation")
        if self._string_setting("response_mode", "completed") == "accepted":
            task_id = "mock-" + hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:16]
            with _STORE_LOCK:
                tasks = self._load_tasks()
                tasks.setdefault(
                    task_id,
                    {"polls": 0, "operation": request.operation, "canceled": False},
                )
                self._save_tasks(tasks)
            return SubmissionResult(
                mode="accepted",
                remote_task_id=task_id,
                progress=0,
                poll_after_seconds=0.01,
                provider_request_id=f"request-{task_id}",
            )
        return SubmissionResult(
            mode="completed",
            output=self._output_for(request.operation),
            progress=100,
            provider_request_id="mock-sync-request",
        )

    async def get_task(self, remote_task_id: str) -> RemoteTaskSnapshot:
        await self._before_operation()
        with _STORE_LOCK:
            tasks = self._load_tasks()
            record = tasks.get(remote_task_id)
            if record is None:
                raise ProviderProtocolError("Mock remote task was not found")
            if record.get("canceled") is True:
                return RemoteTaskSnapshot(state="canceled", progress=0)
            polls = record.get("polls")
            operation = record.get("operation")
            if isinstance(polls, bool) or not isinstance(polls, int):
                raise ProviderProtocolError("Mock remote task state is invalid")
            if not isinstance(operation, str):
                raise ProviderProtocolError("Mock remote task operation is invalid")
            if polls == 0:
                record["polls"] = 1
                self._save_tasks(tasks)
                return RemoteTaskSnapshot(state="running", progress=50, poll_after_seconds=0.01)
        return RemoteTaskSnapshot(
            state="succeeded",
            progress=100,
            output=self._output_for(operation),
        )

    async def cancel_task(self, remote_task_id: str) -> CancelResult:
        await self._before_operation()
        with _STORE_LOCK:
            tasks = self._load_tasks()
            record = tasks.get(remote_task_id)
            if record is None:
                return CancelResult(
                    accepted=False,
                    terminal=False,
                    message="Mock task was not found",
                )
            record["canceled"] = True
            self._save_tasks(tasks)
        return CancelResult(accepted=True, terminal=True, message="Mock task canceled")

    async def close(self) -> None:
        self._closed = True

    async def _before_operation(self) -> None:
        self._ensure_open()
        delay_ms = self._integer_setting("delay_ms", 0)
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)
        await self._authenticate()
        mode = self._mode()
        if mode == "rate_limit":
            raise ProviderRateLimitError("Mock rate limit reached", retry_after_seconds=1.5)
        if mode == "unavailable":
            raise ProviderUnavailableError("Mock Provider is unavailable")
        if mode == "timeout":
            raise ProviderTimeoutError("Mock Provider timed out")
        if mode == "protocol_error":
            raise ProviderProtocolError("Mock Provider returned an invalid response")

    async def _authenticate(self) -> None:
        if self._credential_ref is None:
            raise ProviderAuthenticationError("Mock credential is required")
        secret = await self._context.secrets.get(self._credential_ref, "api_key")
        if self._mode() == "authentication_error" or secret.reveal() != _VALID_KEY:
            raise ProviderAuthenticationError("Mock credential was rejected")

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProviderProtocolError("Mock client is already closed")

    def _mode(self) -> str:
        return self._string_setting("mode", "healthy")

    def _string_setting(self, key: str, default: str) -> str:
        value = self._settings.get(key, default)
        if not isinstance(value, str):
            raise PluginConfigurationError(f"Mock setting {key} must be a string")
        return value

    def _integer_setting(self, key: str, default: int) -> int:
        value = self._settings.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise PluginConfigurationError(f"Mock setting {key} must be an integer")
        return value

    def _load_tasks(self) -> dict[str, dict[str, object]]:
        if not self._task_store.exists():
            return {}
        try:
            loaded = json.loads(self._task_store.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProviderProtocolError("Mock remote task store is unreadable") from exc
        if not isinstance(loaded, dict):
            raise ProviderProtocolError("Mock remote task store is invalid")
        tasks: dict[str, dict[str, object]] = {}
        for task_id, record in loaded.items():
            if not isinstance(task_id, str) or not isinstance(record, dict):
                raise ProviderProtocolError("Mock remote task store is invalid")
            tasks[task_id] = {str(key): value for key, value in record.items()}
        return tasks

    def _save_tasks(self, tasks: Mapping[str, Mapping[str, object]]) -> None:
        self._task_store.parent.mkdir(parents=True, exist_ok=True)
        partial = Path(f"{self._task_store}.partial")
        try:
            partial.write_text(
                json.dumps(tasks, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(partial, self._task_store)
        finally:
            partial.unlink(missing_ok=True)

    @staticmethod
    def _output_for(operation: str) -> ProviderOutput:
        usage = Usage(
            units={"requests": 1}, cost_micros=1_000, currency="USD", pricing_source="mock"
        )
        if operation == "text.generate":
            return ProviderOutput(
                data={"text": "Mock response"},
                usage=usage,
                finish_reason="stop",
            )
        kind: Literal["video", "image"] = "video" if operation == "video.generate" else "image"
        return ProviderOutput(
            data={"artifact_count": 1},
            artifacts=(
                RemoteArtifact(
                    kind=kind,
                    source="text",
                    value=f"mock-{kind}-artifact",
                    mime_type="text/plain",
                    filename_hint=f"mock.{kind}.txt",
                ),
            ),
            usage=usage,
            finish_reason="completed",
        )
