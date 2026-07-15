"""Runway async task mapping through AstraWeft Core's HTTP transport."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Never, cast

from astraweft_provider_sdk import (
    CancelResult,
    HealthCheckResult,
    HttpResponse,
    PluginConfigurationError,
    ProviderAuthenticationError,
    ProviderCallInfo,
    ProviderContext,
    ProviderError,
    ProviderModel,
    ProviderOutput,
    ProviderPermissionError,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderRequest,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    RemoteArtifact,
    RemoteError,
    RemoteTaskSnapshot,
    SubmissionResult,
    UnsupportedOperationError,
)
from astraweft_runway_provider.schemas import (
    VIDEO_OUTPUT_SCHEMA,
    VIDEO_PARAMETER_SCHEMA,
    VIDEO_PARAMETER_UI_SCHEMA,
)

_BASE_URL = "https://api.dev.runwayml.com"
_ORGANIZATION_PATH = "/v1/organization"
_TEXT_TO_VIDEO_PATH = "/v1/text_to_video"
_TASK_PATH_TEMPLATE = "/v1/tasks/{task_id}"
_API_VERSION = "2024-11-06"
_MODEL_ID = "gen4.5"
_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_RETRYABLE_FAILURE_CODES = frozenset(
    {
        "INPUT_PREPROCESSING.INTERNAL",
        "THIRD_PARTY.UNAVAILABLE",
        "INTERNAL",
    }
)
_NON_RETRYABLE_FAILURE_CODES = frozenset({"SAFETY", "ASSET.INVALID"})


class RunwayProviderClient:
    """Map Runway's asynchronous task API to the stable Provider SDK."""

    def __init__(
        self,
        context: ProviderContext,
        settings: Mapping[str, object],
        credential_ref: str | None,
    ) -> None:
        self._context = context
        self._settings = dict(settings)
        self._credential_ref = credential_ref
        self._closed = False

    async def health_check(self) -> HealthCheckResult:
        self._ensure_open()
        started = self._context.clock.monotonic()
        response = await self._request("GET", _ORGANIZATION_PATH)
        _raise_for_status(response, "GET", _ORGANIZATION_PATH)
        payload = _json_object(response, "GET", _ORGANIZATION_PATH)
        if not _valid_organization(payload):
            raise ProviderProtocolError(
                "Runway 组织信息格式无效",
                call=_call(response, "GET", _ORGANIZATION_PATH),
            )
        latency_ms = max(0, int((self._context.clock.monotonic() - started) * 1000))
        return HealthCheckResult(
            status="healthy",
            latency_ms=latency_ms,
            message="Runway API 连接正常",
            details={"endpoint": "api.dev.runwayml.com"},
        )

    async def list_models(self) -> tuple[ProviderModel, ...]:
        self._ensure_open()
        return (_provider_model(),)

    async def submit(self, request: ProviderRequest) -> SubmissionResult:
        self._ensure_open()
        if request.operation != "video.generate":
            raise UnsupportedOperationError("Runway Provider 当前仅支持视频生成")
        if request.remote_model_id != _MODEL_ID:
            raise ProviderValidationError("Runway Provider 当前仅支持 gen4.5")
        poll_interval = self._poll_interval()
        response = await self._request(
            "POST",
            _TEXT_TO_VIDEO_PATH,
            json_body=_request_body(request),
            timeout_seconds=min(request.timeout_seconds, self._request_timeout()),
            trace_id=request.trace_id,
        )
        _raise_for_status(response, "POST", _TEXT_TO_VIDEO_PATH)
        payload = _json_object(response, "POST", _TEXT_TO_VIDEO_PATH)
        task_id = payload.get("id")
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise ProviderProtocolError(
                "Runway 未返回有效的远程任务标识",
                call=_call(response, "POST", _TEXT_TO_VIDEO_PATH),
            )
        return SubmissionResult(
            mode="accepted",
            remote_task_id=task_id,
            progress=0,
            poll_after_seconds=poll_interval,
            call=_call(response, "POST", _TEXT_TO_VIDEO_PATH),
        )

    async def get_task(self, remote_task_id: str) -> RemoteTaskSnapshot:
        self._ensure_open()
        task_id = _valid_task_id(remote_task_id)
        response = await self._request(
            "GET",
            f"/v1/tasks/{task_id}",
            timeout_seconds=self._request_timeout(),
        )
        _raise_for_status(response, "GET", _TASK_PATH_TEMPLATE)
        payload = _json_object(response, "GET", _TASK_PATH_TEMPLATE)
        return _snapshot(
            payload,
            call=_call(response, "GET", _TASK_PATH_TEMPLATE),
            poll_interval=self._poll_interval(),
        )

    async def cancel_task(self, remote_task_id: str) -> CancelResult:
        self._ensure_open()
        task_id = _valid_task_id(remote_task_id)
        response = await self._request(
            "DELETE",
            f"/v1/tasks/{task_id}",
            timeout_seconds=self._request_timeout(),
        )
        _raise_for_status(response, "DELETE", _TASK_PATH_TEMPLATE)
        return CancelResult(
            accepted=True,
            terminal=True,
            message="Runway 已接受取消请求",
            call=_call(response, "DELETE", _TASK_PATH_TEMPLATE),
        )

    async def close(self) -> None:
        self._closed = True

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, object] | None = None,
        timeout_seconds: float | None = None,
        trace_id: str | None = None,
    ) -> HttpResponse:
        return await self._context.http.request(
            method,
            f"{_BASE_URL}{path}",
            headers=await self._headers(),
            json_body=json_body,
            timeout_seconds=self._request_timeout() if timeout_seconds is None else timeout_seconds,
            trace_id=trace_id,
        )

    async def _headers(self) -> dict[str, str]:
        if self._credential_ref is None:
            raise ProviderAuthenticationError("请先配置 Runway API Key")
        secret = await self._context.secrets.get(self._credential_ref, "api_key")
        return {
            "Authorization": secret.as_bearer(),
            "Content-Type": "application/json",
            "X-Runway-Version": _API_VERSION,
        }

    def _request_timeout(self) -> float:
        return _number_setting(
            self._settings,
            "request_timeout_seconds",
            default=60,
            minimum=1,
            maximum=300,
        )

    def _poll_interval(self) -> float:
        return _number_setting(
            self._settings,
            "poll_interval_seconds",
            default=5,
            minimum=5,
            maximum=60,
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProviderProtocolError("Runway Provider client is already closed")


def _provider_model() -> ProviderModel:
    return ProviderModel(
        remote_model_id=_MODEL_ID,
        display_name="Runway Gen-4.5",
        modality="VIDEO",
        operations=frozenset({"video.generate"}),
        parameter_schema=VIDEO_PARAMETER_SCHEMA,
        parameter_ui_schema=VIDEO_PARAMETER_UI_SCHEMA,
        output_schema=VIDEO_OUTPUT_SCHEMA,
        capabilities={
            "text_to_video": True,
            "async_tasks": True,
            "cancel": True,
            "output_resolution": ["1280:720", "720:1280"],
        },
        pricing=(),
    )


def _request_body(request: ProviderRequest) -> dict[str, object]:
    unexpected = set(request.inputs) - {"prompt", "duration", "ratio", "seed"}
    if unexpected:
        raise ProviderValidationError(
            "Runway 请求包含当前适配器不支持的参数",
            safe_details={"unsupported_fields": sorted(unexpected)},
        )
    prompt = request.inputs.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ProviderValidationError("请输入非空 Prompt")
    if len(prompt.encode("utf-16-le")) // 2 > 1000:
        raise ProviderValidationError("Runway Prompt 不能超过 1000 个 UTF-16 code units")
    duration = request.inputs.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, int) or not 2 <= duration <= 10:
        raise ProviderValidationError("Runway 视频时长必须在 2 到 10 秒之间")
    ratio = request.inputs.get("ratio")
    if ratio not in {"1280:720", "720:1280"}:
        raise ProviderValidationError("Runway 画幅参数无效")
    body: dict[str, object] = {
        "model": request.remote_model_id,
        "promptText": prompt,
        "duration": duration,
        "ratio": ratio,
    }
    seed = request.inputs.get("seed")
    if seed is not None:
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 4294967295:
            raise ProviderValidationError("Runway Seed 必须在 0 到 4294967295 之间")
        body["seed"] = seed
    return body


