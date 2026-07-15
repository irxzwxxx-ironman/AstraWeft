"""ComfyUI HTTP/WebSocket protocol adapter tests against a loopback server."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from aiohttp import web

from astraweft.domain.comfyui import ComfyUIExecutionStatus, ComfyUIHealth, ComfyUIInstance
from astraweft.infrastructure.comfyui import AioHttpComfyUIClient, ComfyUITransportError
from astraweft.infrastructure.comfyui.client import (
    _content_length,
    _content_type,
    _decode_ws_message,
    _find_prompt_by_execution,
    _history_snapshot,
    _output_files,
    _prompt_id,
    _queue_status,
    _websocket_endpoint,
)
from astraweft.ports.comfyui import ComfyUIOutputFile


def _instance(base_url: str) -> ComfyUIInstance:
    now = datetime(2026, 7, 15, tzinfo=UTC)
    return ComfyUIInstance(
        id="instance-1",
        name="Loopback",
        base_url=base_url,
        enabled=True,
        health=ComfyUIHealth.UNKNOWN,
        version=None,
        python_version=None,
        capabilities={},
        node_catalog_hash=None,
        last_error_code=None,
        last_checked_at=None,
        row_version=1,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_comfyui_client_probe_submit_reconcile_progress_and_download(
    tmp_path: Path,
) -> None:
    state: dict[str, Any] = {"mode": "queued", "submitted": None, "canceled": False}

    async def system_stats(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "system": {"comfyui_version": "0.3.50", "python_version": "3.12"},
                "devices": [{"name": "Metal"}],
            }
        )

    async def features(_request: web.Request) -> web.Response:
        return web.json_response({"supports_preview_metadata": True})

    async def object_info(_request: web.Request) -> web.Response:
        return web.json_response({"KSampler": {}, "SaveImage": {}})

    async def prompt(request: web.Request) -> web.Response:
        state["submitted"] = await request.json()
        return web.json_response({"prompt_id": "prompt-1", "number": 7})

    def queue_payload() -> dict[str, object]:
        submitted = state["submitted"] or {
            "prompt": {},
            "extra_data": {"astraweft_execution_id": "exec-1"},
        }
        extra = submitted.get("extra_data", {})
        entry = [7, "prompt-1", submitted.get("prompt", {}), extra]
        if state["mode"] == "running":
            return {"queue_running": [entry], "queue_pending": []}
        if state["mode"] == "queued":
            return {"queue_running": [], "queue_pending": [entry]}
        return {"queue_running": [], "queue_pending": []}

    async def queue(request: web.Request) -> web.Response:
        if request.method == "POST":
            payload = await request.json()
            state["canceled"] = payload == {"delete": ["prompt-1"]}
            state["mode"] = "none"
            return web.json_response({})
        return web.json_response(queue_payload())

    async def history_all(_request: web.Request) -> web.Response:
        if state["mode"] != "history":
            return web.json_response({})
        return web.json_response(
            {
                "prompt-1": {
                    "prompt": [
                        7,
                        "prompt-1",
                        {},
                        {"astraweft_execution_id": "exec-1"},
                    ],
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {
                        "9": {
                            "images": [
                                {
                                    "filename": "result.png",
                                    "subfolder": "",
                                    "type": "output",
                                }
                            ]
                        }
                    },
                }
            }
        )

    async def history_one(request: web.Request) -> web.Response:
        full = await history_all(request)
        return full

    async def view(_request: web.Request) -> web.Response:
        return web.Response(body=b"PNG-DATA", content_type="image/png")

    async def interrupt(_request: web.Request) -> web.Response:
        state["canceled"] = True
        state["mode"] = "none"
        return web.json_response({})

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        assert request.query["clientId"] == "client-1"
        response = web.WebSocketResponse()
        await response.prepare(request)
        await response.send_json(
            {
                "type": "progress",
                "data": {"prompt_id": "prompt-1", "value": 3, "max": 4},
            }
        )
        await response.send_json(
            {
                "type": "executing",
                "data": {"prompt_id": "prompt-1", "node": None},
            }
        )
        await response.close()
        return response

    app = web.Application()
    app.add_routes(
        [
            web.get("/system_stats", system_stats),
            web.get("/features", features),
            web.get("/object_info", object_info),
            web.post("/prompt", prompt),
            web.get("/queue", queue),
            web.post("/queue", queue),
            web.get("/history", history_all),
            web.get("/history/{prompt_id}", history_one),
            web.get("/view", view),
            web.post("/interrupt", interrupt),
            web.get("/ws", websocket),
        ]
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets  # type: ignore[union-attr]
    port = sockets[0].getsockname()[1]
    instance = _instance(f"http://127.0.0.1:{port}")
    client = AioHttpComfyUIClient(user_agent="AstraWeft/Test")
    try:
        probe = await client.probe(instance)
        assert probe.version == "0.3.50"
        assert probe.python_version == "3.12"
        assert probe.capabilities == {
            "node_count": 2,
            "features": {"supports_preview_metadata": True},
            "device_count": 1,
        }

        submitted = await client.submit(
            instance,
            prompt={"1": {"class_type": "KSampler", "inputs": {}}},
            client_id="client-1",
            execution_id="exec-1",
            workflow_checksum="a" * 64,
        )
        assert submitted.prompt_id == "prompt-1"
        assert await client.find_execution(instance, "exec-1") == "prompt-1"
        assert (await client.snapshot(instance, "prompt-1")).status.value == "QUEUED"

        await client.ensure_progress_watch(
            instance,
            prompt_id="prompt-1",
            client_id="client-1",
        )
        for _ in range(100):
            if client.latest_progress("prompt-1") == 100:
                break
            await asyncio.sleep(0.01)
        assert client.latest_progress("prompt-1") == 100

        state["mode"] = "history"
        snapshot = await client.snapshot(instance, "prompt-1")
        assert snapshot.status.value == "MATERIALIZING"
        assert snapshot.files[0].filename == "result.png"

        target = tmp_path / "result.partial"
        result = await client.download_output(
            instance,
            ComfyUIOutputFile("9", "result.png", "", "output", "image"),
            target=target,
            max_bytes=100,
            timeout_seconds=2,
        )
        assert target.read_bytes() == b"PNG-DATA"
        assert result.content_type == "image/png"
        assert len(result.sha256) == 64

        state["mode"] = "queued"
        assert await client.cancel(instance, "prompt-1") is True
        assert state["canceled"] is True
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_comfyui_client_rejects_bad_protocol_and_closed_client() -> None:
    async def invalid(_request: web.Request) -> web.Response:
        return web.Response(text="not-json", content_type="application/json")

    app = web.Application()
    app.router.add_get("/system_stats", invalid)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets  # type: ignore[union-attr]
    port = sockets[0].getsockname()[1]
    instance = _instance(f"http://127.0.0.1:{port}")
    client = AioHttpComfyUIClient(user_agent="AstraWeft/Test")
    try:
        with pytest.raises(ComfyUITransportError) as error:
            await client.probe(instance)
        assert error.value.code == "invalid_json"
        await client.close()
        with pytest.raises(ComfyUITransportError) as closed:
            await client.probe(instance)
        assert closed.value.code == "client_closed"
    finally:
        await client.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_comfyui_client_remote_state_and_download_failure_edges(tmp_path: Path) -> None:
    state = {"queue": "running", "history": False, "view": "http_error"}

    async def queue(_request: web.Request) -> web.Response:
        entry = [1, "prompt-edge", {}, {"astraweft_execution_id": "exec-edge"}]
        if state["queue"] == "running":
            return web.json_response({"queue_running": [entry], "queue_pending": []})
        return web.json_response({"queue_running": [], "queue_pending": []})

    async def history(_request: web.Request) -> web.Response:
        if not state["history"]:
            return web.json_response({})
        return web.json_response(
            {
                "prompt-edge": {
                    "prompt": [1, "prompt-edge", {}, {"astraweft_execution_id": "exec-edge"}],
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {},
                }
            }
        )

    async def invalid_prompt(_request: web.Request) -> web.Response:
        return web.json_response({"number": "not-an-integer"})

    async def interrupt(_request: web.Request) -> web.Response:
        state["queue"] = "none"
        return web.json_response({})

    async def view(_request: web.Request) -> web.StreamResponse:
        if state["view"] == "http_error":
            return web.Response(status=503)
        if state["view"] == "declared_large":
            return web.Response(body=b"12345")
        if state["view"] == "chunked_large":
            response = web.StreamResponse()
            await response.prepare(_request)
            await response.write(b"12345")
            await response.write_eof()
            return response
        return web.Response(body=b"")

    app = web.Application()
    app.add_routes(
        [
            web.get("/queue", queue),
            web.get("/history", history),
            web.get("/history/{prompt_id}", history),
            web.post("/prompt", invalid_prompt),
            web.post("/interrupt", interrupt),
            web.get("/view", view),
        ]
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets  # type: ignore[union-attr]
    instance = _instance(f"http://127.0.0.1:{sockets[0].getsockname()[1]}")
    client = AioHttpComfyUIClient(user_agent="AstraWeft/Test")
    output = ComfyUIOutputFile("9", "edge.png", "nested", "output", "image")
    try:
        with pytest.raises(ComfyUITransportError) as invalid:
            await client.submit(
                instance,
                prompt={"1": {"class_type": "Text", "inputs": {}}},
                client_id="client-edge",
                execution_id="exec-edge",
                workflow_checksum="a" * 64,
            )
        assert invalid.value.code == "invalid_prompt_response"
        assert await client.find_execution(instance, "exec-edge") == "prompt-edge"
        assert (
            await client.snapshot(instance, "prompt-edge")
        ).status is ComfyUIExecutionStatus.RUNNING
        assert await client.cancel(instance, "prompt-edge") is True

        state["history"] = True
        assert await client.find_execution(instance, "exec-edge") == "prompt-edge"
        assert await client.cancel(instance, "prompt-edge") is True
        state["history"] = False
        missing = await client.snapshot(instance, "prompt-edge")
        assert missing.status is ComfyUIExecutionStatus.NEEDS_ATTENTION
        assert await client.cancel(instance, "prompt-edge") is False

        for mode, code, limit in (
            ("http_error", "output_download_failed", 10),
            ("declared_large", "output_too_large", 4),
            ("chunked_large", "output_too_large", 4),
            ("empty", "empty_output", 10),
        ):
            state["view"] = mode
            with pytest.raises(ComfyUITransportError) as failure:
                await client.download_output(
                    instance,
                    output,
                    target=tmp_path / f"{mode}.partial",
                    max_bytes=limit,
                    timeout_seconds=2,
                )
            assert failure.value.code == code
        with pytest.raises(ValueError):
            await client.download_output(
                instance,
                output,
                target=tmp_path / "invalid.partial",
                max_bytes=0,
                timeout_seconds=2,
            )
    finally:
        await client.close()
        await runner.cleanup()


def test_comfyui_protocol_parsers_handle_malformed_and_multimodal_values() -> None:
    with pytest.raises(ValueError):
        AioHttpComfyUIClient(user_agent="")
    assert _websocket_endpoint("https://example.test/comfy", "a b").startswith("wss://")
    assert _decode_ws_message(None) is None
    assert _decode_ws_message("not json") is None
    assert _decode_ws_message("[]") is None
    assert _decode_ws_message('{"type":"progress"}') == {"type": "progress"}
    assert _content_length(None) is None
    assert _content_length("5") == 5
    for invalid in ("nope", "-1"):
        with pytest.raises(ComfyUITransportError):
            _content_length(invalid)
    assert _content_type(None) is None
    assert _content_type("IMAGE/PNG; charset=binary") == "image/png"
    assert _content_type("") is None
    assert _content_type("图像/png") is None

    nested = {"astraweft_execution_id": "exec", "prompt_id": "prompt-map"}
    assert _find_prompt_by_execution(nested, "exec") == "prompt-map"
    assert _find_prompt_by_execution({"history-id": {"nested": nested}}, "exec") == "history-id"
    assert _find_prompt_by_execution({}, "exec") is None
    assert _prompt_id({"nested": {"prompt_id": "prompt-nested"}}) == "prompt-nested"
    assert _prompt_id([0, "prompt-list"]) == "prompt-list"
    assert _prompt_id([{"other": True}]) is None
    assert _queue_status({"queue_pending": "invalid"}, "prompt") is None

    files = _output_files(
        {
            "9": {
                "videos": [{"filename": "clip.mp4"}],
                "audio": [{"filename": "sound.wav", "subfolder": 1, "type": 2}],
                "text": [{"filename": "result.json"}],
                "bad": [None, {"filename": ""}],
            },
            cast(str, 10): {"images": [{"filename": "ignored.png"}]},
            "bad": "not-an-output",
        }
    )
    assert [item.kind for item in files] == ["video", "audio", "text"]
    assert files[1].subfolder == ""
    assert files[1].folder_type == "output"

    interrupted = _history_snapshot(
        "prompt",
        {"status": {"messages": [["execution_interrupted", {}]]}, "outputs": {}},
        40,
    )
    assert interrupted.error_code == "remote_execution_interrupted"
    failed = _history_snapshot(
        "prompt",
        {"status": {"messages": [["execution_error", {}]]}, "outputs": {}},
        None,
    )
    assert failed.error_code == "remote_execution_failed"
    active = _history_snapshot("prompt", {"status": {}, "outputs": []}, 25)
    assert active.status is ComfyUIExecutionStatus.RUNNING
