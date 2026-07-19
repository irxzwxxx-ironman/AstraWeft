"""Safe declarative REST/JSON execution for user-configured gateway routes."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast
from urllib.parse import quote, urlencode

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
    Usage,
    validate_schema,
)

_OPERATIONS = frozenset(
    {"text.generate", "image.generate", "video.generate", "audio.generate", "custom.invoke"}
)
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})
_SECRET_FIELDS = frozenset({"api_key", "api_secret", "username", "password"})
_TEMPLATE = re.compile(r"\$\{([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\}")
_HEADER = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}")
_SENSITIVE_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "x-api-key", "api-key", "x-auth-token"}
)
_BLOCKED_HEADERS = frozenset(
    {"host", "content-length", "connection", "transfer-encoding", "proxy-connection"}
)
_MISSING = object()


class CustomRestProviderClient:
    """Execute multiple configured upstream routes through Core's restricted transport."""

    def __init__(
        self,
        context: ProviderContext,
        settings: Mapping[str, object],
        credential_ref: str | None,
    ) -> None:
        if context.endpoint is None:
            raise PluginConfigurationError("请先配置第三方 API 的 HTTPS 服务地址")
        self._context = context
        self._settings = dict(settings)
        self._credential_ref = credential_ref
        self._endpoint = context.endpoint.rstrip("/")
        self._definition = _mapping(self._settings.get("definition"), "definition")
        self._models = _validate_definition(self._definition)
        self._closed = False

    async def health_check(self) -> HealthCheckResult:
        self._ensure_open()
        health = self._definition.get("health")
        if health is None:
            return HealthCheckResult(
                status="degraded",
                latency_ms=None,
                message="API 定义已通过校验，但未配置 health 请求",
                details={"endpoint": _safe_endpoint_host(self._endpoint)},
            )
        request_spec = _mapping(health, "health")
        started = self._context.clock.monotonic()
        response = await self._execute(request_spec, {}, trace_id=None, idempotency_key=None)
        _raise_for_status(response, request_spec)
        latency = max(0, int((self._context.clock.monotonic() - started) * 1000))
        return HealthCheckResult(
            status="healthy",
            latency_ms=latency,
            message="自定义 API 连接正常",
            details={"endpoint": _safe_endpoint_host(self._endpoint)},
        )

    async def list_models(self) -> tuple[ProviderModel, ...]:
        self._ensure_open()
        result: list[ProviderModel] = []
        for model in self._models.values():
            operations = frozenset(_string_list(model.get("operations"), "model.operations"))
            result.append(
                ProviderModel(
                    remote_model_id=_string(model.get("id"), "model.id"),
                    display_name=_string(model.get("name"), "model.name"),
                    modality=_string(model.get("modality"), "model.modality").upper(),
                    operations=operations,
                    parameter_schema=_mapping(model.get("input_schema"), "model.input_schema"),
                    parameter_ui_schema=_optional_mapping(model.get("input_ui_schema")),
                    output_schema=_optional_mapping(model.get("output_schema"))
                    or {"type": "object", "additionalProperties": True},
                    capabilities={
                        "custom_rest": True,
                        "async": any(
                            _mapping(
                                _mapping(flow, "request flow").get("response"), "response"
                            ).get("mode")
                            == "async"
                            for flow in _mapping(model.get("requests"), "model.requests").values()
                        ),
                    },
                )
            )
        return tuple(result)

    async def submit(self, request: ProviderRequest) -> SubmissionResult:
        self._ensure_open()
        model, flow = self._flow(request.remote_model_id, request.operation)
        submit = _mapping(flow.get("submit"), "request.submit")
        template_context: dict[str, object] = {
            "input": request.inputs,
            "model_id": request.remote_model_id,
            "trace_id": request.trace_id,
            "idempotency_key": request.idempotency_key,
        }
        response = await self._execute(
            submit,
            template_context,
            trace_id=request.trace_id,
            idempotency_key=request.idempotency_key,
            timeout_seconds=min(request.timeout_seconds, self._request_timeout()),
        )
        _raise_for_status(response, submit)
        response_spec = _mapping(flow.get("response"), "request.response")
        payload = _response_payload(response, response_spec, submit)
        call = _call(response, submit)
        if response_spec.get("mode") == "sync":
            return SubmissionResult(
                mode="completed",
                output=_provider_output(payload, _mapping(response_spec.get("output"), "output")),
                progress=100,
                call=call,
            )
        task_id = _pointer(payload, _string(response_spec.get("task_id"), "response.task_id"))
        if isinstance(task_id, bool) or not isinstance(task_id, (str, int)):
            raise ProviderProtocolError("第三方 API 未返回有效任务 ID", call=call)
        remote_id = str(task_id)
        if not remote_id or len(remote_id) > 2048:
            raise ProviderProtocolError("第三方 API 任务 ID 无效", call=call)
        return SubmissionResult(
            mode="accepted",
            remote_task_id=_encode_task_identity(
                _string(model.get("id"), "model.id"), request.operation, remote_id
            ),
            progress=0,
            poll_after_seconds=_positive_number(response_spec.get("poll_after_seconds", 2), 2),
            call=call,
        )

    async def get_task(self, remote_task_id: str) -> RemoteTaskSnapshot:
        self._ensure_open()
        model_id, operation, upstream_id = _decode_task_identity(remote_task_id)
        _model, flow = self._flow(model_id, operation)
        response_spec = _mapping(flow.get("response"), "request.response")
        if response_spec.get("mode") != "async":
            raise ProviderProtocolError("该请求流不是异步任务")
        poll = _mapping(response_spec.get("poll"), "response.poll")
        context = {"model_id": model_id, "remote_task_id": upstream_id}
        response = await self._execute(poll, context, trace_id=None, idempotency_key=None)
        _raise_for_status(response, poll)
        payload = _response_payload(response, response_spec, poll)
        call = _call(response, poll)
        raw_state = _pointer(payload, _string(response_spec.get("state"), "response.state"))
        state = _normalized_state(raw_state, response_spec)
        progress = _optional_progress(payload, response_spec.get("progress"))
        poll_after = _positive_number(response_spec.get("poll_after_seconds", 2), 2)
        if state == "succeeded":
            return RemoteTaskSnapshot(
                state=state,
                progress=100,
                output=_provider_output(
                    payload, _mapping(response_spec.get("output"), "response.output")
                ),
                poll_after_seconds=None,
                call=call,
            )
        if state == "failed":
            return RemoteTaskSnapshot(
                state=state,
                progress=progress,
                error=RemoteError(
                    code="upstream_task_failed",
                    message="第三方 API 报告任务失败",
                    retryable=False,
                ),
                call=call,
            )
        return RemoteTaskSnapshot(
            state=state,
            progress=progress,
            poll_after_seconds=None if state == "canceled" else poll_after,
            call=call,
        )

    async def cancel_task(self, remote_task_id: str) -> CancelResult:
        self._ensure_open()
        model_id, operation, upstream_id = _decode_task_identity(remote_task_id)
        _model, flow = self._flow(model_id, operation)
        response_spec = _mapping(flow.get("response"), "request.response")
        cancel = response_spec.get("cancel")
        if cancel is None:
            raise UnsupportedOperationError("该自定义 API 没有配置取消接口")
        cancel_spec = _mapping(cancel, "response.cancel")
        response = await self._execute(
            cancel_spec,
            {"model_id": model_id, "remote_task_id": upstream_id},
            trace_id=None,
            idempotency_key=None,
        )
        _raise_for_status(response, cancel_spec)
        return CancelResult(
            accepted=True,
            terminal=bool(cancel_spec.get("terminal", False)),
            message="第三方 API 已接收取消请求",
            call=_call(response, cancel_spec),
        )

    async def close(self) -> None:
        self._closed = True

    def _flow(
        self, model_id: str, operation: str
    ) -> tuple[Mapping[str, object], Mapping[str, object]]:
        model = self._models.get(model_id)
        if model is None:
            raise ProviderValidationError("自定义 Provider 中不存在该模型")
        requests = _mapping(model.get("requests"), "model.requests")
        flow = requests.get(operation)
        if flow is None:
            raise UnsupportedOperationError("该模型未配置这个 operation")
        return model, _mapping(flow, "request flow")

    async def _execute(
        self,
        spec: Mapping[str, object],
        context: Mapping[str, object],
        *,
        trace_id: str | None,
        idempotency_key: str | None,
        timeout_seconds: float | None = None,
    ) -> HttpResponse:
        secrets = await self._secrets_for(spec)
        render_context = dict(context)
        render_context["secret"] = secrets
        path_template = _string(spec.get("path"), "request.path")
        path = _render_path(path_template, render_context)
        headers = _render_string_mapping(spec.get("headers", {}), render_context, "headers")
        _merge_auth_headers(headers, self._settings, secrets)
        raw_body = spec.get("body", _MISSING)
        body_value = _MISSING if raw_body is None else _render(raw_body, render_context)
        body: Mapping[str, object] | None
        if body_value is _MISSING:
            body = None
        elif isinstance(body_value, Mapping):
            body = cast(Mapping[str, object], body_value)
            if not _has_header(headers, "Content-Type"):
                headers["Content-Type"] = "application/json"
        else:
            raise PluginConfigurationError("request.body 必须是 JSON 对象")
        query = _render_mapping(spec.get("query", {}), render_context, "query")
        url = f"{self._endpoint}/{path.lstrip('/')}"
        encoded_query = _encoded_query(query)
        if encoded_query:
            url = f"{url}?{encoded_query}"
        return await self._context.http.request(
            _method(spec.get("method")),
            url,
            headers=headers,
            json_body=body,
            timeout_seconds=self._request_timeout() if timeout_seconds is None else timeout_seconds,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
        )

    async def _secrets_for(self, spec: Mapping[str, object]) -> dict[str, str]:
        fields = _referenced_secrets(spec)
        mode = self._settings.get("auth_mode", "bearer")
        if mode in {"bearer", "api_key_header"}:
            fields.add("api_key")
        elif mode == "basic":
            fields.update(("username", "password"))
        if not fields:
            return {}
        if self._credential_ref is None:
            raise ProviderAuthenticationError("该转发接口需要密钥，请先在 AstraWeft 中配置")
        result: dict[str, str] = {}
        try:
            for field in sorted(fields):
                result[field] = (
                    await self._context.secrets.get(self._credential_ref, field)
                ).reveal()
        except Exception as exc:
            raise ProviderAuthenticationError("该转发接口需要的密钥字段未配置完整") from exc
        return result

    def _request_timeout(self) -> float:
        value = self._settings.get("request_timeout_seconds", 120)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 900:
            raise PluginConfigurationError("request_timeout_seconds 必须在 1 到 900 之间")
        return float(value)

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProviderProtocolError("Custom REST Provider client is already closed")


