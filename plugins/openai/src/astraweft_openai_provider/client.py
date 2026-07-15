"""OpenAI Models and Responses API mapping through Core's HTTP transport."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Never, cast

from astraweft_openai_provider.schemas import (
    TEXT_OUTPUT_SCHEMA,
    TEXT_PARAMETER_SCHEMA,
    TEXT_PARAMETER_UI_SCHEMA,
)
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
    ProviderTaskFailedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    RemoteTaskSnapshot,
    SubmissionResult,
    UnsupportedOperationError,
    Usage,
)

_BASE_URL = "https://api.openai.com/v1"
_MODELS_PATH = "/v1/models"
_RESPONSES_PATH = "/v1/responses"
_EXCLUDED_MODEL_MARKERS = (
    "audio",
    "chat",
    "codex",
    "computer-use",
    "deep-research",
    "embedding",
    "image",
    "moderation",
    "realtime",
    "search",
    "transcribe",
    "tts",
)


class OpenAIProviderClient:
    """Map stable SDK operations to a conservative Responses API slice."""

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
        response = await self._request("GET", _MODELS_PATH)
        _raise_for_status(response, "GET", _MODELS_PATH)
        payload = _json_object(response, "GET", _MODELS_PATH)
        if not isinstance(payload.get("data"), list):
            raise ProviderProtocolError(
                "OpenAI 模型目录格式无效",
                call=_call(response, "GET", _MODELS_PATH),
            )
        latency_ms = max(0, int((self._context.clock.monotonic() - started) * 1000))
        return HealthCheckResult(
            status="healthy",
            latency_ms=latency_ms,
            message="OpenAI API 连接正常",
            details={"endpoint": "api.openai.com", "storage": "disabled"},
        )

    async def list_models(self) -> tuple[ProviderModel, ...]:
        self._ensure_open()
        response = await self._request("GET", _MODELS_PATH)
        _raise_for_status(response, "GET", _MODELS_PATH)
        payload = _json_object(response, "GET", _MODELS_PATH)
        data = payload.get("data")
        if not isinstance(data, list):
            raise ProviderProtocolError(
                "OpenAI 模型目录格式无效", call=_call(response, "GET", _MODELS_PATH)
            )
        ids = sorted(
            {
                model_id
                for item in data
                if isinstance(item, Mapping)
                and isinstance((model_id := item.get("id")), str)
                and _supports_basic_text(model_id)
            }
        )
        return tuple(_provider_model(model_id) for model_id in ids)

    async def submit(self, request: ProviderRequest) -> SubmissionResult:
        self._ensure_open()
        if request.operation != "text.generate":
            raise UnsupportedOperationError("OpenAI Provider 当前仅支持文本生成")
        body = _request_body(request)
        response = await self._request(
            "POST",
            _RESPONSES_PATH,
            json_body=body,
            timeout_seconds=min(request.timeout_seconds, self._request_timeout()),
            client_request_id=_client_request_id(request.trace_id),
            trace_id=request.trace_id,
        )
        _raise_for_status(response, "POST", _RESPONSES_PATH)
        payload = _json_object(response, "POST", _RESPONSES_PATH)
        status = payload.get("status")
        call = _call(response, "POST", _RESPONSES_PATH)
        if status == "failed":
            error = payload.get("error")
            provider_code = error.get("code") if isinstance(error, Mapping) else None
            raise ProviderTaskFailedError(
                "OpenAI 未能完成本次生成",
                provider_code=provider_code if isinstance(provider_code, str) else None,
                call=call,
            )
        if status not in ("completed", "incomplete"):
            raise ProviderProtocolError("OpenAI 返回了非同步终态响应", call=call)
        text, refused = _output_text(payload)
        if text is None:
            raise ProviderProtocolError("OpenAI 响应中没有可用的文本输出", call=call)
        finish_reason = "refusal" if refused else _finish_reason(payload, status)
        return SubmissionResult(
            mode="completed",
            output=ProviderOutput(
                data={"text": text},
                usage=_usage(payload),
                finish_reason=finish_reason,
            ),
            progress=100,
            call=call,
        )

    async def get_task(self, remote_task_id: str) -> RemoteTaskSnapshot:
        del remote_task_id
        raise UnsupportedOperationError("OpenAI Provider 当前不使用远程异步任务")

    async def cancel_task(self, remote_task_id: str) -> CancelResult:
        del remote_task_id
        raise UnsupportedOperationError("OpenAI Provider 当前没有可取消的远程任务")

    async def close(self) -> None:
        self._closed = True

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, object] | None = None,
        timeout_seconds: float | None = None,
        client_request_id: str | None = None,
        trace_id: str | None = None,
    ) -> HttpResponse:
        headers = await self._headers()
        if client_request_id is not None:
            headers["X-Client-Request-Id"] = client_request_id
        return await self._context.http.request(
            method,
            f"{_BASE_URL}{path.removeprefix('/v1')}",
            headers=headers,
            json_body=json_body,
            timeout_seconds=self._request_timeout() if timeout_seconds is None else timeout_seconds,
            trace_id=trace_id,
        )

    async def _headers(self) -> dict[str, str]:
        if self._credential_ref is None:
            raise ProviderAuthenticationError("请先配置 OpenAI API Key")
        secret = await self._context.secrets.get(self._credential_ref, "api_key")
        headers = {
            "Authorization": secret.as_bearer(),
            "Content-Type": "application/json",
        }
        organization = self._optional_setting("organization")
        project = self._optional_setting("project")
        if organization is not None:
            headers["OpenAI-Organization"] = organization
        if project is not None:
            headers["OpenAI-Project"] = project
        return headers

    def _optional_setting(self, key: str) -> str | None:
        value = self._settings.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise PluginConfigurationError(f"OpenAI setting {key} must be a non-empty string")
        return value.strip()

    def _request_timeout(self) -> float:
        value = self._settings.get("request_timeout_seconds", 60)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 300:
            raise PluginConfigurationError(
                "OpenAI request timeout must be between 1 and 300 seconds"
            )
        return float(value)

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProviderProtocolError("OpenAI Provider client is already closed")


def _provider_model(model_id: str) -> ProviderModel:
    return ProviderModel(
        remote_model_id=model_id,
        display_name=model_id,
        modality="TEXT",
        operations=frozenset({"text.generate"}),
        parameter_schema=TEXT_PARAMETER_SCHEMA,
        parameter_ui_schema=TEXT_PARAMETER_UI_SCHEMA,
        output_schema=TEXT_OUTPUT_SCHEMA,
        capabilities={"responses_api": True, "streaming": False},
        pricing=(),
    )


def _supports_basic_text(model_id: str) -> bool:
    normalized = model_id.lower()
    base = normalized.partition(":")[2] if normalized.startswith("ft:") else normalized
    if any(marker in base for marker in _EXCLUDED_MODEL_MARKERS):
        return False
    return base.startswith(("gpt-4", "gpt-5", "o1", "o3", "o4"))


def _request_body(request: ProviderRequest) -> dict[str, object]:
    unexpected = set(request.inputs) - {"prompt", "instructions", "max_output_tokens"}
    if unexpected:
        raise ProviderValidationError(
            "OpenAI 请求包含当前适配器不支持的参数",
            safe_details={"unsupported_fields": sorted(unexpected)},
        )
    prompt = request.inputs.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ProviderValidationError("请输入非空 Prompt")
    body: dict[str, object] = {
        "model": request.remote_model_id,
        "input": prompt,
        "store": False,
    }
    instructions = request.inputs.get("instructions")
    if instructions is not None:
        if not isinstance(instructions, str) or not instructions.strip():
            raise ProviderValidationError("Instructions 必须是非空文本")
        body["instructions"] = instructions
    max_tokens = request.inputs.get("max_output_tokens")
    if max_tokens is not None:
        if (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or not 1 <= max_tokens <= 100000
        ):
            raise ProviderValidationError("Max output tokens 必须在 1 到 100000 之间")
        body["max_output_tokens"] = max_tokens
    return body


def _json_object(response: HttpResponse, method: str, path: str) -> Mapping[str, object]:
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderProtocolError(
            "OpenAI 返回了无效 JSON",
            call=_call(response, method, path),
        ) from exc
    if not isinstance(value, Mapping):
        raise ProviderProtocolError(
            "OpenAI JSON 响应根节点必须是对象",
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


def _client_request_id(trace_id: str) -> str:
    normalized = trace_id.strip()
    if normalized and len(normalized) <= 480 and normalized.isascii():
        return f"astraweft-{normalized}"
    return f"astraweft-{hashlib.sha256(trace_id.encode()).hexdigest()}"


def _raise_for_status(response: HttpResponse, method: str, path: str) -> None:
    if 200 <= response.status < 300:
        return
    call = _call(response, method, path)
    provider_type, provider_code = _provider_error_identity(response)
    details: dict[str, object] = {"http_status": response.status}
    if provider_type is not None:
        details["provider_error_type"] = provider_type
    if provider_code is not None:
        details["provider_error_code"] = provider_code
    if response.status == 401:
        _raise_normalized(
            ProviderAuthenticationError,
            "OpenAI API Key 无效或已失效",
            response,
            call,
            provider_code,
            details,
        )
    if response.status == 403:
        _raise_normalized(
            ProviderPermissionError,
            "OpenAI 拒绝了该项目或区域的访问",
            response,
            call,
            provider_code,
            details,
        )
    if response.status == 408:
        _raise_normalized(
            ProviderTimeoutError,
            "OpenAI 请求超时，可稍后重试",
            response,
            call,
            provider_code,
            details,
        )
    if response.status == 429:
        _raise_normalized(
            ProviderRateLimitError,
            "OpenAI 请求过多或配额不足",
            response,
            call,
            provider_code,
            details,
            retry_after_seconds=_retry_after(response),
            retryable=provider_code != "insufficient_quota",
        )
    if response.status in {500, 502, 503, 504}:
        _raise_normalized(
            ProviderUnavailableError,
            "OpenAI 服务暂时不可用",
            response,
            call,
            provider_code,
            details,
        )
    if response.status in {400, 404, 422}:
        _raise_normalized(
            ProviderValidationError,
            "OpenAI 拒绝了请求，请检查模型和参数",
            response,
            call,
            provider_code,
            details,
        )
    _raise_normalized(
        ProviderProtocolError,
        "OpenAI 返回了未知 HTTP 状态",
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
    retryable: bool | None = None,
) -> Never:
    raise error_type(
        user_message,
        technical_message=f"OpenAI HTTP {response.status}",
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
        provider_code=provider_code,
        call=call,
        safe_details=safe_details,
    )


def _provider_error_identity(response: HttpResponse) -> tuple[str | None, str | None]:
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    error = payload.get("error") if isinstance(payload, Mapping) else None
    if not isinstance(error, Mapping):
        return None, None
    error_type = error.get("type")
    code = error.get("code")
    return (
        error_type if isinstance(error_type, str) else None,
        code if isinstance(code, str) else None,
    )


def _retry_after(response: HttpResponse) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _output_text(payload: Mapping[str, object]) -> tuple[str | None, bool]:
    output = payload.get("output")
    if not isinstance(output, list):
        return None, False
    texts: list[str] = []
    refusals: list[str] = []
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, Mapping):
                continue
            value = part.get("text") if part.get("type") == "output_text" else None
            refusal = part.get("refusal") if part.get("type") == "refusal" else None
            if isinstance(value, str):
                texts.append(value)
            if isinstance(refusal, str):
                refusals.append(refusal)
    if texts:
        return "\n".join(texts), False
    if refusals:
        return "\n".join(refusals), True
    return None, False


def _finish_reason(payload: Mapping[str, object], status: object) -> str:
    if status != "incomplete":
        return "completed"
    details = payload.get("incomplete_details")
    reason = details.get("reason") if isinstance(details, Mapping) else None
    return reason if isinstance(reason, str) and reason else "incomplete"


def _usage(payload: Mapping[str, object]) -> Usage | None:
    raw = payload.get("usage")
    if not isinstance(raw, Mapping):
        return None
    units: dict[str, int | str] = {}
    _copy_integer(raw, "input_tokens", units)
    _copy_integer(raw, "output_tokens", units)
    _copy_integer(raw, "total_tokens", units)
    input_details = raw.get("input_tokens_details")
    output_details = raw.get("output_tokens_details")
    if isinstance(input_details, Mapping):
        _copy_integer(input_details, "cached_tokens", units)
    if isinstance(output_details, Mapping):
        _copy_integer(output_details, "reasoning_tokens", units)
    return Usage(units=units) if units else None


def _copy_integer(source: Mapping[str, object], key: str, target: dict[str, int | str]) -> None:
    value = source.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        target[key] = value
