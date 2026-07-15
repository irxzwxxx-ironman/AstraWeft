"""ComfyUI network and persistence boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self

from astraweft.domain.comfyui import (
    ComfyUIExecution,
    ComfyUIExecutionStatus,
    ComfyUIInstance,
    ComfyUITemplate,
)
from astraweft.domain.task import Artifact
from astraweft.ports.artifacts import ArtifactDownloadResult
from astraweft.ports.tasks import ArtifactRepository


@dataclass(frozen=True, slots=True)
class ComfyUIProbe:
    version: str | None
    python_version: str | None
    capabilities: Mapping[str, object]
    node_catalog_hash: str


@dataclass(frozen=True, slots=True)
class ComfyUISubmitResult:
    prompt_id: str
    queue_number: int | None


@dataclass(frozen=True, slots=True)
class ComfyUIOutputFile:
    node_id: str
    filename: str
    subfolder: str
    folder_type: str
    kind: str


@dataclass(frozen=True, slots=True)
class ComfyUIRemoteSnapshot:
    status: ComfyUIExecutionStatus
    progress: int | None
    outputs: Mapping[str, object]
    files: tuple[ComfyUIOutputFile, ...]
    error_code: str | None = None
    error_message: str | None = None


class ComfyUIClient(Protocol):
    async def probe(self, instance: ComfyUIInstance) -> ComfyUIProbe: ...

    async def submit(
        self,
        instance: ComfyUIInstance,
        *,
        prompt: Mapping[str, object],
        client_id: str,
        execution_id: str,
        workflow_checksum: str,
    ) -> ComfyUISubmitResult: ...

    async def find_execution(
        self,
        instance: ComfyUIInstance,
        execution_id: str,
    ) -> str | None: ...

    async def snapshot(
        self,
        instance: ComfyUIInstance,
        prompt_id: str,
    ) -> ComfyUIRemoteSnapshot: ...

    async def ensure_progress_watch(
        self,
        instance: ComfyUIInstance,
        *,
        prompt_id: str,
        client_id: str,
    ) -> None: ...

    def latest_progress(self, prompt_id: str) -> int | None: ...

    async def cancel(self, instance: ComfyUIInstance, prompt_id: str) -> bool: ...

    async def download_output(
        self,
        instance: ComfyUIInstance,
        output: ComfyUIOutputFile,
        *,
        target: Path,
        max_bytes: int,
        timeout_seconds: float,
    ) -> ArtifactDownloadResult: ...

    async def close(self) -> None: ...


class ComfyUIArtifactWriter(Protocol):
    async def materialize(
        self,
        *,
        owner_id: str,
        artifact_id: str,
        instance: ComfyUIInstance,
        output: ComfyUIOutputFile,
        client: ComfyUIClient,
        created_at: datetime,
    ) -> Artifact: ...


class ComfyUIInstanceRepository(Protocol):
    async def add(self, instance: ComfyUIInstance) -> None: ...

    async def get(
        self,
        instance_id: str,
        *,
        include_deleted: bool = False,
    ) -> ComfyUIInstance | None: ...

    async def list(self, *, include_deleted: bool = False) -> tuple[ComfyUIInstance, ...]: ...

    async def update(self, instance: ComfyUIInstance, *, expected_version: int) -> None: ...


class ComfyUITemplateRepository(Protocol):
    async def add(self, template: ComfyUITemplate) -> None: ...

    async def get(self, template_id: str) -> ComfyUITemplate | None: ...

    async def list_for_instance(self, instance_id: str) -> tuple[ComfyUITemplate, ...]: ...

    async def update(self, template: ComfyUITemplate, *, expected_version: int) -> None: ...


class ComfyUIExecutionRepository(Protocol):
    async def add(self, execution: ComfyUIExecution) -> None: ...

    async def get(self, execution_id: str) -> ComfyUIExecution | None: ...

    async def get_for_node_run(self, node_run_id: str) -> ComfyUIExecution | None: ...

    async def list_by_status(
        self,
        statuses: frozenset[ComfyUIExecutionStatus],
        *,
        limit: int = 1000,
    ) -> tuple[ComfyUIExecution, ...]: ...

    async def list_recent(self, *, limit: int = 1000) -> tuple[ComfyUIExecution, ...]: ...

    async def update(self, execution: ComfyUIExecution, *, expected_version: int) -> None: ...


class ComfyUIUnitOfWork(Protocol):
    @property
    def instances(self) -> ComfyUIInstanceRepository: ...

    @property
    def templates(self) -> ComfyUITemplateRepository: ...

    @property
    def executions(self) -> ComfyUIExecutionRepository: ...

    @property
    def artifacts(self) -> ArtifactRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    def publish_after_commit(self, event: object) -> None: ...


class ComfyUIUnitOfWorkFactory(Protocol):
    def __call__(self) -> ComfyUIUnitOfWork: ...
