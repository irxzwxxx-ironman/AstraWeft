"""SQLAlchemy repositories for immutable workflow definitions and durable runs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from astraweft.domain.workflow import (
    ArtifactLink,
    ArtifactLinkDirection,
    NodeRun,
    NodeRunStatus,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeType,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowTransitionError,
    WorkflowVersion,
    WorkflowVersionStatus,
)
from astraweft.infrastructure.database.models import (
    ArtifactLinkRecord,
    NodeRunRecord,
    WorkflowCurrentVersionRecord,
    WorkflowEdgeRecord,
    WorkflowNodeRecord,
    WorkflowRecord,
    WorkflowRunRecord,
    WorkflowVersionRecord,
)


class WorkflowOptimisticConcurrencyError(RuntimeError):
    """A stale workflow writer attempted to replace newer durable state."""


class SQLWorkflowDefinitionRepository:
    """Persist stable workflow identities and immutable versioned graphs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, workflow: Workflow) -> None:
        self._session.add(_workflow_record(workflow))
        await self._session.flush()
        await self._set_current_version(workflow)

    async def get(self, workflow_id: str) -> Workflow | None:
        row = (
            await self._session.execute(
                select(WorkflowRecord, WorkflowCurrentVersionRecord.version_id)
                .outerjoin(
                    WorkflowCurrentVersionRecord,
                    WorkflowCurrentVersionRecord.workflow_id == WorkflowRecord.id,
                )
                .where(WorkflowRecord.id == workflow_id, WorkflowRecord.deleted_at.is_(None))
            )
        ).one_or_none()
        return None if row is None else _workflow_entity(row[0], row[1])

    async def list(self, *, limit: int = 1000) -> tuple[Workflow, ...]:
        rows = (
            await self._session.execute(
                select(WorkflowRecord, WorkflowCurrentVersionRecord.version_id)
                .outerjoin(
                    WorkflowCurrentVersionRecord,
                    WorkflowCurrentVersionRecord.workflow_id == WorkflowRecord.id,
                )
                .where(WorkflowRecord.deleted_at.is_(None))
                .order_by(WorkflowRecord.updated_at.desc(), WorkflowRecord.name)
                .limit(limit)
            )
        ).all()
        return tuple(_workflow_entity(record, version_id) for record, version_id in rows)

    async def update(self, workflow: Workflow, *, expected_version: int) -> None:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(WorkflowRecord)
                .where(
                    WorkflowRecord.id == workflow.id,
                    WorkflowRecord.row_version == expected_version,
                )
                .values(**_workflow_values(workflow))
            ),
        )
        if result.rowcount != 1:
            raise WorkflowOptimisticConcurrencyError("Workflow was updated by another operation")
        await self._set_current_version(workflow)

    async def add_version(self, version: WorkflowVersion) -> None:
        self._session.add(_version_record(version))
        await self._session.flush()

    async def get_version(self, version_id: str) -> WorkflowVersion | None:
        record = await self._session.get(WorkflowVersionRecord, version_id)
        return None if record is None else _version_entity(record)

    async def get_draft(self, workflow_id: str) -> WorkflowVersion | None:
        record = await self._session.scalar(
            select(WorkflowVersionRecord).where(
                WorkflowVersionRecord.workflow_id == workflow_id,
                WorkflowVersionRecord.status == WorkflowVersionStatus.DRAFT.value,
            )
        )
        return None if record is None else _version_entity(record)

    async def find_version_by_checksum(self, checksum: str) -> WorkflowVersion | None:
        record = await self._session.scalar(
            select(WorkflowVersionRecord)
            .where(WorkflowVersionRecord.checksum == checksum)
            .order_by(WorkflowVersionRecord.created_at.desc())
            .limit(1)
        )
        return None if record is None else _version_entity(record)

    async def list_versions(self, workflow_id: str) -> tuple[WorkflowVersion, ...]:
        records = (
            await self._session.scalars(
                select(WorkflowVersionRecord)
                .where(WorkflowVersionRecord.workflow_id == workflow_id)
                .order_by(WorkflowVersionRecord.version_no.desc())
            )
        ).all()
        return tuple(_version_entity(record) for record in records)

    async def update_version(
        self,
        version: WorkflowVersion,
        *,
        expected_version: int,
    ) -> None:
        existing = await self._session.get(WorkflowVersionRecord, version.id)
        if existing is None:
            raise WorkflowOptimisticConcurrencyError("Workflow version no longer exists")
        if existing.status != WorkflowVersionStatus.DRAFT.value:
            allowed_publish = (
                existing.status == WorkflowVersionStatus.PUBLISHED.value
                and version.status is WorkflowVersionStatus.ARCHIVED
            )
            if not allowed_publish:
                raise WorkflowTransitionError("published workflow version is immutable")
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(WorkflowVersionRecord)
                .where(
                    WorkflowVersionRecord.id == version.id,
                    WorkflowVersionRecord.row_version == expected_version,
                )
                .values(**_version_values(version))
            ),
        )
        if result.rowcount != 1:
            raise WorkflowOptimisticConcurrencyError(
                "Workflow version was updated by another operation"
            )

    async def replace_draft_definition(
        self,
        version: WorkflowVersion,
        nodes: tuple[WorkflowNode, ...],
        edges: tuple[WorkflowEdge, ...],
        *,
        expected_version: int,
    ) -> None:
        existing = await self._session.get(WorkflowVersionRecord, version.id)
        if existing is None:
            raise WorkflowOptimisticConcurrencyError("Workflow draft no longer exists")
        if existing.status != WorkflowVersionStatus.DRAFT.value:
            raise WorkflowTransitionError("published workflow version is immutable")
        await self.update_version(version, expected_version=expected_version)
        await self._session.execute(
            delete(WorkflowEdgeRecord).where(WorkflowEdgeRecord.workflow_version_id == version.id)
        )
        await self._session.execute(
            delete(WorkflowNodeRecord).where(WorkflowNodeRecord.workflow_version_id == version.id)
        )
        self._session.add_all(_node_record(node) for node in nodes)
        await self._session.flush()
        self._session.add_all(_edge_record(edge) for edge in edges)

    async def get_nodes(self, version_id: str) -> tuple[WorkflowNode, ...]:
        records = (
            await self._session.scalars(
                select(WorkflowNodeRecord)
                .where(WorkflowNodeRecord.workflow_version_id == version_id)
                .order_by(WorkflowNodeRecord.node_key)
            )
        ).all()
        return tuple(_node_entity(record) for record in records)

    async def get_edges(self, version_id: str) -> tuple[WorkflowEdge, ...]:
        records = (
            await self._session.scalars(
                select(WorkflowEdgeRecord)
                .where(WorkflowEdgeRecord.workflow_version_id == version_id)
                .order_by(
                    WorkflowEdgeRecord.target_node_id,
                    WorkflowEdgeRecord.target_port,
                    WorkflowEdgeRecord.source_node_id,
                )
            )
        ).all()
        return tuple(_edge_entity(record) for record in records)

    async def _set_current_version(self, workflow: Workflow) -> None:
        if workflow.current_version_id is None:
            await self._session.execute(
                delete(WorkflowCurrentVersionRecord).where(
                    WorkflowCurrentVersionRecord.workflow_id == workflow.id
                )
            )
            return
        statement = sqlite_insert(WorkflowCurrentVersionRecord).values(
            workflow_id=workflow.id,
            version_id=workflow.current_version_id,
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[WorkflowCurrentVersionRecord.workflow_id],
                set_={"version_id": workflow.current_version_id},
            )
        )


