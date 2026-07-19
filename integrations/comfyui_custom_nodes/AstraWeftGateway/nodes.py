"""Authenticated Provider image/video nodes with no API-key widgets."""

from __future__ import annotations

import json
import os
import stat
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path
from tempfile import gettempdir
from typing import Any

_BASE_URL = "http://127.0.0.1:17493/api/v1"
_SERVICE_NAME = "AstraWeft"
_KEYRING_ACCOUNT = "loopback_gateway:access_token"
_DEFAULT_TOKEN_FILE = Path.home() / ".astraweft" / "comfyui-gateway-token"


class AstraWeftGatewayError(RuntimeError):
    """The desktop gateway could not complete a Custom Node request."""


class AstraWeftProviderImage:
    CATEGORY = "AstraWeft"
    FUNCTION = "generate"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, object]:
        provider_ids, model_ids, operations = _catalog_choices("image.generate")
        return {
            "required": {
                "provider_id": (provider_ids,),
                "model_id": (model_ids,),
                "operation": (operations,),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "inputs_json": ("STRING", {"multiline": True, "default": "{}"}),
                "timeout_seconds": ("INT", {"default": 300, "min": 1, "max": 86400}),
            }
        }

    def generate(
        self,
        provider_id: str,
        model_id: str,
        operation: str,
        prompt: str,
        inputs_json: str,
        timeout_seconds: int,
    ) -> tuple[Any]:
        artifact = _run_task(
            provider_id,
            model_id,
            operation,
            prompt,
            inputs_json,
            timeout_seconds,
            expected_kind="IMAGE",
        )
        path = _download_artifact(artifact, timeout_seconds)
        return (_load_image_tensor(path),)


class AstraWeftProviderVideo:
    CATEGORY = "AstraWeft"
    FUNCTION = "generate"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("video_path",)
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, object]:
        provider_ids, model_ids, operations = _catalog_choices("video.generate")
        return {
            "required": {
                "provider_id": (provider_ids,),
                "model_id": (model_ids,),
                "operation": (operations,),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "inputs_json": ("STRING", {"multiline": True, "default": "{}"}),
                "timeout_seconds": ("INT", {"default": 900, "min": 1, "max": 86400}),
            }
        }

    def generate(
        self,
        provider_id: str,
        model_id: str,
        operation: str,
        prompt: str,
        inputs_json: str,
        timeout_seconds: int,
    ) -> tuple[str]:
        artifact = _run_task(
            provider_id,
            model_id,
            operation,
            prompt,
            inputs_json,
            timeout_seconds,
            expected_kind="VIDEO",
        )
        return (str(_download_artifact(artifact, timeout_seconds)),)


class AstraWeftProviderJSON:
    """Invoke any configured gateway operation and return its normalized JSON output."""

    CATEGORY = "AstraWeft"
    FUNCTION = "invoke"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result_json",)
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, object]:
        provider_ids, model_ids, operations = _catalog_choices("custom.invoke")
        return {
            "required": {
                "provider_id": (provider_ids,),
                "model_id": (model_ids,),
                "operation": (operations,),
                "inputs_json": ("STRING", {"multiline": True, "default": "{}"}),
                "timeout_seconds": ("INT", {"default": 300, "min": 1, "max": 86400}),
            }
        }

    def invoke(
        self,
        provider_id: str,
        model_id: str,
        operation: str,
        inputs_json: str,
        timeout_seconds: int,
    ) -> tuple[str]:
        snapshot = _submit_and_wait(
            provider_id,
            model_id,
            operation,
            "",
            inputs_json,
            timeout_seconds,
        )
        return (json.dumps(snapshot.get("output"), ensure_ascii=False),)


def _run_task(
    provider_id: str,
    model_id: str,
    operation: str,
    prompt: str,
    inputs_json: str,
    timeout_seconds: int,
    *,
    expected_kind: str,
) -> dict[str, object]:
    snapshot = _submit_and_wait(
        provider_id,
        model_id,
        operation,
        prompt,
        inputs_json,
        timeout_seconds,
    )
    task_id = snapshot.get("id")
    if not isinstance(task_id, str):
        raise AstraWeftGatewayError("AstraWeft 未返回任务编号")
    artifacts = _json_request("GET", f"/tasks/{task_id}/artifacts", None, timeout=10).get(
        "artifacts"
    )
    if not isinstance(artifacts, list):
        raise AstraWeftGatewayError("AstraWeft 产物列表格式无效")
    for artifact in artifacts:
        if (
            isinstance(artifact, dict)
            and str(artifact.get("kind", "")).casefold() == expected_kind.casefold()
        ):
            return artifact
    raise AstraWeftGatewayError(f"任务没有返回 {expected_kind} 产物")


