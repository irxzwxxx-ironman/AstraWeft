"""Lazy content-addressed Artifact thumbnail cache tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage
from pytestqt.qtbot import QtBot

from astraweft.domain.task import Artifact
from astraweft.presentation.thumbnails import ThumbnailCache


@pytest.mark.gui
def test_thumbnail_cache_generates_once_and_uses_artifact_hash(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    del qtbot
    source = tmp_path / "source.png"
    image = QImage(800, 600, QImage.Format.Format_RGBA8888)
    image.fill(QColor("#36D399"))
    assert image.save(str(source))
    artifact = Artifact(
        id="artifact-image",
        task_id=None,
        kind="IMAGE",
        relative_path="source.png",
        mime_type="image/png",
        size_bytes=source.stat().st_size,
        sha256="a" * 64,
        metadata={},
        source_url_redacted=None,
        created_at=datetime.now(UTC),
    )
    cache = ThumbnailCache(tmp_path / "thumbnails", width=240, height=160)

    first = cache.pixmap_for(artifact, source)
    target = cache.path_for(artifact)
    assert first is not None and not first.isNull()
    assert first.width() <= 240 and first.height() <= 160
    assert target.is_file() and target.name.startswith(artifact.sha256)
    modified = target.stat().st_mtime_ns

    second = cache.pixmap_for(artifact, source)
    assert second is not None and not second.isNull()
    assert target.stat().st_mtime_ns == modified
    cache.invalidate(artifact)
    assert not target.exists()

    with pytest.raises(ValueError, match="positive"):
        ThumbnailCache(tmp_path, width=0)
