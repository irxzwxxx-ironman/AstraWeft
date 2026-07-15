"""Atomic local artifact materialization tests."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from astraweft.infrastructure.artifacts import LocalArtifactWriter
from astraweft.infrastructure.network import CoreHttpClient
from astraweft.ports.artifacts import ArtifactWriteError
from astraweft_provider_sdk import RemoteArtifact

_NOW = datetime(2026, 7, 15, tzinfo=UTC)


@pytest.mark.asyncio
async def test_json_and_base64_artifacts_are_atomic_and_content_verified(
    tmp_path: Path,
) -> None:
    writer = LocalArtifactWriter(tmp_path)
    json_artifact = await writer.write(
        task_id="task-1",
        artifact_id="artifact-json",
        remote=RemoteArtifact(
            kind="json",
            source="json",
            value={"items": [1, 2], "ok": True},
            filename_hint="result.JSON",
        ),
        created_at=_NOW,
    )
    raw = b"\x00\x01binary"
    binary_artifact = await writer.write(
        task_id="task-1",
        artifact_id="artifact-image",
        remote=RemoteArtifact(
            kind="image",
            source="base64",
            value=base64.b64encode(raw).decode(),
            filename_hint="unsafe.long-extension-name",
        ),
        created_at=_NOW,
    )

    json_path = tmp_path / json_artifact.relative_path
    binary_path = tmp_path / binary_artifact.relative_path
    assert json_path.read_text() == '{"items":[1,2],"ok":true}'
    assert json_artifact.mime_type == "application/json"
    assert binary_path.read_bytes() == raw
    assert binary_artifact.relative_path.endswith(".img")
    assert binary_artifact.mime_type == "application/octet-stream"
    assert binary_artifact.sha256 == hashlib.sha256(raw).hexdigest()
    assert not tuple(tmp_path.rglob("*.partial"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remote", "message"),
    [
        (
            RemoteArtifact(kind="image", source="url", value="https://example.invalid/a"),
            "下载器",
        ),
        (
            RemoteArtifact(kind="image", source="base64", value="not-base64"),
            "无法解码",
        ),
        (
            RemoteArtifact(kind="text", source="text", value={"wrong": "shape"}),
            "文本产物内容无效",
        ),
    ],
)
async def test_unsupported_or_invalid_artifacts_fail_without_partial_file(
    tmp_path: Path,
    remote: RemoteArtifact,
    message: str,
) -> None:
    with pytest.raises(ArtifactWriteError, match=message):
        await LocalArtifactWriter(tmp_path).write(
            task_id="task-1",
            artifact_id="artifact-1",
            remote=remote,
            created_at=_NOW,
        )

    assert not tuple(tmp_path.rglob("*.partial"))


@pytest.mark.asyncio
async def test_url_artifact_is_downloaded_atomically_with_redacted_source(
    tmp_path: Path,
) -> None:
    payload = b"offline-video-bytes"
    core = CoreHttpClient(
        user_agent="AstraWeft/test",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "video/mp4", "content-length": str(len(payload))},
                    content=payload,
                    request=request,
                )
            )
        ),
    )
    writer = LocalArtifactWriter(tmp_path, downloader=core)
    try:
        artifact = await writer.write(
            task_id="task-remote",
            artifact_id="artifact-video",
            remote=RemoteArtifact(
                kind="video",
                source="url",
                value="https://assets.example.test/output.mp4?token=TOP_SECRET",
                filename_hint="output.mp4",
            ),
            created_at=_NOW,
            allowed_hosts=("*.example.test",),
            trace_id="trace-artifact",
        )
    finally:
        await core.close()

    assert (tmp_path / artifact.relative_path).read_bytes() == payload
    assert artifact.sha256 == hashlib.sha256(payload).hexdigest()
    assert artifact.mime_type == "video/mp4"
    assert artifact.source_url_redacted == "https://assets.example.test/<redacted>"
    assert "TOP_SECRET" not in repr(artifact)
    assert not tuple(tmp_path.rglob("*.partial"))


@pytest.mark.asyncio
async def test_task_identity_cannot_escape_artifact_root(tmp_path: Path) -> None:
    with pytest.raises(ArtifactWriteError, match="路径越界"):
        await LocalArtifactWriter(tmp_path).write(
            task_id="../../../../escape",
            artifact_id="artifact-1",
            remote=RemoteArtifact(kind="text", source="text", value="safe"),
            created_at=_NOW,
        )


@pytest.mark.asyncio
async def test_artifact_trash_is_reversible_conflict_safe_and_purgeable(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    trash = tmp_path / "trash"
    writer = LocalArtifactWriter(root, trash_root=trash)
    artifact = await writer.write(
        task_id="task-trash",
        artifact_id="artifact-trash",
        remote=RemoteArtifact(kind="text", source="text", value="recoverable"),
        created_at=_NOW,
    )

    assert await writer.exists(artifact)
    assert not await writer.exists(artifact, trashed=True)
    await writer.move_to_trash(artifact)
    assert not await writer.exists(artifact)
    assert await writer.exists(artifact, trashed=True)

    original = root / artifact.relative_path
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_text("conflict", encoding="utf-8")
    with pytest.raises(ArtifactWriteError, match="同名"):
        await writer.restore_from_trash(artifact)
    original.unlink()
    await writer.restore_from_trash(artifact)
    assert original.read_text(encoding="utf-8") == "recoverable"

    await writer.move_to_trash(artifact)
    await writer.purge_from_trash(artifact)
    assert not await writer.exists(artifact, trashed=True)
    await writer.purge_from_trash(artifact)


@pytest.mark.asyncio
async def test_artifact_trash_reports_missing_source_and_missing_restore(tmp_path: Path) -> None:
    writer = LocalArtifactWriter(tmp_path / "artifacts")
    artifact = await writer.write(
        task_id="task-missing",
        artifact_id="artifact-missing",
        remote=RemoteArtifact(kind="text", source="text", value="temporary"),
        created_at=_NOW,
    )
    (writer.root / artifact.relative_path).unlink()

    with pytest.raises(ArtifactWriteError, match="不存在"):
        await writer.move_to_trash(artifact)
    with pytest.raises(ArtifactWriteError, match="回收站文件不存在"):
        await writer.restore_from_trash(artifact)
