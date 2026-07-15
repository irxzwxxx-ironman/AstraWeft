"""Strict loopback-only HTTP gateway backed by existing application services."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import cast

from aiohttp import web

from astraweft.application.providers import ProviderService
from astraweft.application.tasks import CreateTask, TaskNotFoundError, TaskService
from astraweft.domain.task import Artifact, Task
from astraweft.ports.secrets import SecretNotFoundError, SecretStore, SecretValue

_CREDENTIAL_REF = "loopback_gateway"
_CREDENTIAL_FIELD = "access_token"
_MAX_BODY_BYTES = 256 * 1024
_DEFAULT_RATE_LIMIT = 120

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class LoopbackGateway:
    """Expose a narrow task API on 127.0.0.1 with bearer authentication."""

    def __init__(
        self,
        *,
        tasks: TaskService,
        providers: ProviderService,
        secrets_store: SecretStore,
        artifact_root: Path,
        host: str = "127.0.0.1",
        port: int = 17493,
        rate_limit_per_minute: int = _DEFAULT_RATE_LIMIT,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError("loopback gateway must bind to 127.0.0.1")
        if not 0 <= port <= 65535 or rate_limit_per_minute < 1:
            raise ValueError("loopback gateway settings are invalid")
        self._tasks = tasks
        self._providers = providers
        self._secrets = secrets_store
        self._artifact_root = artifact_root.resolve()
        self._host = host
        self._port = port
        self._rate_limit = rate_limit_per_minute
        self._requests: deque[float] = deque()
        self._rate_lock = asyncio.Lock()
        self._token: SecretValue | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._bound_port: int | None = None

    @property
    def running(self) -> bool:
        return self._runner is not None

    @property
    def bound_port(self) -> int | None:
        return self._bound_port

    async def start(self) -> None:
        if self.running:
            return
        self._token = await self._load_or_create_token()
        app = web.Application(
            middlewares=[self._security_middleware],
            client_max_size=_MAX_BODY_BYTES,
        )
        app.add_routes(
            [
                web.get("/api/v1/health", self._health),
                web.get("/api/v1/catalog", self._catalog),
                web.post("/api/v1/tasks", self._create_task),
                web.get("/api/v1/tasks/{task_id}", self._get_task),
                web.post("/api/v1/tasks/{task_id}/cancel", self._cancel_task),
                web.get("/api/v1/tasks/{task_id}/artifacts", self._task_artifacts),
                web.get("/api/v1/artifacts/{artifact_id}", self._download_artifact),
            ]
        )
        runner = web.AppRunner(app, access_log=None)
        try:
            await runner.setup()
            site = web.TCPSite(runner, self._host, self._port)
            await site.start()
        except Exception:
            await runner.cleanup()
            raise
        self._runner = runner
        self._site = site
        addresses = runner.addresses
        self._bound_port = int(addresses[0][1]) if addresses else self._port

    async def stop(self) -> None:
        runner = self._runner
        self._runner = None
        self._site = None
        self._bound_port = None
        self._requests.clear()
        if runner is not None:
            await runner.cleanup()

    @web.middleware
    async def _security_middleware(
        self,
        request: web.Request,
        handler: Handler,
    ) -> web.StreamResponse:
        if request.method == "OPTIONS":
            return _error("cors_not_supported", "浏览器跨域请求未开放", status=403)
        host = request.host.lower()
        allowed_hosts = {
            f"127.0.0.1:{self._bound_port or self._port}",
            f"localhost:{self._bound_port or self._port}",
        }
        if host not in allowed_hosts:
            return _error("invalid_host", "请求主机无效", status=400)
        origin = request.headers.get("Origin")
        if origin is not None and origin.lower() not in {
            f"http://127.0.0.1:{self._bound_port or self._port}",
            f"http://localhost:{self._bound_port or self._port}",
        }:
            return _error("origin_rejected", "请求来源被拒绝", status=403)
        content_length = request.content_length
        if content_length is not None and content_length > _MAX_BODY_BYTES:
            return _error("body_too_large", "请求正文过大", status=413)
        token = self._token
        header = request.headers.get("Authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else ""
        if token is None or not secrets.compare_digest(supplied, token.reveal()):
            return _error("unauthorized", "本机网关认证失败", status=401)
        if not await self._take_rate_slot():
            return _error("rate_limited", "本机网关请求过于频繁", status=429)
        try:
            response = await handler(request)
        except TaskNotFoundError:
            return _error("task_not_found", "任务不存在", status=404)
        except (ValueError, TypeError) as exc:
            return _error("invalid_request", str(exc), status=400)
        except Exception:
            return _error("internal_error", "本机网关操作失败", status=500)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def _take_rate_slot(self) -> bool:
        async with self._rate_lock:
            now = time.monotonic()
            cutoff = now - 60
            while self._requests and self._requests[0] <= cutoff:
                self._requests.popleft()
            if len(self._requests) >= self._rate_limit:
                return False
            self._requests.append(now)
            return True

    async def _load_or_create_token(self) -> SecretValue:
        try:
            return await self._secrets.get(_CREDENTIAL_REF, _CREDENTIAL_FIELD)
        except SecretNotFoundError:
            value = SecretValue(secrets.token_urlsafe(32))
            await self._secrets.set(_CREDENTIAL_REF, _CREDENTIAL_FIELD, value)
            return value

    async def _health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "api": "astraweft.loopback/v1"})

    async def _catalog(self, _request: web.Request) -> web.Response:
        providers = await self._providers.list_providers()
        models = await self._providers.list_models()
        return web.json_response(
            {
                "providers": [
                    {
                        "id": provider.id,
                        "name": provider.name,
                        "enabled": provider.enabled,
                        "health": provider.health_status.value,
                    }
                    for provider in providers
                ],
                "models": [
                    {
                        "id": model.id,
                        "provider_id": model.provider_id,
                        "name": model.display_name,
                        "modality": model.modality,
                        "operations": sorted(model.operations),
                        "input_schema": _plain_json(model.parameter_schema),
                        "output_schema": _plain_json(model.output_schema),
                        "enabled": model.enabled and model.available and not model.deprecated,
                    }
                    for model in models
                ],
            }
        )

    async def _create_task(self, request: web.Request) -> web.Response:
        payload = _mapping(await request.json(), "请求正文")
        unknown = set(payload) - {
            "provider_id",
            "model_id",
            "operation",
            "inputs",
            "priority",
            "timeout_seconds",
        }
        if unknown:
            raise ValueError("请求包含未知字段")
        provider_id = _required_text(payload.get("provider_id"), "provider_id")
        model_id = _required_text(payload.get("model_id"), "model_id")
        operation = _required_text(payload.get("operation"), "operation")
        inputs = _mapping(payload.get("inputs", {}), "inputs")
        priority = _integer(payload.get("priority", 100), "priority")
        timeout = _number(payload.get("timeout_seconds", 300), "timeout_seconds")
        task = await self._tasks.create(
            CreateTask(
                provider_id,
                model_id,
                operation,
                inputs,
                priority=priority,
                timeout_seconds=timeout,
            )
        )
        return web.json_response(_task_payload(task), status=202)

    async def _get_task(self, request: web.Request) -> web.Response:
        task = await self._tasks.get(request.match_info["task_id"])
        return web.json_response(_task_payload(task))

    async def _cancel_task(self, request: web.Request) -> web.Response:
        task = await self._tasks.cancel(request.match_info["task_id"])
        return web.json_response(_task_payload(task))

    async def _task_artifacts(self, request: web.Request) -> web.Response:
        task_id = request.match_info["task_id"]
        await self._tasks.get(task_id)
        artifacts = await self._tasks.list_artifacts(task_id)
        return web.json_response(
            {"artifacts": [_artifact_payload(artifact) for artifact in artifacts]}
        )

    async def _download_artifact(self, request: web.Request) -> web.StreamResponse:
        artifact_id = request.match_info["artifact_id"]
        artifacts = await self._tasks.list_artifacts(limit=10_000)
        artifact = next((item for item in artifacts if item.id == artifact_id), None)
        if artifact is None:
            return _error("artifact_not_found", "产物不存在", status=404)
        target = (self._artifact_root / artifact.relative_path).resolve()
        if not target.is_relative_to(self._artifact_root) or not target.is_file():
            return _error("artifact_unavailable", "产物文件不可用", status=404)
        return web.FileResponse(
            target,
            headers={
                "Content-Type": artifact.mime_type,
                "Content-Disposition": f'attachment; filename="{artifact.id}{target.suffix}"',
            },
        )


def _task_payload(task: Task) -> dict[str, object]:
    return {
        "id": task.id,
        "status": task.status.value,
        "progress": task.progress,
        "operation": task.operation,
        "error": None
        if task.status.value not in {"FAILED", "NEEDS_ATTENTION"}
        else {"code": "task_failed", "message": "任务未成功完成"},
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def _artifact_payload(artifact: Artifact) -> dict[str, object]:
    return {
        "id": artifact.id,
        "kind": artifact.kind,
        "mime_type": artifact.mime_type,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "download_url": f"/api/v1/artifacts/{artifact.id}",
    }


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label}必须是对象")
    return cast(Mapping[str, object], value)


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f"{label}无效")
    return value.strip()


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}必须是整数")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}必须是数字")
    return float(value)


def _error(code: str, message: str, *, status: int) -> web.Response:
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(child) for child in value]
    return value