def _submit_and_wait(
    provider_id: str,
    model_id: str,
    operation: str,
    prompt: str,
    inputs_json: str,
    timeout_seconds: int,
) -> dict[str, object]:
    if not provider_id.strip() or not model_id.strip() or not operation.strip():
        raise AstraWeftGatewayError("Provider、模型与操作不能为空")
    health = _json_request("GET", "/health", None, timeout=5)
    if health.get("api") != "astraweft.loopback/v1":
        raise AstraWeftGatewayError("AstraWeft 本机网关版本不兼容")
    try:
        inputs = json.loads(inputs_json or "{}")
    except json.JSONDecodeError as exc:
        raise AstraWeftGatewayError("inputs_json 不是有效 JSON") from exc
    if not isinstance(inputs, dict):
        raise AstraWeftGatewayError("inputs_json 必须是 JSON 对象")
    if prompt:
        inputs["prompt"] = prompt
    task = _json_request(
        "POST",
        "/tasks",
        {
            "provider_id": provider_id,
            "model_id": model_id,
            "operation": operation,
            "inputs": inputs,
            "timeout_seconds": timeout_seconds,
        },
        timeout=10,
    )
    task_id = task.get("id")
    if not isinstance(task_id, str):
        raise AstraWeftGatewayError("AstraWeft 未返回任务编号")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        snapshot = _json_request("GET", f"/tasks/{task_id}", None, timeout=10)
        status = snapshot.get("status")
        if status == "SUCCESS":
            return snapshot
        if status in {"FAILED", "CANCELED", "NEEDS_ATTENTION"}:
            raise AstraWeftGatewayError(f"AstraWeft 任务以 {status} 结束")
        time.sleep(1.0)
    with suppress(AstraWeftGatewayError):
        _json_request("POST", f"/tasks/{task_id}/cancel", {}, timeout=5)
    raise AstraWeftGatewayError("等待 AstraWeft 任务超时")


def _download_artifact(artifact: dict[str, object], timeout_seconds: int) -> Path:
    artifact_id = artifact.get("id")
    download_url = artifact.get("download_url")
    mime_type = artifact.get("mime_type")
    if not isinstance(artifact_id, str) or not isinstance(download_url, str):
        raise AstraWeftGatewayError("AstraWeft 产物信息不完整")
    suffix = _suffix(mime_type if isinstance(mime_type, str) else "")
    target = Path(gettempdir()) / "astraweft-comfyui" / f"{artifact_id}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(  # noqa: S310 - fixed loopback gateway origin
        f"{_BASE_URL.removesuffix('/api/v1')}{download_url}",
        headers={"Authorization": f"Bearer {_gateway_token()}"},
        method="GET",
    )
    partial = target.with_name(f".{target.name}.partial")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            payload = response.read(512 * 1024 * 1024 + 1)
        if not payload or len(payload) > 512 * 1024 * 1024:
            raise AstraWeftGatewayError("AstraWeft 产物为空或过大")
        partial.write_bytes(payload)
        partial.replace(target)
    except (OSError, urllib.error.URLError) as exc:
        partial.unlink(missing_ok=True)
        raise AstraWeftGatewayError("无法下载 AstraWeft 产物") from exc
    return target


def _json_request(
    method: str,
    route: str,
    body: dict[str, object] | None,
    *,
    timeout: int,
) -> dict[str, object]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310
        f"{_BASE_URL}{route}",
        data=payload,
        headers={
            "Authorization": f"Bearer {_gateway_token()}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read(256 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise AstraWeftGatewayError(f"AstraWeft 网关返回 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise AstraWeftGatewayError("无法连接 AstraWeft；请先启动桌面应用") from exc
    if len(raw) > 256 * 1024:
        raise AstraWeftGatewayError("AstraWeft 网关响应过大")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AstraWeftGatewayError("AstraWeft 网关返回无效数据") from exc
    if not isinstance(value, dict):
        raise AstraWeftGatewayError("AstraWeft 网关返回格式无效")
    return value


def _gateway_token() -> str:
    environment_token = os.environ.get("ASTRAWEFT_GATEWAY_TOKEN", "").strip()
    if environment_token:
        return environment_token
    try:
        import keyring
    except ImportError:
        pass
    else:
        try:
            token = keyring.get_password(_SERVICE_NAME, _KEYRING_ACCOUNT)
        except Exception:
            token = None
        if token:
            return token
    configured_path = os.environ.get("ASTRAWEFT_GATEWAY_TOKEN_FILE", "").strip()
    token_path = Path(configured_path).expanduser() if configured_path else _DEFAULT_TOKEN_FILE
    try:
        metadata = token_path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise AstraWeftGatewayError("AstraWeft 本机网关令牌路径不安全")
        if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077:
            raise AstraWeftGatewayError("AstraWeft 本机网关令牌权限不安全")
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AstraWeftGatewayError("未找到 AstraWeft 本机网关令牌；请先启动 AstraWeft") from exc
    if not token or len(token) > 4096:
        raise AstraWeftGatewayError("AstraWeft 本机网关令牌无效")
    return token


def _catalog_choices(default_operation: str) -> tuple[list[str], list[str], list[str]]:
    """Populate ComfyUI dropdowns without exposing third-party credentials."""
    try:
        catalog = _json_request("GET", "/catalog", None, timeout=3)
    except AstraWeftGatewayError:
        return [""], [""], [default_operation]
    providers = catalog.get("providers")
    models = catalog.get("models")
    provider_ids = (
        sorted(
            {
                str(item["id"])
                for item in providers
                if isinstance(item, dict) and item.get("enabled") is True
            }
        )
        if isinstance(providers, list)
        else []
    )
    model_ids: list[str] = []
    operations = {default_operation}
    if isinstance(models, list):
        for model in models:
            if not isinstance(model, dict) or model.get("enabled") is not True:
                continue
            model_id = model.get("id")
            if isinstance(model_id, str):
                model_ids.append(model_id)
            configured_operations = model.get("operations")
            if isinstance(configured_operations, list):
                operations.update(
                    item for item in configured_operations if isinstance(item, str) and item
                )
    return provider_ids or [""], sorted(set(model_ids)) or [""], sorted(operations)


def _load_image_tensor(path: Path) -> Any:
    try:
        import numpy
        import torch
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise AstraWeftGatewayError("ComfyUI 图像运行库不可用") from exc
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    array = numpy.asarray(image).astype(numpy.float32) / 255.0
    return torch.from_numpy(array)[None,]


def _suffix(mime_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
    }.get(mime_type, ".bin")
