"""Bounded ComfyUI HTTP client with best-effort WebSocket progress."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import cast
from urllib.parse import urlencode, urlsplit, urlunsplit

import aiohttp
import anyio

from astraweft.domain.comfyui import ComfyUIExecutionStatus, ComfyUIInstance
from astraweft.ports.artifacts import ArtifactDownloadResult
from astraweft.ports.comfyui import (
    ComfyUIOutputFile,
    ComfyUIProbe,
    ComfyUIRemoteSnapshot,
    ComfyUISubmitResult,
)

_DEFAULT_JSON_LIMIT = 16 * 1024 * 1024
_CATALOG_JSON_LIMIT = 64 * 1024 * 1024


class ComfyUITransportError(RuntimeError):
    """A bounded ComfyUI request failed without exposing response contents."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "comfyui_transport_error",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class AioHttpComfyUIClient:
    """One pooled client; polling remains authoritative when WebSocket drops."""

    def __init__(
        self,
        *,
        user_agent: str,
        session: aiohttp.ClientSession | None = None,
        request_timeout_seconds: float = 20,
    ) -> None:
        if not user_agent.strip() or request_timeout_seconds <= 0:
            raise ValueError("ComfyUI client settings are invalid")
        self._user_agent = user_agent
        self._session = session
        self._owns_session = session is None
        self._request_timeout_seconds = request_timeout_seconds
        self._progress: dict[str, int] = {}
        self._watches: dict[str, asyncio.Task[None]] = {}
        self._closed = False

    async def probe(self, instance: ComfyUIInstance) -> ComfyUIProbe:
        system = _mapping(
            await self._request_json(instance, "GET", "/system_stats"),
            "ComfyUI system stats",
        )
        features_raw = await self._request_json(instance, "GET", "/features")
        catalog = _mapping(
            await self._request_json(
                instance,
                "GET",
                "/object_info",
                max_bytes=_CATALOG_JSON_LIMIT,
            ),
            "ComfyUI node catalog",
        )
        catalog_hash = hashlib.sha256(
            json.dumps(
                catalog,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        system_info = system.get("system")
        details = system_info if isinstance(system_info, Mapping) else system
        version = _optional_string(details.get("comfyui_version"))
        python_version = _optional_string(details.get("python_version"))
        capabilities: dict[str, object] = {
            "node_count": len(catalog),
            "features": features_raw if isinstance(features_raw, Mapping) else {},
        }
        devices = system.get("devices")
        if isinstance(devices, Sequence) and not isinstance(devices, (str, bytes, bytearray)):
            capabilities["device_count"] = len(devices)
        return ComfyUIProbe(
            version=version,
            python_version=python_version,
            capabilities=capabilities,
            node_catalog_hash=catalog_hash,
        )

    async def submit(
        self,
        instance: ComfyUIInstance,
        *,
        prompt: Mapping[str, object],
        client_id: str,
        execution_id: str,
        workflow_checksum: str,
    ) -> ComfyUISubmitResult:
        payload = {
            "prompt": prompt,
            "client_id": client_id,
            "extra_data": {
                "astraweft_execution_id": execution_id,
                "astraweft_workflow_checksum": workflow_checksum,
            },
        }
        response = _mapping(
            await self._request_json(instance, "POST", "/prompt", json_body=payload),
            "ComfyUI prompt response",
        )
        prompt_id = response.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ComfyUITransportError(
                "ComfyUI 未返回任务编号",
                code="invalid_prompt_response",
                retryable=False,
            )
        number = response.get("number")
        return ComfyUISubmitResult(
            prompt_id=prompt_id,
            queue_number=number if isinstance(number, int) else None,
        )

    async def find_execution(
        self,
        instance: ComfyUIInstance,
        execution_id: str,
    ) -> str | None:
        queue = _mapping(
            await self._request_json(instance, "GET", "/queue"),
            "ComfyUI queue",
        )
        found = _find_prompt_by_execution(queue, execution_id)
        if found is not None:
            return found
        history = _mapping(
            await self._request_json(instance, "GET", "/history?max_items=200"),
            "ComfyUI history",
        )
        return _find_prompt_by_execution(history, execution_id)

    async def snapshot(
        self,
        instance: ComfyUIInstance,
        prompt_id: str,
    ) -> ComfyUIRemoteSnapshot:
        history = _mapping(
            await self._request_json(instance, "GET", f"/history/{prompt_id}"),
            "ComfyUI history",
        )
        raw_record = history.get(prompt_id)
        if isinstance(raw_record, Mapping):
            return _history_snapshot(prompt_id, raw_record, self.latest_progress(prompt_id))
        queue = _mapping(
            await self._request_json(instance, "GET", "/queue"),
            "ComfyUI queue",
        )
        queue_status = _queue_status(queue, prompt_id)
        if queue_status is not None:
            return ComfyUIRemoteSnapshot(
                status=queue_status,
                progress=self.latest_progress(prompt_id),
                outputs={},
                files=(),
            )
        return ComfyUIRemoteSnapshot(
            status=ComfyUIExecutionStatus.NEEDS_ATTENTION,
            progress=self.latest_progress(prompt_id),
            outputs={},
            files=(),
            error_code="remote_prompt_missing",
            error_message="ComfyUI 队列与历史记录中均未找到该任务",
        )

    async def ensure_progress_watch(
        self,
        instance: ComfyUIInstance,
        *,
        prompt_id: str,
        client_id: str,
    ) -> None:
        if self._closed:
            return
        current = self._watches.get(prompt_id)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(
            self._watch_progress(instance, prompt_id=prompt_id, client_id=client_id),
            name=f"comfyui-progress-{prompt_id}",
        )
        self._watches[prompt_id] = task
        task.add_done_callback(lambda completed: self._watch_finished(prompt_id, completed))

    def latest_progress(self, prompt_id: str) -> int | None:
        return self._progress.get(prompt_id)

    async def cancel(self, instance: ComfyUIInstance, prompt_id: str) -> bool:
        queue = _mapping(
            await self._request_json(instance, "GET", "/queue"),
            "ComfyUI queue",
        )
        status = _queue_status(queue, prompt_id)
        if status is ComfyUIExecutionStatus.QUEUED:
            await self._request_json(
                instance,
                "POST",
                "/queue",
                json_body={"delete": [prompt_id]},
            )
            return True
        if status is ComfyUIExecutionStatus.RUNNING:
            await self._request_json(instance, "POST", "/interrupt", json_body={})
            return True
        history = _mapping(
            await self._request_json(instance, "GET", f"/history/{prompt_id}"),
            "ComfyUI history",
        )
        return prompt_id in history

    async def download_output(
        self,
        instance: ComfyUIInstance,
        output: ComfyUIOutputFile,
        *,
        target: Path,
        max_bytes: int,
        timeout_seconds: float,
    ) -> ArtifactDownloadResult:
        if max_bytes < 1 or timeout_seconds <= 0:
            raise ValueError("ComfyUI output download limits must be positive")
        query = urlencode(
            {
                "filename": output.filename,
                "subfolder": output.subfolder,
                "type": output.folder_type,
            }
        )
        url = _endpoint(instance.base_url, f"/view?{query}")
        digest = hashlib.sha256()
        size = 0
        session = await self._session_or_create()
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with session.get(url, timeout=timeout, allow_redirects=False) as response:
                if not 200 <= response.status < 300:
                    raise ComfyUITransportError(
                        f"ComfyUI 成果下载返回 HTTP {response.status}",
                        code="output_download_failed",
                    )
                declared_size = _content_length(response.headers.get("Content-Length"))
                if declared_size is not None and declared_size > max_bytes:
                    raise ComfyUITransportError(
                        "ComfyUI 成果超过安全大小限制",
                        code="output_too_large",
                        retryable=False,
                    )
                async with await anyio.open_file(target, "wb") as stream:
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        size += len(chunk)
                        if size > max_bytes:
                            raise ComfyUITransportError(
                                "ComfyUI 成果超过安全大小限制",
                                code="output_too_large",
                                retryable=False,
                            )
                        digest.update(chunk)
                        await stream.write(chunk)
                content_type = _content_type(response.headers.get("Content-Type"))
        except ComfyUITransportError:
            raise
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ComfyUITransportError(
                "ComfyUI 成果下载失败",
                code="output_download_failed",
            ) from exc
        if size == 0:
            raise ComfyUITransportError(
                "ComfyUI 返回了空成果",
                code="empty_output",
                retryable=False,
            )
        return ArtifactDownloadResult(
            size_bytes=size,
            sha256=digest.hexdigest(),
            content_type=content_type,
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        watches = tuple(self._watches.values())
        for task in watches:
            task.cancel()
        if watches:
            await asyncio.gather(*watches, return_exceptions=True)
        self._watches.clear()
        if self._session is not None and self._owns_session:
            await self._session.close()

    async def _request_json(
        self,
        instance: ComfyUIInstance,
        method: str,
        route: str,
        *,
        json_body: Mapping[str, object] | None = None,
        max_bytes: int = _DEFAULT_JSON_LIMIT,
    ) -> object:
        session = await self._session_or_create()
        url = _endpoint(instance.base_url, route)
        try:
            timeout = aiohttp.ClientTimeout(total=self._request_timeout_seconds)
            async with session.request(
                method,
                url,
                json=None if json_body is None else dict(json_body),
                timeout=timeout,
                allow_redirects=False,
            ) as response:
                if not 200 <= response.status < 300:
                    retryable = response.status >= 500 or response.status in {408, 429}
                    raise ComfyUITransportError(
                        f"ComfyUI 返回 HTTP {response.status}",
                        code="http_error",
                        retryable=retryable,
                    )
                body = await _read_bounded(response, max_bytes=max_bytes)
        except ComfyUITransportError:
            raise
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ComfyUITransportError("无法连接 ComfyUI", code="network_error") from exc
        if not body:
            return {}
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ComfyUITransportError(
                "ComfyUI 返回了无效数据",
                code="invalid_json",
                retryable=False,
            ) from exc

    async def _session_or_create(self) -> aiohttp.ClientSession:
        if self._closed:
            raise ComfyUITransportError(
                "ComfyUI 网络连接已关闭",
                code="client_closed",
                retryable=False,
            )
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": self._user_agent},
                connector=aiohttp.TCPConnector(limit=20, limit_per_host=8),
                cookie_jar=aiohttp.DummyCookieJar(),
                timeout=aiohttp.ClientTimeout(total=self._request_timeout_seconds),
            )
        return self._session

    async def _watch_progress(
        self,
        instance: ComfyUIInstance,
        *,
        prompt_id: str,
        client_id: str,
    ) -> None:
        session = await self._session_or_create()
        url = _websocket_endpoint(instance.base_url, client_id)
        try:
            async with session.ws_connect(
                url,
                timeout=aiohttp.ClientWSTimeout(
                    ws_close=self._request_timeout_seconds,
                ),
                heartbeat=30,
                max_msg_size=2 * 1024 * 1024,
            ) as websocket:
                async for message in websocket:
                    if message.type is not aiohttp.WSMsgType.TEXT:
                        if message.type in {
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        }:
                            return
                        continue
                    payload = _decode_ws_message(message.data)
                    if payload is None:
                        continue
                    data = payload.get("data")
                    if not isinstance(data, Mapping) or data.get("prompt_id") != prompt_id:
                        continue
                    message_type = payload.get("type")
                    if message_type == "progress":
                        value = data.get("value")
                        maximum = data.get("max")
                        if isinstance(value, int) and isinstance(maximum, int) and maximum > 0:
                            self._progress[prompt_id] = min(
                                99, max(0, round(value * 100 / maximum))
                            )
                    elif message_type == "executing" and data.get("node") is None:
                        self._progress[prompt_id] = 100
                        return
                    elif message_type in {"execution_error", "execution_interrupted"}:
                        return
        except (TimeoutError, aiohttp.ClientError):
            return

    def _watch_finished(self, prompt_id: str, task: asyncio.Task[None]) -> None:
        current = self._watches.get(prompt_id)
        if current is task:
            self._watches.pop(prompt_id, None)
        with suppress(asyncio.CancelledError, Exception):
            task.result()


async def _read_bounded(response: aiohttp.ClientResponse, *, max_bytes: int) -> bytes:
    declared_size = _content_length(response.headers.get("Content-Length"))
    if declared_size is not None and declared_size > max_bytes:
        raise ComfyUITransportError(
            "ComfyUI 响应超过安全大小限制",
            code="response_too_large",
            retryable=False,
        )
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > max_bytes:
            raise ComfyUITransportError(
                "ComfyUI 响应超过安全大小限制",
                code="response_too_large",
                retryable=False,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _endpoint(base_url: str, route: str) -> str:
    return f"{base_url.rstrip('/')}/{route.lstrip('/')}"


def _websocket_endpoint(base_url: str, client_id: str) -> str:
    parsed = urlsplit(_endpoint(base_url, "/ws"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, parsed.path, urlencode({"clientId": client_id}), ""))


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ComfyUITransportError(
            f"{label} 格式无效",
            code="protocol_error",
            retryable=False,
        )
    return cast(Mapping[str, object], value)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _find_prompt_by_execution(value: object, execution_id: str) -> str | None:
    if isinstance(value, Mapping):
        if value.get("astraweft_execution_id") == execution_id:
            prompt_id = value.get("prompt_id")
            return prompt_id if isinstance(prompt_id, str) else None
        for key, child in value.items():
            if _contains_execution_marker(child, execution_id):
                if isinstance(key, str) and key not in {
                    "queue_running",
                    "queue_pending",
                    "extra_data",
                }:
                    return key
                prompt_id = _prompt_id(child)
                if prompt_id is not None:
                    return prompt_id
    return None


def _contains_execution_marker(value: object, execution_id: str) -> bool:
    if isinstance(value, Mapping):
        return value.get("astraweft_execution_id") == execution_id or any(
            _contains_execution_marker(child, execution_id) for child in value.values()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_execution_marker(child, execution_id) for child in value)
    return False


def _prompt_id(value: object) -> str | None:
    if isinstance(value, Mapping):
        candidate = value.get("prompt_id")
        if isinstance(candidate, str):
            return candidate
        for child in value.values():
            found = _prompt_id(child)
            if found is not None:
                return found
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 1 and isinstance(value[1], str):
            return value[1]
        for child in value:
            found = _prompt_id(child)
            if found is not None:
                return found
    return None


def _queue_status(queue: Mapping[str, object], prompt_id: str) -> ComfyUIExecutionStatus | None:
    for key, status in (
        ("queue_running", ComfyUIExecutionStatus.RUNNING),
        ("queue_pending", ComfyUIExecutionStatus.QUEUED),
    ):
        entries = queue.get(key)
        if (
            isinstance(entries, Sequence)
            and not isinstance(entries, (str, bytes, bytearray))
            and any(_prompt_id(entry) == prompt_id for entry in entries)
        ):
            return status
    return None


def _history_snapshot(
    prompt_id: str,
    record: Mapping[str, object],
    progress: int | None,
) -> ComfyUIRemoteSnapshot:
    outputs_raw = record.get("outputs")
    outputs = outputs_raw if isinstance(outputs_raw, Mapping) else {}
    files = _output_files(outputs)
    status_raw = record.get("status")
    status = status_raw if isinstance(status_raw, Mapping) else {}
    status_text = status.get("status_str")
    messages = status.get("messages")
    message_error = _history_error_code(messages)
    failed = status_text in {"error", "failed"} or message_error is not None
    if failed:
        return ComfyUIRemoteSnapshot(
            status=ComfyUIExecutionStatus.FAILED,
            progress=progress,
            outputs=cast(Mapping[str, object], outputs),
            files=files,
            error_code=message_error or "remote_execution_failed",
            error_message=f"ComfyUI 任务 {prompt_id} 执行失败",
        )
    completed = status.get("completed") is True or status_text == "success"
    return ComfyUIRemoteSnapshot(
        status=(
            ComfyUIExecutionStatus.MATERIALIZING if completed else ComfyUIExecutionStatus.RUNNING
        ),
        progress=100 if completed else progress,
        outputs=cast(Mapping[str, object], outputs),
        files=files,
    )


def _history_error_code(value: object) -> str | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    for message in value:
        if (
            isinstance(message, Sequence)
            and not isinstance(message, (str, bytes, bytearray))
            and message
        ):
            if message[0] == "execution_interrupted":
                return "remote_execution_interrupted"
            if message[0] == "execution_error":
                return "remote_execution_failed"
    return None


def _output_files(outputs: Mapping[str, object]) -> tuple[ComfyUIOutputFile, ...]:
    files: list[ComfyUIOutputFile] = []
    for node_id, node_value in outputs.items():
        if not isinstance(node_id, str) or not isinstance(node_value, Mapping):
            continue
        for collection, raw_items in node_value.items():
            if not isinstance(raw_items, Sequence) or isinstance(
                raw_items, (str, bytes, bytearray)
            ):
                continue
            for raw in raw_items:
                if not isinstance(raw, Mapping):
                    continue
                filename = raw.get("filename")
                if not isinstance(filename, str) or not filename:
                    continue
                subfolder = raw.get("subfolder")
                folder_type = raw.get("type")
                files.append(
                    ComfyUIOutputFile(
                        node_id=node_id,
                        filename=filename,
                        subfolder=subfolder if isinstance(subfolder, str) else "",
                        folder_type=folder_type if isinstance(folder_type, str) else "output",
                        kind=_output_kind(str(collection), filename),
                    )
                )
    return tuple(files)


def _output_kind(collection: str, filename: str) -> str:
    lowered = f"{collection} {filename}".lower()
    if any(token in lowered for token in ("video", ".mp4", ".webm", ".mov")):
        return "video"
    if any(token in lowered for token in ("audio", ".wav", ".mp3", ".flac")):
        return "audio"
    if any(token in lowered for token in ("text", ".txt", ".json")):
        return "text"
    return "image"


def _decode_ws_message(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, str):
        return None
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return cast(Mapping[str, object], loaded) if isinstance(loaded, Mapping) else None


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ComfyUITransportError(
            "ComfyUI Content-Length 无效",
            code="protocol_error",
            retryable=False,
        ) from exc
    if parsed < 0:
        raise ComfyUITransportError(
            "ComfyUI Content-Length 无效",
            code="protocol_error",
            retryable=False,
        )
    return parsed


def _content_type(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.partition(";")[0].strip().lower()
    if not normalized or len(normalized) > 255 or not normalized.isascii():
        return None
    return normalized
