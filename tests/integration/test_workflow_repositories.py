"""Workflow graph, run recovery, lineage, and concurrency persistence tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from astraweft.application.providers import CreateProvider
from astraweft.bootstrap.container import build_app_context
from astraweft.domain.task import Artifact
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
from astraweft.infrastructure.database import (
    SQLiteTaskUnitOfWorkFactory,
    SQLiteWorkflowUnitOfWorkFactory,
)
from astraweft.infrastructure.database.workflow_repositories import (
    WorkflowOptimisticConcurrencyError,
)
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.secrets import SecretValue


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workflow_graph_publication_is_round_trip_and_immutable(tmp_path: Path) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Workflow Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = (await context.provider_service.sync_models(provider.id))[0]
        now = context.clock.now()
        workflow = Workflow(
            id=context.ids.new(),
            name="Story pipeline",
            description="A persisted two-node graph",
            current_version_id=None,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        version = WorkflowVersion(
            id=context.ids.new(),
            workflow_id=workflow.id,
            version_no=1,
            status=WorkflowVersionStatus.DRAFT,
            input_schema=_object_schema("prompt"),
            output_schema=_object_schema("result"),
            output_bindings={"result": {"node": "render", "port": "result"}},
            checksum="a" * 64,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        source = WorkflowNode(
            id=context.ids.new(),
            workflow_version_id=version.id,
            node_key="draft",
            node_type=WorkflowNodeType.PROVIDER_MODEL,
            name="Draft",
            provider_id=provider.id,
            model_id=model.id,
            operation=next(iter(model.operations)),
            input_schema=_object_schema("prompt"),
            output_schema=_object_schema("result"),
            input_bindings={"prompt": {"workflow_input": "prompt"}},
            config={},
            continue_on_error=False,
            position_x=20,
            position_y=30,
        )
        target = WorkflowNode(
            id=context.ids.new(),
            workflow_version_id=version.id,
            node_key="render",
            node_type=WorkflowNodeType.TRANSFORM,
            name="Render",
            provider_id=None,
            model_id=None,
            operation=None,
            input_schema=_object_schema("result"),
            output_schema=_object_schema("result"),
            input_bindings={},
            config={"kind": "project", "fields": {"result": "result"}},
            continue_on_error=False,
            position_x=320,
            position_y=30,
        )
        edge = WorkflowEdge(
            id=context.ids.new(),
            workflow_version_id=version.id,
            source_node_id=source.id,
            source_port="result",
            target_node_id=target.id,
            target_port="result",
        )
        factory = SQLiteWorkflowUnitOfWorkFactory(context.database.sessions, context.events)
        async with factory() as uow:
            await uow.definitions.add(workflow)
            await uow.definitions.add_version(version)
            edited = version.with_draft_definition(
                input_schema=version.input_schema,
                output_schema=version.output_schema,
                output_bindings=version.output_bindings,
                checksum="b" * 64,
                at=now + timedelta(seconds=1),
            )
            await uow.definitions.replace_draft_definition(
                edited,
                (source, target),
                (edge,),
                expected_version=version.row_version,
            )
            await uow.commit()

        async with factory() as uow:
            assert await uow.definitions.get(workflow.id) == workflow
            assert await uow.definitions.get_draft(workflow.id) == edited
            assert await uow.definitions.get_nodes(version.id) == (source, target)
            assert await uow.definitions.get_edges(version.id) == (edge,)
            assert await uow.definitions.find_version_by_checksum("b" * 64) == edited

        published = edited.publish(now + timedelta(seconds=2))
        current = workflow.with_current_version(version.id, now + timedelta(seconds=2))
        async with factory() as uow:
            await uow.definitions.update_version(
                published,
                expected_version=edited.row_version,
            )
            await uow.definitions.update(current, expected_version=workflow.row_version)
            await uow.commit()

        async with factory() as uow:
            assert (await uow.definitions.get(workflow.id)) == current
            assert await uow.definitions.list_versions(workflow.id) == (published,)
            with pytest.raises(WorkflowTransitionError, match="immutable"):
                await uow.definitions.replace_draft_definition(
                    published,
                    (source, target),
                    (edge,),
                    expected_version=published.row_version,
                )
            with pytest.raises(WorkflowOptimisticConcurrencyError):
                await uow.definitions.update(current, expected_version=workflow.row_version)
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workflow_run_restart_state_and_artifact_lineage_round_trip(tmp_path: Path) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Run Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = (await context.provider_service.sync_models(provider.id))[0]
        now = context.clock.now()
        workflow, version, node = _single_node_definition(
            context.ids.new,
            now,
            provider.id,
            model.id,
            next(iter(model.operations)),
        )
        run = WorkflowRun(
            id=context.ids.new(),
            workflow_id=workflow.id,
            workflow_version_id=version.id,
            status=WorkflowRunStatus.CREATED,
            input={"prompt": "hello"},
            output=None,
            definition_checksum=version.checksum,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        node_run = NodeRun(
            id=context.ids.new(),
            workflow_run_id=run.id,
            workflow_node_id=node.id,
            node_key=node.node_key,
            status=NodeRunStatus.PENDING,
            resolved_input=None,
            output=None,
            planned_task_id=None,
            task_id=None,
            error_code=None,
            error_message=None,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        artifact = Artifact(
            id=context.ids.new(),
            task_id=None,
            kind="TEXT",
            relative_path=f"2026/07/{run.id}/result.txt",
            mime_type="text/plain",
            size_bytes=5,
            sha256="f" * 64,
            metadata={"workflow_run_id": run.id},
            source_url_redacted=None,
            created_at=now,
        )
        task_factory = SQLiteTaskUnitOfWorkFactory(context.database.sessions, context.events)
        async with task_factory() as uow:
            await uow.artifacts.add(artifact)
            await uow.commit()

        factory = SQLiteWorkflowUnitOfWorkFactory(context.database.sessions, context.events)
        async with factory() as uow:
            await uow.definitions.add(workflow)
            await uow.definitions.add_version(version)
            await uow.definitions.replace_draft_definition(
                version,
                (node,),
                (),
                expected_version=version.row_version,
            )
            await uow.runs.add(run)
            await uow.runs.add_node_runs((node_run,))
            await uow.commit()

        running = run.transition(WorkflowRunStatus.RUNNING, now + timedelta(seconds=1))
        ready = node_run.transition(NodeRunStatus.READY, now + timedelta(seconds=1))
        executing = ready.transition(
            NodeRunStatus.RUNNING,
            now + timedelta(seconds=2),
            resolved_input={"prompt": "hello"},
            planned_task_id="planned-task-1",
        )
        link = ArtifactLink(
            id=context.ids.new(),
            node_run_id=node_run.id,
            artifact_id=artifact.id,
            direction=ArtifactLinkDirection.OUTPUT,
            port_name="result",
            created_at=now + timedelta(seconds=3),
        )
        async with factory() as uow:
            await uow.runs.update(running, expected_version=run.row_version)
            await uow.runs.update_node_run(ready, expected_version=node_run.row_version)
            await uow.runs.update_node_run(executing, expected_version=ready.row_version)
            await uow.runs.add_artifact_link(link)
            await uow.commit()

        async with factory() as uow:
            assert await uow.runs.get(run.id) == running
            assert await uow.runs.list_by_status(frozenset({WorkflowRunStatus.RUNNING})) == (
                running,
            )
            assert await uow.runs.list_node_runs(run.id) == (executing,)
            assert await uow.runs.list_artifact_links(node_run.id) == (link,)
    finally:
        await context.close()


def _object_schema(field: str) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {field: {"type": "string"}},
        "required": [field],
        "additionalProperties": False,
    }


def _single_node_definition(
    new_id: Callable[[], str],
    now: datetime,
    provider_id: str,
    model_id: str,
    operation: str,
) -> tuple[Workflow, WorkflowVersion, WorkflowNode]:
    workflow = Workflow(
        id=new_id(),
        name="Recoverable workflow",
        description="",
        current_version_id=None,
        row_version=1,
        created_at=now,
        updated_at=now,
    )
    version = WorkflowVersion(
        id=new_id(),
        workflow_id=workflow.id,
        version_no=1,
        status=WorkflowVersionStatus.DRAFT,
        input_schema=_object_schema("prompt"),
        output_schema=_object_schema("result"),
        output_bindings={"result": {"node": "generate", "port": "result"}},
        checksum="c" * 64,
        row_version=1,
        created_at=now,
        updated_at=now,
    )
    node = WorkflowNode(
        id=new_id(),
        workflow_version_id=version.id,
        node_key="generate",
        node_type=WorkflowNodeType.PROVIDER_MODEL,
        name="Generate",
        provider_id=provider_id,
        model_id=model_id,
        operation=operation,
        input_schema=_object_schema("prompt"),
        output_schema=_object_schema("result"),
        input_bindings={"prompt": {"workflow_input": "prompt"}},
        config={},
        continue_on_error=False,
        position_x=0,
        position_y=0,
    )
    return workflow, version, node
