"""Workflow definition, run, and lineage persistence interfaces."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from astraweft.domain.workflow import (
    ArtifactLink,
    NodeRun,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowVersion,
)


class WorkflowDefinitionRepository(Protocol):
    async def add(self, workflow: Workflow) -> None: ...

    async def get(self, workflow_id: str) -> Workflow | None: ...

    async def list(self, *, limit: int = 1000) -> tuple[Workflow, ...]: ...

    async def update(self, workflow: Workflow, *, expected_version: int) -> None: ...

    async def add_version(self, version: WorkflowVersion) -> None: ...

    async def get_version(self, version_id: str) -> WorkflowVersion | None: ...

    async def get_draft(self, workflow_id: str) -> WorkflowVersion | None: ...

    async def find_version_by_checksum(self, checksum: str) -> WorkflowVersion | None: ...

    async def list_versions(self, workflow_id: str) -> tuple[WorkflowVersion, ...]: ...

    async def update_version(
        self,
        version: WorkflowVersion,
        *,
        expected_version: int,
    ) -> None: ...

    async def replace_draft_definition(
        self,
        version: WorkflowVersion,
        nodes: tuple[WorkflowNode, ...],
        edges: tuple[WorkflowEdge, ...],
        *,
        expected_version: int,
    ) -> None: ...

    async def get_nodes(self, version_id: str) -> tuple[WorkflowNode, ...]: ...

    async def get_edges(self, version_id: str) -> tuple[WorkflowEdge, ...]: ...


class WorkflowRunRepository(Protocol):
    async def add(self, run: WorkflowRun) -> None: ...

    async def get(self, run_id: str) -> WorkflowRun | None: ...

    async def list_recent(self, *, limit: int = 1000) -> tuple[WorkflowRun, ...]: ...

    async def list_by_status(
        self,
        statuses: frozenset[WorkflowRunStatus],
        *,
        limit: int = 1000,
    ) -> tuple[WorkflowRun, ...]: ...

    async def update(self, run: WorkflowRun, *, expected_version: int) -> None: ...

    async def add_node_runs(self, node_runs: tuple[NodeRun, ...]) -> None: ...

    async def get_node_run(self, node_run_id: str) -> NodeRun | None: ...

    async def list_node_runs(self, run_id: str) -> tuple[NodeRun, ...]: ...

    async def update_node_run(self, node_run: NodeRun, *, expected_version: int) -> None: ...

    async def add_artifact_link(self, link: ArtifactLink) -> None: ...

    async def list_artifact_links(self, node_run_id: str) -> tuple[ArtifactLink, ...]: ...


class WorkflowUnitOfWork(Protocol):
    @property
    def definitions(self) -> WorkflowDefinitionRepository: ...

    @property
    def runs(self) -> WorkflowRunRepository: ...

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


class WorkflowUnitOfWorkFactory(Protocol):
    def __call__(self) -> WorkflowUnitOfWork: ...
