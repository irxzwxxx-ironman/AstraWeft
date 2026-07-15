"""Core-owned Provider HTTP boundary tests."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from astraweft.infrastructure.network import CoreHttpClient, RestrictedHttpTransport
from astraweft_provider_sdk import (
    ProviderNetworkError,
    ProviderProtocolError,
    ProviderTimeoutError,
    UnsupportedOperationError,
)


def _client(
    handler: httpx.AsyncBaseTransport,
    *,
    max_response_bytes: int = 1024,
) -> CoreHttpClient:
    return CoreHttpClient(
        user_agent="AstraWeft/test",
        client=httpx.AsyncClient(transport=handler),
        max_response_bytes=max_response_bytes,
    )


@pytest.mark.asyncio
async def test_restricted_transport_sends_and_normalizes_allowed_https_request() -> None:
    captured: httpx.Request | None = None

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(200, headers={"X-Request-ID": "req_123"}, json={"ok": True})

    core = _client(httpx.MockTransport(handle))
    transport = RestrictedHttpTransport(core, ("api.example.test",))
    response = await transport.request(
        "post",
        "https://api.example.test/v1/responses",
        headers={"Authorization": "Bearer secret"},
        json_body={"input": "hello"},
        idempotency_key="stable-key",
        trace_id="trace-1",
    )
    await core.close()
    await core.close()

    assert captured is not None
    assert captured.method == "POST"
    assert captured.headers["idempotency-key"] == "stable-key"
    assert captured.headers["authorization"] == "Bearer secret"
    assert response.status == 200
    assert response.headers["x-request-id"] == "req_123"
    assert response.body == b'{"ok":true}'
    with pytest.raises(ProviderProtocolError, match="closed"):
        await transport.request("GET", "https://api.example.test/v1/models")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.test/v1/models",
        "https://other.example.test/v1/models",
        "https://user:password@api.example.test/v1/models",
        "https://api.example.test:8443/v1/models",
        "https://api.example.test/v1/models#fragment",
    ],
)
async def test_restricted_transport_rejects_targets_outside_manifest(url: str) -> None:
    core = _client(httpx.MockTransport(lambda _request: httpx.Response(200)))
    transport = RestrictedHttpTransport(core, ("api.example.test",))
    try:
        with pytest.raises(UnsupportedOperationError):
            await transport.request("GET", url)
    finally:
        await core.close()


@pytest.mark.asyncio
async def test_restricted_transport_supports_manifest_subdomain_patterns() -> None:
    core = _client(httpx.MockTransport(lambda _request: httpx.Response(204)))
    transport = RestrictedHttpTransport(core, ("*.example.test",))
    try:
        assert (await transport.request("HEAD", "https://api.example.test/health")).status == 204
        with pytest.raises(UnsupportedOperationError):
            await transport.request("HEAD", "https://example.test/health")
    finally:
        await core.close()


@pytest.mark.asyncio
async def test_restricted_transport_caps_response_size() -> None:
    core = _client(
        httpx.MockTransport(lambda _request: httpx.Response(200, content=b"too large")),
        max_response_bytes=4,
    )
    try:
        with pytest.raises(ProviderProtocolError, match="size limit"):
            await core.request(
                "GET",
                "https://api.example.test/v1/models",
                allowed_hosts=("api.example.test",),
            )
    finally:
        await core.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (httpx.ReadTimeout("late"), ProviderTimeoutError),
        (httpx.ConnectError("offline"), ProviderNetworkError),
    ],
)
async def test_restricted_transport_normalizes_network_failures(
    raised: httpx.RequestError,
    expected: type[Exception],
) -> None:
    def fail(_request: httpx.Request) -> httpx.Response:
        raise raised

    core = _client(httpx.MockTransport(fail))
    try:
        with pytest.raises(expected):
            await core.request(
                "GET",
                "https://api.example.test/v1/models",
                allowed_hosts=("api.example.test",),
            )
    finally:
        await core.close()


@pytest.mark.asyncio
async def test_restricted_transport_rejects_empty_permission_and_unknown_method() -> None:
    core = _client(httpx.MockTransport(lambda _request: httpx.Response(200)))
    try:
        with pytest.raises(UnsupportedOperationError, match="no network permission"):
            await core.request(
                "GET",
                "https://api.example.test/v1/models",
                allowed_hosts=(),
            )
        with pytest.raises(UnsupportedOperationError, match="HTTP method"):
            await core.request(
                "TRACE",
                "https://api.example.test/v1/models",
                allowed_hosts=("api.example.test",),
            )
    finally:
        await core.close()


@pytest.mark.asyncio
async def test_artifact_download_streams_with_hash_content_type_and_host_permission(
    tmp_path: Path,
) -> None:
    payload = b"video-payload"
    core = _client(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-length": str(len(payload)), "content-type": "video/mp4; x=y"},
                content=payload,
                request=request,
            )
        )
    )
    target = tmp_path / "artifact.partial"
    try:
        result = await core.download(
            "https://cdn.example.test/video.mp4?signature=secret",
            allowed_hosts=("*.example.test",),
            target=target,
            max_bytes=100,
            timeout_seconds=10,
            trace_id="trace-download",
        )
    finally:
        await core.close()

    assert target.read_bytes() == payload
    assert result.size_bytes == len(payload)
    assert result.sha256 == __import__("hashlib").sha256(payload).hexdigest()
    assert result.content_type == "video/mp4"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "headers", "payload", "message"),
    [
        (302, {"location": "https://other.example.test/video"}, b"redirect", "HTTP 302"),
        (200, {"content-length": "101"}, b"small", "size limit"),
        (200, {"content-length": "invalid"}, b"small", "Content-Length"),
        (200, {}, b"", "empty body"),
    ],
)
async def test_artifact_download_rejects_unsafe_or_invalid_responses(
    tmp_path: Path,
    status: int,
    headers: dict[str, str],
    payload: bytes,
    message: str,
) -> None:
    core = _client(
        httpx.MockTransport(
            lambda request: httpx.Response(
                status, headers=headers, content=payload, request=request
            )
        )
    )
    try:
        with pytest.raises(ProviderProtocolError, match=message):
            await core.download(
                "https://cdn.example.test/video.mp4",
                allowed_hosts=("cdn.example.test",),
                target=tmp_path / "artifact.partial",
                max_bytes=100,
                timeout_seconds=10,
            )
    finally:
        await core.close()