def _snapshot(
    payload: Mapping[str, object],
    *,
    call: ProviderCallInfo,
    poll_interval: float,
) -> RemoteTaskSnapshot:
    status = payload.get("status")
    if status in {"PENDING", "THROTTLED"}:
        return RemoteTaskSnapshot(
            state="queued",
            progress=0,
            poll_after_seconds=poll_interval,
            call=call,
        )
    if status == "RUNNING":
        return RemoteTaskSnapshot(
            state="running",
            progress=_progress(payload.get("progress"), call),
            poll_after_seconds=poll_interval,
            call=call,
        )
    if status == "CANCELLED":
        return RemoteTaskSnapshot(state="canceled", call=call)
    if status == "FAILED":
        code = _safe_code(payload.get("failureCode"))
        retryable = code is None or code in _RETRYABLE_FAILURE_CODES
        if code in _NON_RETRYABLE_FAILURE_CODES:
            retryable = False
        details: dict[str, object] = {"provider_status": "FAILED"}
        if code is not None:
            details["failure_code"] = code
        return RemoteTaskSnapshot(
            state="failed",
            error=RemoteError(
                code="runway_task_failed",
                message="Runway 视频生成失败",
                retryable=retryable,
                safe_details=details,
            ),
            call=call,
        )
    if status == "SUCCEEDED":
        raw_output = payload.get("output")
        if not isinstance(raw_output, list) or not raw_output:
            raise ProviderProtocolError("Runway 成功任务没有成果 URL", call=call)
        urls: list[str] = []
        for value in raw_output:
            if not isinstance(value, str) or not value.startswith("https://"):
                raise ProviderProtocolError("Runway 成果 URL 格式无效", call=call)
            urls.append(value)
        artifacts = tuple(
            RemoteArtifact(
                kind="video",
                source="url",
                value=url,
                mime_type="video/mp4",
                filename_hint=f"runway-{index}.mp4",
                metadata={"provider": "runway", "output_index": index},
            )
            for index, url in enumerate(urls, start=1)
        )
        return RemoteTaskSnapshot(
            state="succeeded",
            progress=100,
            output=ProviderOutput(
                data={"video_count": len(artifacts)},
                artifacts=artifacts,
                finish_reason="succeeded",
            ),
            call=call,
        )
    raise ProviderProtocolError("Runway 返回了未知任务状态", call=call)