class SQLWorkflowRunRepository:
    """Persist restart-safe run/node state and port-level Artifact lineage."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, run: WorkflowRun) -> None:
        self._session.add(_run_record(run))
        await self._session.flush()

    async def get(self, run_id: str) -> WorkflowRun | None:
        record = await self._session.get(WorkflowRunRecord, run_id)
        return None if record is None else _run_entity(record)

    async def list_recent(self, *, limit: int = 1000) -> tuple[WorkflowRun, ...]:
        records = (
            await self._session.scalars(
                select(WorkflowRunRecord).order_by(WorkflowRunRecord.created_at.desc()).limit(limit)
            )
        ).all()
        return tuple(_run_entity(record) for record in records)

    async def list_by_status(
        self,
        statuses: frozenset[WorkflowRunStatus],
        *,
        limit: int = 1000,
    ) -> tuple[WorkflowRun, ...]:
        if not statuses:
            return ()
        records = (
            await self._session.scalars(
                select(WorkflowRunRecord)
                .where(WorkflowRunRecord.status.in_([status.value for status in statuses]))
                .order_by(WorkflowRunRecord.created_at)
                .limit(limit)
            )
        ).all()
        return tuple(_run_entity(record) for record in records)

    async def update(self, run: WorkflowRun, *, expected_version: int) -> None:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(WorkflowRunRecord)
                .where(
                    WorkflowRunRecord.id == run.id,
                    WorkflowRunRecord.row_version == expected_version,
                )
                .values(**_run_values(run))
            ),
        )
        if result.rowcount != 1:
            raise WorkflowOptimisticConcurrencyError("Workflow run was updated by another worker")

    async def add_node_runs(self, node_runs: tuple[NodeRun, ...]) -> None:
        self._session.add_all(_node_run_record(node_run) for node_run in node_runs)

    async def get_node_run(self, node_run_id: str) -> NodeRun | None:
        record = await self._session.get(NodeRunRecord, node_run_id)
        return None if record is None else _node_run_entity(record)

    async def list_node_runs(self, run_id: str) -> tuple[NodeRun, ...]:
        records = (
            await self._session.scalars(
                select(NodeRunRecord)
                .where(NodeRunRecord.workflow_run_id == run_id)
                .order_by(NodeRunRecord.created_at, NodeRunRecord.node_key)
            )
        ).all()
        return tuple(_node_run_entity(record) for record in records)

    async def update_node_run(self, node_run: NodeRun, *, expected_version: int) -> None:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(NodeRunRecord)
                .where(
                    NodeRunRecord.id == node_run.id,
                    NodeRunRecord.row_version == expected_version,
                )
                .values(**_node_run_values(node_run))
            ),
        )
        if result.rowcount != 1:
            raise WorkflowOptimisticConcurrencyError("Node run was updated by another worker")

    async def add_artifact_link(self, link: ArtifactLink) -> None:
        self._session.add(_artifact_link_record(link))

    async def list_artifact_links(self, node_run_id: str) -> tuple[ArtifactLink, ...]:
        records = (
            await self._session.scalars(
                select(ArtifactLinkRecord)
                .where(ArtifactLinkRecord.node_run_id == node_run_id)
                .order_by(
                    ArtifactLinkRecord.direction,
                    ArtifactLinkRecord.port_name,
                    ArtifactLinkRecord.created_at,
                )
            )
        ).all()
        return tuple(_artifact_link_entity(record) for record in records)


def _workflow_record(workflow: Workflow) -> WorkflowRecord:
    return WorkflowRecord(**_workflow_values(workflow))


def _workflow_values(workflow: Workflow) -> dict[str, object]:
    return {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description,
        "row_version": workflow.row_version,
        "created_at": _time(workflow.created_at),
        "updated_at": _time(workflow.updated_at),
        "deleted_at": _optional_time(workflow.deleted_at),
    }


def _workflow_entity(record: WorkflowRecord, version_id: str | None) -> Workflow:
    return Workflow(
        id=record.id,
        name=record.name,
        description=record.description,
        current_version_id=version_id,
        row_version=record.row_version,
        created_at=_parse_time(record.created_at),
        updated_at=_parse_time(record.updated_at),
        deleted_at=_optional_parse_time(record.deleted_at),
    )


def _version_record(version: WorkflowVersion) -> WorkflowVersionRecord:
    return WorkflowVersionRecord(**_version_values(version))


def _version_values(version: WorkflowVersion) -> dict[str, object]:
    return {
        "id": version.id,
        "workflow_id": version.workflow_id,
        "version_no": version.version_no,
        "status": version.status.value,
        "input_schema_json": _dump_json(version.input_schema),
        "output_schema_json": _dump_json(version.output_schema),
        "output_bindings_json": _dump_json(version.output_bindings),
        "checksum": version.checksum,
        "row_version": version.row_version,
        "created_at": _time(version.created_at),
        "updated_at": _time(version.updated_at),
        "published_at": _optional_time(version.published_at),
    }


def _version_entity(record: WorkflowVersionRecord) -> WorkflowVersion:
    return WorkflowVersion(
        id=record.id,
        workflow_id=record.workflow_id,
        version_no=record.version_no,
        status=WorkflowVersionStatus(record.status),
        input_schema=_load_mapping(record.input_schema_json),
        output_schema=_load_mapping(record.output_schema_json),
        output_bindings=_load_mapping(record.output_bindings_json),
        checksum=record.checksum,
        row_version=record.row_version,
        created_at=_parse_time(record.created_at),
        updated_at=_parse_time(record.updated_at),
        published_at=_optional_parse_time(record.published_at),
    )


def _node_record(node: WorkflowNode) -> WorkflowNodeRecord:
    return WorkflowNodeRecord(
        id=node.id,
        workflow_version_id=node.workflow_version_id,
        node_key=node.node_key,
        node_type=node.node_type.value,
        name=node.name,
        provider_id=node.provider_id,
        model_id=node.model_id,
        operation=node.operation,
        input_schema_json=_dump_json(node.input_schema),
        output_schema_json=_dump_json(node.output_schema),
        input_bindings_json=_dump_json(node.input_bindings),
        config_json=_dump_json(node.config),
        continue_on_error=node.continue_on_error,
        position_x=node.position_x,
        position_y=node.position_y,
    )


def _node_entity(record: WorkflowNodeRecord) -> WorkflowNode:
    return WorkflowNode(
        id=record.id,
        workflow_version_id=record.workflow_version_id,
        node_key=record.node_key,
        node_type=WorkflowNodeType(record.node_type),
        name=record.name,
        provider_id=record.provider_id,
        model_id=record.model_id,
        operation=record.operation,
        input_schema=_load_mapping(record.input_schema_json),
        output_schema=_load_mapping(record.output_schema_json),
        input_bindings=_load_mapping(record.input_bindings_json),
        config=_load_mapping(record.config_json),
        continue_on_error=record.continue_on_error,
        position_x=record.position_x,
        position_y=record.position_y,
    )


def _edge_record(edge: WorkflowEdge) -> WorkflowEdgeRecord:
    return WorkflowEdgeRecord(
        id=edge.id,
        workflow_version_id=edge.workflow_version_id,
        source_node_id=edge.source_node_id,
        source_port=edge.source_port,
        target_node_id=edge.target_node_id,
        target_port=edge.target_port,
    )


def _edge_entity(record: WorkflowEdgeRecord) -> WorkflowEdge:
    return WorkflowEdge(
        id=record.id,
        workflow_version_id=record.workflow_version_id,
        source_node_id=record.source_node_id,
        source_port=record.source_port,
        target_node_id=record.target_node_id,
        target_port=record.target_port,
    )


def _run_record(run: WorkflowRun) -> WorkflowRunRecord:
    return WorkflowRunRecord(**_run_values(run))


def _run_values(run: WorkflowRun) -> dict[str, object]:
    return {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "workflow_version_id": run.workflow_version_id,
        "status": run.status.value,
        "input_json": _dump_json(run.input),
        "output_json": _optional_json(run.output),
        "definition_checksum": run.definition_checksum,
        "row_version": run.row_version,
        "created_at": _time(run.created_at),
        "updated_at": _time(run.updated_at),
        "started_at": _optional_time(run.started_at),
        "completed_at": _optional_time(run.completed_at),
        "cancel_requested_at": _optional_time(run.cancel_requested_at),
    }


def _run_entity(record: WorkflowRunRecord) -> WorkflowRun:
    return WorkflowRun(
        id=record.id,
        workflow_id=record.workflow_id,
        workflow_version_id=record.workflow_version_id,
        status=WorkflowRunStatus(record.status),
        input=_load_mapping(record.input_json),
        output=_optional_mapping(record.output_json),
        definition_checksum=record.definition_checksum,
        row_version=record.row_version,
        created_at=_parse_time(record.created_at),
        updated_at=_parse_time(record.updated_at),
        started_at=_optional_parse_time(record.started_at),
        completed_at=_optional_parse_time(record.completed_at),
        cancel_requested_at=_optional_parse_time(record.cancel_requested_at),
    )


def _node_run_record(node_run: NodeRun) -> NodeRunRecord:
    return NodeRunRecord(**_node_run_values(node_run))


def _node_run_values(node_run: NodeRun) -> dict[str, object]:
    return {
        "id": node_run.id,
        "workflow_run_id": node_run.workflow_run_id,
        "workflow_node_id": node_run.workflow_node_id,
        "node_key": node_run.node_key,
        "status": node_run.status.value,
        "resolved_input_json": _optional_json(node_run.resolved_input),
        "output_json": _optional_json(node_run.output),
        "planned_task_id": node_run.planned_task_id,
        "task_id": node_run.task_id,
        "planned_comfyui_execution_id": node_run.planned_comfyui_execution_id,
        "comfyui_execution_id": node_run.comfyui_execution_id,
        "error_code": node_run.error_code,
        "error_message": node_run.error_message,
        "row_version": node_run.row_version,
        "created_at": _time(node_run.created_at),
        "updated_at": _time(node_run.updated_at),
        "started_at": _optional_time(node_run.started_at),
        "completed_at": _optional_time(node_run.completed_at),
    }


def _node_run_entity(record: NodeRunRecord) -> NodeRun:
    return NodeRun(
        id=record.id,
        workflow_run_id=record.workflow_run_id,
        workflow_node_id=record.workflow_node_id,
        node_key=record.node_key,
        status=NodeRunStatus(record.status),
        resolved_input=_optional_mapping(record.resolved_input_json),
        output=_optional_mapping(record.output_json),
        planned_task_id=record.planned_task_id,
        task_id=record.task_id,
        planned_comfyui_execution_id=record.planned_comfyui_execution_id,
        comfyui_execution_id=record.comfyui_execution_id,
        error_code=record.error_code,
        error_message=record.error_message,
        row_version=record.row_version,
        created_at=_parse_time(record.created_at),
        updated_at=_parse_time(record.updated_at),
        started_at=_optional_parse_time(record.started_at),
        completed_at=_optional_parse_time(record.completed_at),
    )


def _artifact_link_record(link: ArtifactLink) -> ArtifactLinkRecord:
    return ArtifactLinkRecord(
        id=link.id,
        node_run_id=link.node_run_id,
        artifact_id=link.artifact_id,
        direction=link.direction.value,
        port_name=link.port_name,
        created_at=_time(link.created_at),
    )


def _artifact_link_entity(record: ArtifactLinkRecord) -> ArtifactLink:
    return ArtifactLink(
        id=record.id,
        node_run_id=record.node_run_id,
        artifact_id=record.artifact_id,
        direction=ArtifactLinkDirection(record.direction),
        port_name=record.port_name,
        created_at=_parse_time(record.created_at),
    )


def _dump_json(value: object) -> str:
    def thaw(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): thaw(child) for key, child in item.items()}
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            return [thaw(child) for child in item]
        return item

    return json.dumps(thaw(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _optional_json(value: Mapping[str, object] | None) -> str | None:
    return None if value is None else _dump_json(value)


def _load_mapping(value: str) -> Mapping[str, object]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ValueError("database JSON value is not an object")
    return cast(dict[str, object], loaded)


def _optional_mapping(value: str | None) -> Mapping[str, object] | None:
    return None if value is None else _load_mapping(value)


def _time(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("database timestamps must be timezone-aware")
    return value.isoformat()


def _optional_time(value: datetime | None) -> str | None:
    return None if value is None else _time(value)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("database timestamp has no timezone")
    return parsed


def _optional_parse_time(value: str | None) -> datetime | None:
    return None if value is None else _parse_time(value)
