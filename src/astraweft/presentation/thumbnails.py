"""Lazy, content-addressed image thumbnail cache for the Artifact library."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader, QPixmap

from astraweft.domain.task import Artifact


class ThumbnailCache:
    """Generate bounded previews only when an image Artifact is selected."""

    def __init__(self, root: Path, *, width: int = 420, height: int = 280) -> None:
        if width < 1 or height < 1:
            raise ValueError("thumbnail dimensions must be positive")
        self._root = root
        self._width = width
        self._height = height

    def pixmap_for(self, artifact: Artifact, source: Path) -> QPixmap | None:
        if not artifact.mime_type.startswith("image/") or not source.is_file():
            return None
        target = self.path_for(artifact)
        cached = QPixmap(str(target)) if target.is_file() else QPixmap()
        if not cached.isNull():
            return cached

        reader = QImageReader(str(source))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            return None
        thumbnail = image.scaled(
            self._width,
            self._height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._root.mkdir(parents=True, exist_ok=True)
        temporary = self._root / f".{target.stem}.{uuid4().hex}.tmp.png"
        try:
            if not thumbnail.save(str(temporary)):
                return QPixmap.fromImage(thumbnail)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return QPixmap.fromImage(thumbnail)

    def path_for(self, artifact: Artifact) -> Path:
        return self._root / f"{artifact.sha256}-{self._width}x{self._height}.png"

    def invalidate(self, artifact: Artifact) -> None:
        self.path_for(artifact).unlink(missing_ok=True)


__all__ = ["ThumbnailCache"]
