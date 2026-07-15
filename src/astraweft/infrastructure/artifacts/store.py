"""Atomic, content-verified local artifact writer."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from astraweft.domain.comfyui import ComfyUIInstance
from astraweft.domain.task import Artifact
from astraweft.ports.artifacts import ArtifactDownloader, ArtifactWriteError
from astraweft.ports.comfyui import ComfyUIClient, ComfyUIOutputFile
from astraweft_provider_sdk import RemoteArtifact

_SAFE_SUFFIX = re.compile(r"^\.[a-zA-Z0-9]{1,12}$")
_DEFAULT_MAX_REMOTE_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class LocalArtifactWriter:
    root: Path
    trash_root: Path | None = None
    downloader: ArtifactDownloader | None = None
    max_remote_bytes: int = _DEFAULT_MAX_REMOTE_BYTES
    download_timeout_seconds: float = 300

    async def exists(self, artifact: Artifact, *, trashed: bool = False) -> bool:
        path = self._trash_path(artifact) if trashed else self.root / artifact.relative_path
        return await asyncio.to_thread(path.is_file)

    async def move_to_trash(self, artifact: Artifact) -> None:
        source = self.root / artifact.relative_path
        destination = self._trash_path(artifact)
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(os.replace, source, destination)
        except FileNotFoundError as exc:
            raise ArtifactWriteError("产物文件不存在，未移入回收站") from exc

    async def restore_from_trash(self, artifact: Artifact) -> None:
        source = self._trash_path(artifact)
        destination = self.root / artifact.relative_path
        if await asyncio.to_thread(destination.exists):
            raise ArtifactWriteError("原位置已有同名文件，未恢复产物")
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(os.replace, source, destination)
        except FileNotFoundError as exc:
            raise ArtifactWriteError("回收站文件不存在，未恢复产物") from exc

    async def purge_from_trash(self, artifact: Artifact) -> None:
        await asyncio.to_thread(self._trash_path(artifact).unlink, missing_ok=True)

    def _trash_path(self, artifact: Artifact) -> Path:
        root = self.trash_root or self.root.parent / "trash"
        return root / "artifacts" / artifact.id / Path(artifact.relative_path).name

    async def write(
        self,
        *,
        task_id: str,
        artifact_id: str,
        remote: RemoteArtifact,
        created_at: datetime,
        allowed_hosts: tuple[str, ...] = (),
        trace_id: str | None = None,
    ) -> Artifact:
        if remote.source == "url":
            return await self._write_url(
                task_id=task_id,
                artifact_id=artifact_id,
                remote=remote,
                created_at=created_at,
                allowed_hosts=allowed_hosts,
                trace_id=trace_id,
            )
        return await asyncio.to_thread(
            self._write_sync,
            task_id,
            artifact_id,
            remote,
            created_at,
        )

    async def materialize(
        self,
        *,
        owner_id: str,
        artifact_id: str,
        instance: ComfyUIInstance,
        output: ComfyUIOutputFile,
        client: ComfyUIClient,
        created_at: datetime,
    ) -> Artifact:
        """Download a ComfyUI `/view` result into the immutable artifact store."""
        remote = RemoteArtifact(
            kind=_remote_kind(output.kind),
            source="url",
            value=f"{instance.base_url}/view",
            filename_hint=output.filename,
            metadata={
                "source": "comfyui",
                "instance_id": instance.id,
                "node_id": output.node_id,
                "remote_filename": output.filename,
                "remote_subfolder": output.subfolder,
                "remote_type": output.folder_type,
            },
        )
        relative, target = self._target(owner_id, artifact_id, remote, created_at)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.partial")
        try:
            result = await client.download_output(
                instance,
                output,
                target=partial,
                max_bytes=self.max_remote_bytes,
                timeout_seconds=self.download_timeout_seconds,
            )
            actual_size = await asyncio.to_thread(lambda: partial.stat().st_size)
            if actual_size != result.size_bytes:
                raise ArtifactWriteError("ComfyUI 产物写入不完整")
            await asyncio.to_thread(os.replace, partial, target)
        except Exception as exc:
            await asyncio.to_thread(partial.unlink, missing_ok=True)
            if isinstance(exc, ArtifactWriteError):
                raise
            raise ArtifactWriteError("ComfyUI 产物下载失败") from exc
        return Artifact(
            id=artifact_id,
            task_id=None,
            kind=output.kind.upper(),
            relative_path=relative.as_posix(),
            mime_type=result.content_type or _filename_mime(output.filename),
            size_bytes=result.size_bytes,
            sha256=result.sha256,
            metadata=remote.metadata,
            source_url_redacted=None,
            created_at=created_at,
        )

    def _write_sync(
        self,
        task_id: str,
        artifact_id: str,
        remote: RemoteArtifact,
        created_at: datetime,
    ) -> Artifact:
        payload = _payload(remote)
        relative, target = self._target(task_id, artifact_id, remote, created_at)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.partial")
        try:
            partial.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            if partial.stat().st_size != len(payload):
                raise ArtifactWriteError("产物写入不完整")
            os.replace(partial, target)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return Artifact(
            id=artifact_id,
            task_id=task_id,
            kind=remote.kind.upper(),
            relative_path=relative.as_posix(),
            mime_type=remote.mime_type or _default_mime(remote),
            size_bytes=len(payload),
            sha256=digest,
            metadata={**dict(remote.metadata), "source": remote.source},
            source_url_redacted=None,
            created_at=created_at,
        )

    async def _write_url(
        self,
        *,
        task_id: str,
        artifact_id: str,
        remote: RemoteArtifact,
        created_at: datetime,
        allowed_hosts: tuple[str, ...],
        trace_id: str | None,
    ) -> Artifact:
        if self.downloader is None:
            raise ArtifactWriteError("未配置 URL 产物下载器")
        if not isinstance(remote.value, str):
            raise ArtifactWriteError("URL 产物地址无效")
        relative, target = self._target(task_id, artifact_id, remote, created_at)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.partial")
        try:
            result = await self.downloader.download(
                remote.value,
                allowed_hosts=allowed_hosts,
                target=partial,
                max_bytes=self.max_remote_bytes,
                timeout_seconds=self.download_timeout_seconds,
                trace_id=trace_id,
            )
            actual_size = await asyncio.to_thread(lambda: partial.stat().st_size)
            if actual_size != result.size_bytes:
                raise ArtifactWriteError("远程产物写入不完整")
            await asyncio.to_thread(os.replace, partial, target)
        except Exception as exc:
            await asyncio.to_thread(partial.unlink, missing_ok=True)
            if isinstance(exc, ArtifactWriteError):
                raise
            raise ArtifactWriteError("远程产物下载失败") from exc
        return Artifact(
            id=artifact_id,
            task_id=task_id,
            kind=remote.kind.upper(),
            relative_path=relative.as_posix(),
            mime_type=remote.mime_type or result.content_type or _default_mime(remote),
            size_bytes=result.size_bytes,
            sha256=result.sha256,
            metadata={**dict(remote.metadata), "source": remote.source},
            source_url_redacted=_redacted_source(remote.value),
            created_at=created_at,
        )

    def _target(
        self,
        task_id: str,
        artifact_id: str,
        remote: RemoteArtifact,
        created_at: datetime,
    ) -> tuple[Path, Path]:
        relative = Path(
            f"{created_at.year:04d}",
            f"{created_at.month:02d}",
            task_id,
            f"{artifact_id}{_suffix(remote)}",
        )
        root = self.root.resolve()
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise ArtifactWriteError("产物路径越界")
        return relative, target


def _payload(remote: RemoteArtifact) -> bytes:
    if remote.source == "text":
        if not isinstance(remote.value, str):
            raise ArtifactWriteError("文本产物内容无效")
        return remote.value.encode("utf-8")
    if remote.source == "json":
        return json.dumps(
            _plain_json(remote.value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    if remote.source == "base64":
        if not isinstance(remote.value, str):
            raise ArtifactWriteError("Base64 产物内容无效")
        try:
            return base64.b64decode(remote.value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ArtifactWriteError("Base64 产物无法解码") from exc
    raise ArtifactWriteError("不支持的产物来源")


def _suffix(remote: RemoteArtifact) -> str:
    if remote.filename_hint:
        suffix = Path(remote.filename_hint).suffix
        if _SAFE_SUFFIX.fullmatch(suffix):
            return suffix.lower()
    return {
        "image": ".img",
        "video": ".video",
        "audio": ".audio",
        "text": ".txt",
        "json": ".json",
    }[remote.kind]


def _default_mime(remote: RemoteArtifact) -> str:
    if remote.source == "json" or remote.kind == "json":
        return "application/json"
    if remote.source == "text" or remote.kind == "text":
        return "text/plain"
    return "application/octet-stream"


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(child) for child in value]
    return value


def _redacted_source(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or "redacted"
    return f"https://{host}/<redacted>"


def _remote_kind(value: str) -> Literal["image", "video", "audio", "text", "json"]:
    if value == "video":
        return "video"
    if value == "audio":
        return "audio"
    if value == "text":
        return "text"
    return "image"


def _filename_mime(filename: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".txt": "text/plain",
        ".json": "application/json",
    }.get(Path(filename).suffix.lower(), "application/octet-stream")