def _validate_definition(
    definition: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    unknown = set(definition) - {"health", "models"}
    if unknown:
        raise PluginConfigurationError("API 定义包含未知顶层字段")
    if definition.get("health") is not None:
        _validate_request(_mapping(definition.get("health"), "health"), "health")
    models_raw = _sequence(definition.get("models"), "definition.models")
    if not 1 <= len(models_raw) <= 100:
        raise PluginConfigurationError("API 定义必须包含 1 到 100 个模型")
    models: dict[str, Mapping[str, object]] = {}
    for index, raw in enumerate(models_raw):
        model = _mapping(raw, f"models[{index}]")
        model_id = _string(model.get("id"), f"models[{index}].id")
        if len(model_id) > 200 or model_id in models:
            raise PluginConfigurationError("模型 ID 重复或过长")
        _string(model.get("name"), f"models[{index}].name")
        _string(model.get("modality"), f"models[{index}].modality")
        operations = frozenset(_string_list(model.get("operations"), f"models[{index}].operations"))
        if not operations or not operations <= _OPERATIONS:
            raise PluginConfigurationError("模型 operations 包含不支持的操作")
        input_schema = _mapping(model.get("input_schema"), "model.input_schema")
        try:
            validate_schema(input_schema)
            output_schema = _optional_mapping(model.get("output_schema"))
            if output_schema:
                validate_schema(output_schema)
        except Exception as exc:
            raise PluginConfigurationError("模型 JSON Schema 无效") from exc
        requests = _mapping(model.get("requests"), "model.requests")
        if set(requests) != operations:
            raise PluginConfigurationError("每个 model operation 都必须且只能有一个请求流")
        for operation, raw_flow in requests.items():
            flow = _mapping(raw_flow, f"requests.{operation}")
            if set(flow) != {"submit", "response"}:
                raise PluginConfigurationError("request flow 只允许 submit 和 response")
            _validate_request(_mapping(flow.get("submit"), "request.submit"), "submit")
            _validate_response(_mapping(flow.get("response"), "request.response"))
        models[model_id] = model
    return models


def _validate_request(spec: Mapping[str, object], label: str) -> None:
    allowed = {"method", "path", "headers", "query", "body", "terminal"}
    if set(spec) - allowed:
        raise PluginConfigurationError(f"{label} 包含未知字段")
    _method(spec.get("method"))
    path = _string(spec.get("path"), f"{label}.path")
    if (
        len(path) > 1024
        or not path.startswith("/")
        or any(mark in path for mark in ("://", "?", "#"))
    ):
        raise PluginConfigurationError(f"{label}.path 必须是安全的绝对路径")
    _validate_templates(spec)
    headers = _mapping(spec.get("headers", {}), f"{label}.headers")
    for name, value in headers.items():
        if not isinstance(name, str) or _HEADER.fullmatch(name) is None:
            raise PluginConfigurationError("请求头名无效")
        normalized = name.casefold()
        if normalized in _BLOCKED_HEADERS:
            raise PluginConfigurationError(f"不允许自定义请求头 {name}")
        if normalized in _SENSITIVE_HEADERS and (
            not isinstance(value, str) or "${secret." not in value
        ):
            raise PluginConfigurationError("敏感请求头必须使用 ${secret.<field>} 引用")
    _mapping(spec.get("query", {}), f"{label}.query")
    if spec.get("body") is not None:
        _mapping(spec.get("body"), f"{label}.body")


def _validate_response(spec: Mapping[str, object]) -> None:
    mode = spec.get("mode")
    if mode not in {"sync", "async"}:
        raise PluginConfigurationError("response.mode 必须是 sync 或 async")
    if spec.get("format", "json") not in {"json", "text"}:
        raise PluginConfigurationError("response.format 必须是 json 或 text")
    if mode == "sync":
        _validate_output(_mapping(spec.get("output"), "response.output"))
        return
    for field in ("task_id", "state"):
        _pointer_text(spec.get(field), f"response.{field}")
    _validate_request(_mapping(spec.get("poll"), "response.poll"), "response.poll")
    cancel = spec.get("cancel")
    if cancel is not None:
        _validate_request(_mapping(cancel, "response.cancel"), "response.cancel")
    states = _mapping(spec.get("states"), "response.states")
    if not {"queued", "running", "succeeded", "failed", "canceled"} <= set(states):
        raise PluginConfigurationError("response.states 必须定义五种标准状态")
    for values in states.values():
        _string_list(values, "response.states value")
    if spec.get("progress") is not None:
        _pointer_text(spec.get("progress"), "response.progress")
    _validate_output(_mapping(spec.get("output"), "response.output"))


def _validate_output(spec: Mapping[str, object]) -> None:
    allowed = {"data", "data_pointer", "artifacts", "usage", "finish_reason"}
    if set(spec) - allowed:
        raise PluginConfigurationError("output 包含未知字段")
    data = _mapping(spec.get("data", {}), "output.data")
    for pointer in data.values():
        _pointer_text(pointer, "output.data pointer")
    if spec.get("data_pointer") is not None:
        _pointer_text(spec.get("data_pointer"), "output.data_pointer")
    artifacts = _sequence(spec.get("artifacts", ()), "output.artifacts")
    for raw in artifacts:
        artifact = _mapping(raw, "output artifact")
        if artifact.get("kind") not in {"image", "video", "audio", "text", "json"}:
            raise PluginConfigurationError("artifact.kind 无效")
        if artifact.get("source") not in {"url", "base64", "text", "json"}:
            raise PluginConfigurationError("artifact.source 无效")
        _pointer_text(artifact.get("pointer"), "artifact.pointer")


def _provider_output(payload: object, spec: Mapping[str, object]) -> ProviderOutput:
    data: dict[str, object] = {}
    data_pointer = spec.get("data_pointer")
    if data_pointer is not None:
        value = _pointer(payload, _pointer_text(data_pointer, "output.data_pointer"))
        if not isinstance(value, Mapping):
            raise ProviderProtocolError("响应 data_pointer 没有指向 JSON 对象")
        data.update({str(key): child for key, child in value.items()})
    for name, pointer in _mapping(spec.get("data", {}), "output.data").items():
        data[name] = _pointer(payload, _pointer_text(pointer, f"output.data.{name}"))
    artifacts: list[RemoteArtifact] = []
    for raw in _sequence(spec.get("artifacts", ()), "output.artifacts"):
        artifact = _mapping(raw, "output artifact")
        pointer = _pointer_text(artifact.get("pointer"), "artifact.pointer")
        value = _pointer(payload, pointer, missing=_MISSING)
        if value is _MISSING and bool(artifact.get("optional", False)):
            continue
        source = _string(artifact.get("source"), "artifact.source")
        if source == "json":
            if not isinstance(value, Mapping):
                raise ProviderProtocolError("映射的 JSON 产物不是对象")
            artifact_value: str | Mapping[str, object] = cast(Mapping[str, object], value)
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            artifact_value = str(value)
        else:
            raise ProviderProtocolError("映射的产物值无效")
        artifacts.append(
            RemoteArtifact(
                kind=cast(Any, _string(artifact.get("kind"), "artifact.kind")),
                source=cast(Any, source),
                value=artifact_value,
                mime_type=_optional_string(artifact.get("mime_type")),
                filename_hint=_optional_string(artifact.get("filename")),
            )
        )
    finish_reason = None
    if spec.get("finish_reason") is not None:
        finish = _pointer(payload, _pointer_text(spec.get("finish_reason"), "finish_reason"))
        finish_reason = str(finish) if finish is not None else None
    return ProviderOutput(
        data=data,
        artifacts=tuple(artifacts),
        usage=_usage(payload, spec.get("usage")),
        finish_reason=finish_reason,
    )


def _usage(payload: object, raw: object) -> Usage | None:
    if raw is None:
        return None
    spec = _mapping(raw, "output.usage")
    units: dict[str, int | str] = {}
    for name, pointer in _mapping(spec.get("units", {}), "usage.units").items():
        value = _pointer(payload, _pointer_text(pointer, "usage unit pointer"))
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ProviderProtocolError("响应中的 usage 数值无效")
        units[name] = value
    cost = None
    currency = None
    if spec.get("cost_micros") is not None:
        raw_cost = _pointer(payload, _pointer_text(spec.get("cost_micros"), "cost pointer"))
        if isinstance(raw_cost, bool) or not isinstance(raw_cost, int) or raw_cost < 0:
            raise ProviderProtocolError("响应中的 cost_micros 无效")
        cost = raw_cost
        currency = _string(spec.get("currency"), "usage.currency").upper()
    return Usage(units=units, cost_micros=cost, currency=currency, pricing_source="provider")


def _response_payload(
    response: HttpResponse,
    response_spec: Mapping[str, object],
    request_spec: Mapping[str, object],
) -> object:
    if response_spec.get("format", "json") == "text":
        try:
            return {"text": response.body.decode("utf-8")}
        except UnicodeDecodeError as exc:
            raise ProviderProtocolError(
                "第三方 API 返回的文本编码无效", call=_call(response, request_spec)
            ) from exc
    try:
        return json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderProtocolError(
            "第三方 API 返回了无效 JSON", call=_call(response, request_spec)
        ) from exc


def _normalized_state(
    value: object, spec: Mapping[str, object]
) -> Literal["queued", "running", "succeeded", "failed", "canceled"]:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ProviderProtocolError("第三方任务状态无效")
    candidate = str(value).casefold()
    states = _mapping(spec.get("states"), "response.states")
    for normalized in ("queued", "running", "succeeded", "failed", "canceled"):
        aliases = _string_list(states.get(normalized), f"response.states.{normalized}")
        if candidate in {alias.casefold() for alias in aliases}:
            return normalized
    raise ProviderProtocolError("第三方 API 返回了未映射的任务状态")


def _optional_progress(payload: object, raw_pointer: object) -> int | None:
    if raw_pointer is None:
        return None
    value = _pointer(payload, _pointer_text(raw_pointer, "response.progress"))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderProtocolError("第三方 API 返回的进度无效")
    return max(0, min(100, int(value)))


def _render(value: object, context: Mapping[str, object]) -> object:
    if isinstance(value, str):
        exact = _TEMPLATE.fullmatch(value)
        if exact:
            return _lookup(context, exact.group(1), missing=_MISSING)
        rendered = value
        for match in _TEMPLATE.finditer(value):
            resolved = _lookup(context, match.group(1), missing=_MISSING)
            if resolved is _MISSING:
                raise ProviderValidationError(f"请求缺少模板参数 {match.group(1)}")
            if isinstance(resolved, (Mapping, Sequence)) and not isinstance(resolved, str):
                raise ProviderValidationError("嵌入字符串的模板参数必须是标量")
            rendered = rendered.replace(match.group(0), str(resolved))
        return rendered
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, child in value.items():
            child_value = _render(child, context)
            if child_value is not _MISSING:
                result[str(key)] = child_value
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        children = (_render(child, context) for child in value)
        return [child for child in children if child is not _MISSING]
    return value


def _render_path(template: str, context: Mapping[str, object]) -> str:
    def replace(match: re.Match[str]) -> str:
        value = _lookup(context, match.group(1), missing=_MISSING)
        if value is _MISSING or (
            isinstance(value, (Mapping, Sequence)) and not isinstance(value, str)
        ):
            raise ProviderValidationError(f"路径缺少标量参数 {match.group(1)}")
        return quote(str(value), safe="")

    rendered = _TEMPLATE.sub(replace, template)
    if not rendered.startswith("/") or any(mark in rendered for mark in ("://", "?", "#")):
        raise ProviderValidationError("模板渲染后的请求路径无效")
    return rendered


def _lookup(context: Mapping[str, object], path: str, *, missing: object) -> object:
    current: object = context
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return missing
        current = current[part]
    return current


def _pointer(payload: object, pointer: str, *, missing: object | None = None) -> object:
    if pointer == "":
        return payload
    current = payload
    for raw in pointer.removeprefix("/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                if missing is not None:
                    return missing
                raise ProviderProtocolError(f"响应中找不到字段 {pointer}") from None
        else:
            if missing is not None:
                return missing
            raise ProviderProtocolError(f"响应中找不到字段 {pointer}")
    return current


def _render_string_mapping(
    raw: object, context: Mapping[str, object], label: str
) -> dict[str, str]:
    rendered = _render_mapping(raw, context, label)
    result: dict[str, str] = {}
    for key, value in rendered.items():
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ProviderValidationError(f"{label}.{key} 渲染后必须是标量")
        text = str(value)
        if "\r" in text or "\n" in text:
            raise ProviderValidationError("请求头值不能包含换行符")
        result[key] = text
    return result


def _render_mapping(raw: object, context: Mapping[str, object], label: str) -> dict[str, object]:
    value = _render(_mapping(raw, label), context)
    if not isinstance(value, Mapping):  # pragma: no cover - renderer preserves mappings
        raise PluginConfigurationError(f"{label} 必须是对象")
    return {str(key): child for key, child in value.items()}


def _merge_auth_headers(
    headers: dict[str, str], settings: Mapping[str, object], secrets: Mapping[str, str]
) -> None:
    mode = settings.get("auth_mode", "bearer")
    if mode in {"none", "custom_templates"}:
        return
    if mode == "bearer":
        _set_unique_header(headers, "Authorization", f"Bearer {secrets['api_key']}")
        return
    if mode == "api_key_header":
        name = _string(settings.get("auth_header_name", "X-API-Key"), "auth_header_name")
        if _HEADER.fullmatch(name) is None or name.casefold() in _BLOCKED_HEADERS:
            raise PluginConfigurationError("auth_header_name 无效")
        prefix = settings.get("auth_prefix", "")
        if not isinstance(prefix, str) or "\r" in prefix or "\n" in prefix:
            raise PluginConfigurationError("auth_prefix 无效")
        value = f"{prefix} {secrets['api_key']}".strip()
        _set_unique_header(headers, name, value)
        return
    if mode == "basic":
        token = base64.b64encode(f"{secrets['username']}:{secrets['password']}".encode()).decode(
            "ascii"
        )
        _set_unique_header(headers, "Authorization", f"Basic {token}")
        return
    raise PluginConfigurationError("auth_mode 无效")


def _set_unique_header(headers: dict[str, str], name: str, value: str) -> None:
    if _has_header(headers, name):
        raise PluginConfigurationError(f"鉴权请求头 {name} 被重复配置")
    headers[name] = value


def _has_header(headers: Mapping[str, str], name: str) -> bool:
    expected = name.casefold()
    return any(candidate.casefold() == expected for candidate in headers)


def _encoded_query(query: Mapping[str, object]) -> str:
    pairs: list[tuple[str, str]] = []
    for name, value in query.items():
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            pairs.extend((name, _scalar_text(child, f"query.{name}")) for child in value)
        else:
            pairs.append((name, _scalar_text(value, f"query.{name}")))
    return urlencode(pairs)


def _scalar_text(value: object, label: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise ProviderValidationError(f"{label} 必须是标量或标量数组")


def _referenced_secrets(value: object) -> set[str]:
    result: set[str] = set()
    if isinstance(value, str):
        for match in _TEMPLATE.finditer(value):
            path = match.group(1).split(".")
            if path[0] == "secret":
                if len(path) != 2 or path[1] not in _SECRET_FIELDS:
                    raise PluginConfigurationError("只允许引用已声明的 secret 字段")
                result.add(path[1])
    elif isinstance(value, Mapping):
        for child in value.values():
            result.update(_referenced_secrets(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            result.update(_referenced_secrets(child))
    return result


def _validate_templates(value: object) -> None:
    allowed_roots = {"input", "model_id", "remote_task_id", "trace_id", "idempotency_key", "secret"}
    if isinstance(value, str):
        for match in _TEMPLATE.finditer(value):
            path = match.group(1).split(".")
            if path[0] not in allowed_roots or (
                path[0] == "secret" and path[-1] not in _SECRET_FIELDS
            ):
                raise PluginConfigurationError(f"不支持的模板变量 {match.group(1)}")
    elif isinstance(value, Mapping):
        for child in value.values():
            _validate_templates(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _validate_templates(child)


def _raise_for_status(response: HttpResponse, spec: Mapping[str, object]) -> None:
    if 200 <= response.status < 300:
        return
    call = _call(response, spec)
    details = {"http_status": response.status}
    error_type: type[ProviderError]
    message: str
    if response.status == 400 or response.status == 422:
        error_type, message = ProviderValidationError, "第三方 API 拒绝了请求参数"
    elif response.status == 401:
        error_type, message = ProviderAuthenticationError, "第三方 API Key 无效或已失效"
    elif response.status == 403:
        error_type, message = ProviderPermissionError, "第三方 API 拒绝了访问"
    elif response.status == 408:
        error_type, message = ProviderTimeoutError, "第三方 API 请求超时"
    elif response.status == 429:
        error_type, message = ProviderRateLimitError, "第三方 API 请求过多或配额不足"
    elif response.status >= 500:
        error_type, message = ProviderUnavailableError, "第三方 API 暂时不可用"
    else:
        error_type, message = ProviderError, "第三方 API 返回了错误"
    raise error_type(message, call=call, safe_details=details)


def _call(response: HttpResponse, spec: Mapping[str, object]) -> ProviderCallInfo:
    request_id = None
    for name in ("x-request-id", "request-id", "x-correlation-id"):
        value = response.headers.get(name)
        if value and len(value) <= 512 and value.isascii():
            request_id = value
            break
    return ProviderCallInfo(
        method=_method(spec.get("method")),
        url_template=_string(spec.get("path"), "request.path"),
        http_status=response.status,
        provider_request_id=request_id,
    )


def _encode_task_identity(model_id: str, operation: str, upstream_id: str) -> str:
    payload = json.dumps([model_id, operation, upstream_id], separators=(",", ":")).encode()
    return "cr1." + base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_task_identity(value: str) -> tuple[str, str, str]:
    if not value.startswith("cr1.") or len(value) > 8192:
        raise ProviderProtocolError("自定义 API 任务标识无效")
    encoded = value[4:]
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        decoded = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderProtocolError("自定义 API 任务标识无效") from exc
    if (
        not isinstance(decoded, list)
        or len(decoded) != 3
        or not all(isinstance(item, str) and item for item in decoded)
    ):
        raise ProviderProtocolError("自定义 API 任务标识无效")
    return decoded[0], decoded[1], decoded[2]


def _safe_endpoint_host(endpoint: str) -> str:
    return endpoint.split("/", 3)[2]


def _method(value: object) -> str:
    method = _string(value, "request.method").upper()
    if method not in _METHODS:
        raise PluginConfigurationError("请求 method 不支持")
    return method


def _pointer_text(value: object, label: str) -> str:
    pointer = _string(value, label, allow_empty=True)
    if pointer and not pointer.startswith("/"):
        raise PluginConfigurationError(f"{label} 必须是 RFC 6901 JSON Pointer")
    return pointer


def _positive_number(value: object, fallback: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.1 <= value <= 3600:
        return fallback
    return float(value)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise PluginConfigurationError(f"{label} 必须是 JSON 对象")
    return cast(Mapping[str, object], value)


def _optional_mapping(value: object) -> Mapping[str, object]:
    return {} if value is None else _mapping(value, "mapping")


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise PluginConfigurationError(f"{label} 必须是 JSON 数组")
    return cast(Sequence[object], value)


def _string_list(value: object, label: str) -> list[str]:
    sequence = _sequence(value, label)
    if not all(isinstance(item, str) and item for item in sequence):
        raise PluginConfigurationError(f"{label} 必须是非空字符串数组")
    return cast(list[str], list(sequence))


def _string(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise PluginConfigurationError(f"{label} 必须是非空字符串")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise PluginConfigurationError("可选字段必须是非空字符串")
    return value
