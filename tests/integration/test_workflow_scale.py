"""Phase 5 local scale gate for validation and active NodeRun scheduling."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from astraweft.application.workflows import (
    CreateWorkflow,
    SaveWorkflowDraft,
    StartWorkflowRun,
    WorkflowNodeDraft,
)
from astraweft.bootstrap.container import build_app_context
from astraweft.domain.workflow import NodeRunStatus, WorkflowNodeType, WorkflowRunStatus
from astraweft.infrastructure.secrets.store import SessionSecretStore


@pytest.mark.integration
@pytest.mark.asyncio
async def test_thousand_node_dag_validates_and_schedules_in_bounded_waves(
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        service = context.workflow_service
        created = await service.create(CreateWorkflow("Thousand node scale gate"))
        text_schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }
        nodes = tuple(
            WorkflowNodeDraft(
                node_key=f"transform_{index}",
                node_type=WorkflowNodeType.TRANSFORM,
                name=f"Transform {index}",
                provider_id=None,
                model_id=None,
                operation=None,
                input_schema=text_schema,
                output_schema=text_schema,
                input_bindings={"text": {"kind": "constant", "value": str(index)}},
                config={"kind": "project", "outputs": {"text": "text"}},
                position_x=(index % 20) * 260,
                position_y=(index // 20) * 150,
            )
            for index in range(1000)
        )
        saved = await service.save_draft(
            SaveWorkflowDraft(
                version_id=created.version.id,
                expected_row_version=created.version.row_version,
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                output_schema=text_schema,
                output_bindings={"text": {"node": "transform_999", "port": "text"}},
                nodes=nodes,
                edges=(),
            )
        )
        assert saved.issues == ()
        published = await service.publish(saved.version.id)
        started = await context.workflow_execution.start(StartWorkflowRun(published.version.id, {}))

        began = time.perf_counter()
        completed = await context.workflow_execution.advance(started.run.id)
        elapsed = time.perf_counter() - began

        assert completed.run.status is WorkflowRunStatus.SUCCESS
        assert len(completed.node_runs) == 1000
        assert all(item.status is NodeRunStatus.SUCCESS for item in completed.node_runs)
        assert completed.run.output == {"text": "999"}
        assert elapsed < 5.0
    finally:
        await context.close()
