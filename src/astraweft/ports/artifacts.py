"""Atomic artifact materialization boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from astraweft.domain.task import Artifact
from astraweft_provider_sdk import RemoteArtifact


class ArtifactWriteError(RuntimeError):
    """A remote artifact could not be materialized safely."""


@dataclass(frozen=True, slots=True)
class ArtifactDownloadResult:
    size_bytes: int
    sha256: str
    content_type: str | None


class ArtifactDownloader(Protocol):
    async def download(
        self,
        url: str,
        *,
        allowed_hosts: tuple[str, ...],
        target: Path,
        max_bytes: int,
        timeout_seconds: float,
        trace_id: str | None = None,
    ) -> ArtifactDownloadResult: ...


class ArtifactWriter(Protocol):
    async def write(
        self,
        *,
        task_id: str,
        artifact_id: str,
        remote: RemoteArtifact,
        created_at: datetime,
        allowed_hosts: tuple[str, ...] = (),
        trace_id: str | None = None,
    ) -> Artifact: ...


class ArtifactLifecycle(Protocol):
    """Reversible filesystem lifecycle for already materialized artifacts."""

    async def exists(self, artifact: Artifact, *, trashed: bool = False) -> bool: ...

    async def move_to_trash(self, artifact: Artifact) -> None: ...

    async def restore_from_trash(self, artifact: Artifact) -> None: ...

    async def purge_from_trash(self, artifact: Artifact) -> None: ...
