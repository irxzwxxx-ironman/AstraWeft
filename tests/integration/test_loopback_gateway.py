"""Loopback gateway authentication, origin, body, rate, and task tests."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from astraweft.application.providers import CreateProvider
from astraweft.bootstrap.container import build_app_context
from astraweft.infrastructure.gateway import LoopbackGateway
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.secrets import SecretValue


def _custom_nodes_module() -> Any:
    path = (
        Path(__file__).parents[2]
        / "integrations"
        / "comfyui_custom_nodes"
        / "AstraWeftGateway"
        / "nodes.py"
    )
    spec = importlib.util.spec_from_file_location("astraweft_custom_nodes", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("custom node module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_custom_node_builds_dropdowns_from_gateway_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_nodes = _custom_nodes_module()
    monkeypatch.setattr(
        custom_nodes,
        "_json_request",
        lambda *_args, **_kwargs: {
            "providers": [
                {"id": "provider-b", "enabled": False},
                {"id": "provider-a", "enabled": True},
            ],
            "models": [
                {
                    "id": "model-a",
                    "enabled": True,
                    "operations": ["image.generate", "custom.invoke"],
                },
                {"id": "model-b", "enabled": False, "operations": ["ignored"]},
            ],
        },
    )

    providers, models, operations = custom_nodes._catalog_choices("image.generate")

    assert providers == ["provider-a"]
    assert models == ["model-a"]
    assert operations == ["custom.invoke", "image.generate"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gateway_rejects_untrusted_requests_and_controls_tasks(tmp_path: Path) -> None:
    secret_store = SessionSecretStore()
    context = await build_app_context(tmp_path, secret_store_override=secret_store)
    gateway = LoopbackGateway(
        tasks=context.task_service,
        providers=context.provider_service,
        secrets_store=secret_store,
        artifact_root=context.paths.artifact_dir,
        port=0,
        rate_limit_per_minute=20,
    )
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Gateway Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = next(
            item
            for item in await context.provider_service.sync_models(provider.id)
            if item.remote_model_id == "mock-text-v1"
        )
        await gateway.start()
        assert gateway.running is True
        assert gateway.bound_port is not None
        base = f"http://127.0.0.1:{gateway.bound_port}/api/v1"
        token = (await secret_store.get("loopback_gateway", "access_token")).reveal()
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as client:
            unauthorized = await client.get(f"{base}/health")
            assert unauthorized.status == 401
            assert "Access-Control-Allow-Origin" not in unauthorized.headers

            rejected_origin = await client.get(
                f"{base}/health",
                headers={**headers, "Origin": "https://evil.example"},
            )
            assert rejected_origin.status == 403

            health = await client.get(f"{base}/health", headers=headers)
            assert health.status == 200
            assert (await health.json())["api"] == "astraweft.loopback/v1"
            assert health.headers["Cache-Control"] == "no-store"

            catalog = await client.get(f"{base}/catalog", headers=headers)
            catalog_body = await catalog.json()
            assert catalog.status == 200
            assert catalog_body["providers"][0]["id"] == provider.id
            assert model.id in {item["id"] for item in catalog_body["models"]}

            created = await client.post(
                f"{base}/tasks",
                headers=headers,
                json={
                    "provider_id": provider.id,
                    "model_id": model.id,
                    "operation": "text.generate",
                    "inputs": {"prompt": "hello"},
                    "timeout_seconds": 30,
                },
            )
            created_body = await created.json()
            assert created.status == 202
            assert created_body["status"] == "QUEUED"

            task_id = created_body["id"]
            task = await client.get(f"{base}/tasks/{task_id}", headers=headers)
            assert (await task.json())["id"] == task_id
            canceled = await client.post(
                f"{base}/tasks/{task_id}/cancel",
                headers=headers,
                json={},
            )
            assert (await canceled.json())["status"] == "CANCELED"

            oversized = await client.post(
                f"{base}/tasks",
                headers={**headers, "Content-Length": str(300 * 1024)},
                data=b"{}",
            )
            assert oversized.status == 413
    finally:
        await gateway.stop()
        await context.close()
    assert gateway.running is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gateway_enforces_authenticated_rate_limit(tmp_path: Path) -> None:
    secret_store = SessionSecretStore()
    context = await build_app_context(tmp_path, secret_store_override=secret_store)
    gateway = LoopbackGateway(
        tasks=context.task_service,
        providers=context.provider_service,
        secrets_store=secret_store,
        artifact_root=context.paths.artifact_dir,
        port=0,
        rate_limit_per_minute=2,
    )
    try:
        await gateway.start()
        token = (await secret_store.get("loopback_gateway", "access_token")).reveal()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"http://127.0.0.1:{gateway.bound_port}/api/v1/health"
        async with aiohttp.ClientSession() as client:
            assert (await client.get(url, headers=headers)).status == 200
            assert (await client.get(url, headers=headers)).status == 200
            limited = await client.get(url, headers=headers)
            assert limited.status == 429
            assert (await limited.json())["error"]["code"] == "rate_limited"
    finally:
        await gateway.stop()
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gateway_publishes_same_user_token_for_comfyui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_nodes = _custom_nodes_module()
    secret_store = SessionSecretStore()
    context = await build_app_context(tmp_path, secret_store_override=secret_store)
    token_path = tmp_path / "handoff" / "gateway-token"
    gateway = LoopbackGateway(
        tasks=context.task_service,
        providers=context.provider_service,
        secrets_store=secret_store,
        artifact_root=context.paths.artifact_dir,
        port=0,
        token_handoff_path=token_path,
    )
    monkeypatch.setenv("ASTRAWEFT_GATEWAY_TOKEN_FILE", str(token_path))
    monkeypatch.delenv("ASTRAWEFT_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(custom_nodes, "_SERVICE_NAME", "AstraWeft-test-no-keyring-token")
    try:
        await gateway.start()
        expected = (await secret_store.get("loopback_gateway", "access_token")).reveal()
        assert token_path.read_text(encoding="utf-8") == expected
        if os.name != "nt":
            assert token_path.stat().st_mode & 0o077 == 0
        assert custom_nodes._gateway_token() == expected
    finally:
        await gateway.stop()
        await context.close()
    assert not token_path.exists()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gateway_rejects_host_options_bad_payloads_and_missing_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError):
        LoopbackGateway(
            tasks=None,  # type: ignore[arg-type]
            providers=None,  # type: ignore[arg-type]
            secrets_store=SessionSecretStore(),
            artifact_root=tmp_path,
            host="192.0.2.1",
        )
    with pytest.raises(ValueError):
        LoopbackGateway(
            tasks=None,  # type: ignore[arg-type]
            providers=None,  # type: ignore[arg-type]
            secrets_store=SessionSecretStore(),
            artifact_root=tmp_path,
            rate_limit_per_minute=0,
        )

    secret_store = SessionSecretStore()
    context = await build_app_context(tmp_path, secret_store_override=secret_store)
    gateway = LoopbackGateway(
        tasks=context.task_service,
        providers=context.provider_service,
        secrets_store=secret_store,
        artifact_root=context.paths.artifact_dir,
        port=0,
        rate_limit_per_minute=20,
    )
    try:
        await gateway.start()
        await gateway.start()
        token = (await secret_store.get("loopback_gateway", "access_token")).reveal()
        headers = {"Authorization": f"Bearer {token}"}
        base = f"http://127.0.0.1:{gateway.bound_port}/api/v1"
        async with aiohttp.ClientSession() as client:
            options = await client.options(f"{base}/health", headers=headers)
            assert options.status == 403
            invalid_host = await client.get(
                f"{base}/health",
                headers={**headers, "Host": "evil.example"},
            )
            assert invalid_host.status == 400
            allowed_origin = await client.get(
                f"{base}/health",
                headers={
                    **headers,
                    "Origin": f"http://localhost:{gateway.bound_port}",
                },
            )
            assert allowed_origin.status == 200
            bad_body = await client.post(f"{base}/tasks", headers=headers, json=[])
            assert bad_body.status == 400
            unknown_field = await client.post(
                f"{base}/tasks",
                headers=headers,
                json={"unknown": True},
            )
            assert unknown_field.status == 400
            bad_priority = await client.post(
                f"{base}/tasks",
                headers=headers,
                json={
                    "provider_id": "provider",
                    "model_id": "model",
                    "operation": "text.generate",
                    "priority": True,
                    "timeout_seconds": False,
                },
            )
            assert bad_priority.status == 400
            missing_task = await client.get(f"{base}/tasks/missing", headers=headers)
            assert missing_task.status == 404
            missing_task_artifacts = await client.get(
                f"{base}/tasks/missing/artifacts", headers=headers
            )
            assert missing_task_artifacts.status == 404
            missing_artifact = await client.get(f"{base}/artifacts/missing", headers=headers)
            assert missing_artifact.status == 404

            async def fail_catalog() -> object:
                raise RuntimeError("boom")

            monkeypatch.setattr(context.provider_service, "list_providers", fail_catalog)
            internal = await client.get(f"{base}/catalog", headers=headers)
            assert internal.status == 500

        before = time.monotonic()
        gateway._requests = deque([before - 61.0])
        assert await gateway._take_rate_slot() is True
        assert len(gateway._requests) == 1
        assert gateway._requests[0] >= before
    finally:
        await gateway.stop()
        await gateway.stop()
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_custom_node_calls_mock_provider_and_downloads_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_nodes = _custom_nodes_module()
    secret_store = SessionSecretStore()
    context = await build_app_context(tmp_path, secret_store_override=secret_store)
    gateway = LoopbackGateway(
        tasks=context.task_service,
        providers=context.provider_service,
        secrets_store=secret_store,
        artifact_root=context.paths.artifact_dir,
        port=0,
    )
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Custom Node Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        models = await context.provider_service.sync_models(provider.id)
        model = next(item for item in models if item.remote_model_id == "mock-image-v1")
        text_model = next(item for item in models if item.remote_model_id == "mock-text-v1")
        context.task_runtime.start()
        await gateway.start()
        token = (await secret_store.get("loopback_gateway", "access_token")).reveal()
        monkeypatch.setattr(
            custom_nodes,
            "_BASE_URL",
            f"http://127.0.0.1:{gateway.bound_port}/api/v1",
        )
        monkeypatch.setattr(custom_nodes, "_gateway_token", lambda: token)

        artifact = await asyncio.to_thread(
            custom_nodes._run_task,
            provider.id,
            model.id,
            "image.generate",
            "gateway e2e",
            "{}",
            10,
            expected_kind="IMAGE",
        )
        assert artifact["kind"] == "IMAGE"
        downloaded = await asyncio.to_thread(custom_nodes._download_artifact, artifact, 10)
        assert downloaded.read_bytes() == b"mock-image-artifact"
        snapshot = await asyncio.to_thread(
            custom_nodes._submit_and_wait,
            provider.id,
            text_model.id,
            "text.generate",
            "",
            '{"prompt": "generic json"}',
            10,
        )
        output = snapshot["output"]
        assert isinstance(output, dict)
        data = output["data"]
        assert isinstance(data, dict)
        assert data["text"] == "Mock response"
    finally:
        await gateway.stop()
        await context.close()
