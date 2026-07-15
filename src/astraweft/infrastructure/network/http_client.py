"""Shared async HTTP client with manifest-bound network permissions."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import httpx

from astraweft.ports.artifacts import ArtifactDownloadResult
from astraweft_provider_sdk import (
    HttpResponse,
    ProviderNetworkError,
    ProviderProtocolError,
    ProviderTimeoutError,
    UnsupportedOperationError,
)

_DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class CoreHttpClient:
    """Own one connection pool and execute bounded, non-redirecting requests."""

    def __init__(
        self,
        *,
        user_agent: str,
        client: httpx.AsyncClient | None = None,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("user_agent must not be empty")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            headers={"User-Agent": user_agent},
        )
        self._client.headers["User-Agent"] = user_agent
        self._max_response_bytes = max_response_bytes
        self._closed = False
        self._logger = logging.getLogger("astraweft.infrastructure.network")

    async def request(
        self,
        method: str,
        url: str,
        *,
        allowed_hosts: tuple[str, ...],
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
        timeout_seconds: float | None = None,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> HttpResponse:
        if self._closed:
            raise ProviderProtocolError("Core HTTP transport is already closed")
        host = _validate_target(url, allowed_hosts)
        normalized_method = method.strip().upper()
        if normalized_method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
            raise UnsupportedOperationError("Provider requested an unsupported HTTP method")
        request_headers = dict(headers or {})
        if idempotency_key is not None:
            request_headers.setdefault("Idempotency-Key", idempotency_key)
        try:
            async with self._client.stream(
                normalized_method,
                url,
                headers=request_headers,
                json=None if json_body is None else dict(json_body),
                timeout=timeout_seconds,
            ) as response:
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self._max_response_bytes:
                        raise ProviderProtocolError(
                            "Provider response exceeded the safe size limit"
                        )
                    chunks.append(chunk)
                body = b"".join(chunks)
        except ProviderProtocolError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Provider 网络请求超时") from exc
        except httpx.RequestError as exc:
            raise ProviderNetworkError("无法连接 Provider 网络服务") from exc
        self._logger.info(
            "provider_http_completed",
            extra={
                "method": normalized_method,
                "host": host,
                "http_status": response.status_code,
                "response_bytes": len(body),
                "trace_id": trace_id,
            },
        )
        return HttpResponse(
            status=response.status_code,
            headers={key.lower(): value for key, value in response.headers.items()},
            body=body,
        )

    async def download(
        self,
        url: str,
        *,
        allowed_hosts: tuple[str, ...],
        target: Path,
        max_bytes: int,
        timeout_seconds: float,
        trace_id: str | None = None,
    ) -> ArtifactDownloadResult:
        """Stream one manifest-authorized HTTPS artifact to a private partial path."""
        if self._closed:
            raise ProviderProtocolError("Core HTTP transport is already closed")
        if max_bytes < 1 or timeout_seconds <= 0:
            raise ValueError("artifact download limits must be positive")
        host = _validate_target(url, allowed_hosts)
        digest = hashlib.sha256()
        size = 0
        try:
            async with self._client.stream("GET", url, timeout=timeout_seconds) as response:
                if not 200 <= response.status_code < 300:
                    raise ProviderProtocolError(
                        f"Artifact download returned HTTP {response.status_code}"
                    )
                declared_size = _content_length(response.headers.get("content-length"))
                if declared_size is not None and declared_size > max_bytes:
                    raise ProviderProtocolError("Artifact exceeded the safe size limit")
                async with await anyio.open_file(target, "wb") as stream:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise ProviderProtocolError("Artifact exceeded the safe size limit")
                        digest.update(chunk)
                        await stream.write(chunk)
        except ProviderProtocolError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("成果下载超时") from exc
        except httpx.RequestError as exc:
            raise ProviderNetworkError("无法下载 Provider 成果") from exc
        if size == 0:
            raise ProviderProtocolError("Artifact download returned an empty body")
        content_type = _content_type(response.headers.get("content-type"))
        self._logger.info(
            "artifact_download_completed",
            extra={
                "method": "GET",
                "host": host,
                "http_status": response.status_code,
                "response_bytes": size,
                "trace_id": trace_id,
            },
        )
        return ArtifactDownloadResult(
            size_bytes=size,
            sha256=digest.hexdigest(),
            content_type=content_type,
        )

    async def close(self) -> None:
        if self._closed:
            return
        await self._client.aclose()
        self._closed = True


class RestrictedHttpTransport:
    """Bind the shared HTTP client to one plugin's manifest host list."""

    def __init__(self, client: CoreHttpClient, allowed_hosts: tuple[str, ...]) -> None:
        self._client = client
        self._allowed_hosts = tuple(host.lower() for host in allowed_hosts)

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
        return await self._client.request(
            method,
            url,
            allowed_hosts=self._allowed_hosts,
            headers=headers,
            json_body=json_body,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
        )


def _validate_target(url: str, allowed_hosts: tuple[str, ...]) -> str:
    if not allowed_hosts:
        raise UnsupportedOperationError("Provider plugin has no network permission")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise UnsupportedOperationError("Provider network access requires an HTTPS URL")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise UnsupportedOperationError("Provider URL contains forbidden userinfo or fragment")
    if parsed.port not in (None, 443):
        raise UnsupportedOperationError("Provider network access requires the standard HTTPS port")
    host = parsed.hostname.lower()
    if not any(_host_matches(host, pattern.lower()) for pattern in allowed_hosts):
        raise UnsupportedOperationError("Provider URL host is outside manifest permissions")
    return host


def _host_matches(host: str, pattern: str) -> bool:
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix) and host != suffix[1:]
    return host == pattern


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ProviderProtocolError("Artifact Content-Length is invalid") from exc
    if parsed < 0:
        raise ProviderProtocolError("Artifact Content-Length is invalid")
    return parsed


def _content_type(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.partition(";")[0].strip().lower()
    if not normalized or len(normalized) > 255 or not normalized.isascii():
        return None
    return normalized