def _progress(value: object, call: ProviderCallInfo) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ProviderProtocolError("Runway 任务进度格式无效", call=call)
    return round(float(value) * 100)


def _valid_task_id(value: str) -> str:
    normalized = value.strip()
    if not _TASK_ID.fullmatch(normalized):
        raise ProviderValidationError("Runway 远程任务标识无效")
    return normalized


def _valid_organization(payload: Mapping[str, object]) -> bool:
    balance = payload.get("creditBalance")
    return (
        isinstance(balance, int)
        and not isinstance(balance, bool)
        and balance >= 0
        and isinstance(payload.get("tier"), Mapping)
        and isinstance(payload.get("usage"), Mapping)
    )


def _json_object(response: HttpResponse, method: str, path: str) -> Mapping[str, object]:
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderProtocolError(
            "Runway 返回了无效 JSON",
            call=_call(response, method, path),
        ) from exc
    if not isinstance(value, Mapping):
        raise ProviderProtocolError(
            "Runway JSON 响应根节点必须是对象",
            call=_call(response, method, path),
        )
    return cast(Mapping[str, object], value)


def _call(response: HttpResponse, method: str, path: str) -> ProviderCallInfo:
    return ProviderCallInfo(
        method=method,
        url_template=path,
        http_status=response.status,
        provider_request_id=_safe_request_id(response.headers.get("x-request-id")),
    )


def _safe_request_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized if normalized and len(normalized) <= 512 and normalized.isascii() else None


def _safe_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 128 or not normalized.isascii():
        return None
    return normalized


def _raise_for_status(response: HttpResponse, method: str, path: str) -> None:
    if 200 <= response.status < 300:
        return
    call = _call(response, method, path)
    provider_code = _provider_error_code(response)
    details: dict[str, object] = {"http_status": response.status}
    if provider_code is not None:
        details["provider_error_code"] = provider_code
    if response.status == 401:
        _raise_normalized(
            ProviderAuthenticationError,
            "Runway API Key 无效或已失效",
            response,
            call,
            provider_code,
            details,
        )
    if response.status == 403:
        _raise_normalized(
            ProviderPermissionError,
            "Runway 拒绝了该组织的访问",
            response,
            call,
            provider_code,
            details,
        )
    if response.status == 408:
        _raise_normalized(
            ProviderTimeoutError,
            "Runway 请求超时，可稍后重试",
            response,
            call,
            provider_code,
            details,
        )
    if response.status == 429:
        _raise_normalized(
            ProviderRateLimitError,
            "Runway 已达到当前用量或并发限制",
            response,
            call,
            provider_code,
            details,
            retry_after_seconds=_retry_after(response),
        )
    if response.status in {502, 503, 504}:
        _raise_normalized(
            ProviderUnavailableError,
            "Runway 服务暂时不可用",
            response,
            call,
            provider_code,
            details,
        )
    if response.status in {400, 404, 405, 422}:
        _raise_normalized(
            ProviderValidationError,
            "Runway 拒绝了请求，请检查模型、任务和参数",
            response,
            call,
            provider_code,
            details,
        )
    _raise_normalized(
        ProviderProtocolError,
        "Runway 返回了未知 HTTP 状态",
        response,
        call,
        provider_code,
        details,
    )


def _raise_normalized(
    error_type: type[ProviderError],
    user_message: str,
    response: HttpResponse,
    call: ProviderCallInfo,
    provider_code: str | None,
    safe_details: Mapping[str, object],
    *,
    retry_after_seconds: float | None = None,
) -> Never:
    raise error_type(
        user_message,
        technical_message=f"Runway HTTP {response.status}",
        retry_after_seconds=retry_after_seconds,
        provider_code=provider_code,
        call=call,
        safe_details=safe_details,
    )


def _provider_error_code(response: HttpResponse) -> str | None:
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    error = payload.get("error")
    if isinstance(error, Mapping):
        return _safe_code(error.get("code"))
    return _safe_code(payload.get("code"))


def _retry_after(response: HttpResponse) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _number_setting(
    settings: Mapping[str, object],
    key: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = settings.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not minimum <= value <= maximum
    ):
        raise PluginConfigurationError(
            f"Runway setting {key} must be between {minimum:g} and {maximum:g}"
        )
    return float(value)
