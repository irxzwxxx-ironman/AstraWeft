"""End-to-end DAG execution, lineage, cancellation, and restart recovery tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from astraweft.application.providers import CreateProvider
from astraweft.application.workflows import (
    CreateWorkflow,
    SaveWorkflowDraft,
    StartWorkflowRun,
    WorkflowDefinitionSnapshot,
    WorkflowEdgeDraft,
    WorkflowExecutionService,
    WorkflowNodeDraft,
    WorkflowService,
)
from astraweft.bootstrap.container import build_app_context
from astraweft.bootstrap.context import AppContext
from astraweft.domain.provider import Model, Provider
from astraweft.domain.task import TaskStatus
from astraweft.domain.workflow import (
    ArtifactLinkDirection,
    NodeRunStatus,
    WorkflowNodeType,
    WorkflowRunStatus,
)
from astraweft.infrastructure.database import SQLiteWorkflowUnitOfWorkFactory
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.secrets import SecretValue


@pytest.mark.integration
@pytest.mark.asyncio
async def test_two_provider_nodes_execute_in_topological_order(tmp_path: Path) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider, model = await _mock_text_model(context, "Two node Mock")
        definition, execution = _services(context)
        published = await _publish_two_provider_flow(definition, provider.id, model.id)

        started = await execution.start(
            StartWorkflowRun(published.version.id, {"prompt": "Write a constellation"})
        )
        first_wave = await execution.advance(started.run.id)
        first = next(item for item in first_wave.node_runs if item.node_key == "first")
        second = next(item for item in first_wave.node_runs if item.node_key == "second")
        assert first.status is NodeRunStatus.RUNNING
        assert first.task_id == first.planned_task_id
        assert second.status is NodeRunStatus.PENDING
        assert first.task_id is not None

        first_task = await context.task_service.run_until_terminal(first.task_id)
        assert first_task.status is TaskStatus.SUCCESS
        second_wave = await execution.advance(started.run.id)
        first = next(item for item in second_wave.node_runs if item.node_key == "first")
        second = next(item for item in second_wave.node_runs if item.node_key == "second")
        assert first.status is NodeRunStatus.SUCCESS
        assert second.status is NodeRunStatus.RUNNING
        assert second.resolved_input == {"prompt": "Mock response"}
        assert second.task_id is not None

        await context.task_service.run_until_terminal(second.task_id)
        completed = await execution.advance(started.run.id)
        assert completed.run.status is WorkflowRunStatus.SUCCESS
        assert completed.run.output == {"text": "Mock response"}
        assert [item.status for item in completed.node_runs] == [
            NodeRunStatus.SUCCESS,
            NodeRunStatus.SUCCESS,
        ]
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_artifact_edge_materializes_input_and_output_lineage(tmp_path: Path) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Lineage Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = next(
            item
            for item in await context.provider_service.sync_models(provider.id)
            if item.remote_model_id == "mock-image-v1"
        )
        definition, execution = _services(context)
        draft = await definition.create(CreateWorkflow("Artifact lineage"))
        generate = await definition.provider_node_draft(
            node_key="generate",
            name="Generate image",
            provider_id=provider.id,
            model_id=model.id,
            operation="image.generate",
        )
        generate = replace(
            generate,
            input_bindings={"prompt": {"kind": "workflow_input", "name": "prompt"}},
        )
        artifact_schema = _object_schema(
            "artifacts",
            {"type": "array", "items": {"type": "string"}},
        )
        project = WorkflowNodeDraft(
            node_key="project",
            node_type=WorkflowNodeType.TRANSFORM,
            name="Project artifacts",
            provider_id=None,
            model_id=None,
            operation=None,
            input_schema=artifact_schema,
            output_schema=artifact_schema,
            input_bindings={},
            config={"kind": "project", "outputs": {"artifacts": "artifacts"}},
            position_x=320,
        )
        saved = await definition.save_draft(
            SaveWorkflowDraft(
                version_id=draft.version.id,
                expected_row_version=draft.version.row_version,
                input_schema=_object_schema("prompt", {"type": "string"}),
                output_schema=artifact_schema,
                output_bindings={"artifacts": {"node": "project", "port": "artifacts"}},
                nodes=(generate, project),
                edges=(WorkflowEdgeDraft("generate", "artifacts", "project", "artifacts"),),
            )
        )
        assert saved.issues == ()
        published = await definition.publish(saved.version.id)
        run = await execution.start(StartWorkflowRun(published.version.id, {"prompt": "Nebula"}))
        wave = await execution.advance(run.run.id)
        generate_run = next(item for item in wave.node_runs if item.node_key == "generate")
        assert generate_run.task_id is not None
        await context.task_service.run_until_terminal(generate_run.task_id)

        completed = await execution.advance(run.run.id)
        assert completed.run.status is WorkflowRunStatus.SUCCESS
        artifact_ids = completed.run.output["artifacts"] if completed.run.output else ()
        assert isinstance(artifact_ids, (list, tuple))
        assert len(artifact_ids) == 1
        assert {
            (link.direction, link.port_name, link.artifact_id) for link in completed.artifact_links
        } == {
            (ArtifactLinkDirection.OUTPUT, "artifacts", artifact_ids[0]),
            (ArtifactLinkDirection.INPUT, "artifacts", artifact_ids[0]),
        }
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_planned_task_identity_recovers_after_restart_without_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = SessionSecretStore()
    first_context = await build_app_context(tmp_path, secret_store_override=secrets)
    definition, execution = _services(first_context)
    provider, model = await _mock_text_model(first_context, "Recovery Mock")
    published = await _publish_single_provider_flow(definition, provider.id, model.id)
    started = await execution.start(StartWorkflowRun(published.version.id, {"prompt": "Recover"}))
    original_create = first_context.task_service.create

    async def interrupt_after_planning(_command: object) -> object:
        raise RuntimeError("simulated process exit")

    monkeypatch.setattr(first_context.task_service, "create", interrupt_after_planning)
    with pytest.raises(RuntimeError, match="process exit"):
        await execution.advance(started.run.id)
    planned_snapshot = await execution.get_run(started.run.id)
    planned = planned_snapshot.node_runs[0]
    assert planned.status is NodeRunStatus.RUNNING
    assert planned.planned_task_id is not None
    assert planned.task_id is None
    monkeypatch.setattr(first_context.task_service, "create", original_create)
    await first_context.close()

    second_context = await build_app_context(tmp_path, secret_store_override=secrets)
    try:
        _definition, recovered_execution = _services(second_context)
        recovered = await recovered_execution.advance(started.run.id)
        node_run = recovered.node_runs[0]
        assert node_run.task_id == planned.planned_task_id
        assert len(await second_context.task_service.list_tasks()) == 1
        repeated = await recovered_execution.advance(started.run.id)
        assert repeated.node_runs[0].task_id == planned.planned_task_id
        assert len(await second_context.task_service.list_tasks()) == 1
    finally:
        await second_context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cancel_marks_run_nodes_and_queued_task_canceled(tmp_path: Path) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider, model = await _mock_text_model(context, "Cancel Workflow Mock")
        definition, execution = _services(context)
        published = await _publish_single_provider_flow(definition, provider.id, model.id)
        started = await execution.start(StartWorkflowRun(published.version.id, {"prompt": "Stop"}))
        active = await execution.advance(started.run.id)
        task_id = active.node_runs[0].task_id
        assert task_id is not None

        canceled = await execution.cancel(started.run.id)

        assert canceled.run.status is WorkflowRunStatus.CANCELED
        assert canceled.node_runs[0].status is NodeRunStatus.CANCELED
        assert (await context.task_service.get(task_id)).status is TaskStatus.CANCELED
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failed_branch_skips_dependents_but_independent_branch_finishes(
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        failing_provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Failing Branch Mock",
                settings={"mode": "task_failed"},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        healthy_provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Healthy Branch Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        failing_model = next(
            item
            for item in await context.provider_service.sync_models(failing_provider.id)
            if item.remote_model_id == "mock-text-v1"
        )
        healthy_model = next(
            item
            for item in await context.provider_service.sync_models(healthy_provider.id)
            if item.remote_model_id == "mock-text-v1"
        )
        definition, execution = _services(context)
        draft = await definition.create(CreateWorkflow("Failure propagation"))
        failing = await definition.provider_node_draft(
            node_key="failing",
            name="Failing root",
            provider_id=failing_provider.id,
            model_id=failing_model.id,
            operation="text.generate",
        )
        healthy = await definition.provider_node_draft(
            node_key="healthy",
            name="Healthy root",
            provider_id=healthy_provider.id,
            model_id=healthy_model.id,
            operation="text.generate",
        )
        binding = {"prompt": {"kind": "workflow_input", "name": "prompt"}}
        failing = replace(failing, input_bindings=binding)
        healthy = replace(healthy, input_bindings=binding)
        text_schema = _object_schema("text", {"type": "string"})
        dependent = WorkflowNodeDraft(
            node_key="dependent",
            node_type=WorkflowNodeType.TRANSFORM,
            name="Dependent",
            provider_id=None,
            model_id=None,
            operation=None,
            input_schema=text_schema,
            output_schema=text_schema,
            input_bindings={},
            config={"kind": "project", "outputs": {"text": "text"}},
            position_x=320,
        )
        saved = await definition.save_draft(
            SaveWorkflowDraft(
                version_id=draft.version.id,
                expected_row_version=draft.version.row_version,
                input_schema=_object_schema("prompt", {"type": "string"}),
                output_schema=text_schema,
                output_bindings={"text": {"node": "healthy", "port": "text"}},
                nodes=(failing, healthy, dependent),
                edges=(WorkflowEdgeDraft("failing", "text", "dependent", "text"),),
            )
        )
        published = await definition.publish(saved.version.id)
        started = await execution.start(StartWorkflowRun(published.version.id, {"prompt": "x"}))
        active = await execution.advance(started.run.id)
        tasks = [item.task_id for item in active.node_runs if item.task_id is not None]
        assert len(tasks) == 2
        for task_id in tasks:
            await context.task_service.run_until_terminal(task_id)

        completed = await execution.advance(started.run.id)
        statuses = {item.node_key: item.status for item in completed.node_runs}
        assert completed.run.status is WorkflowRunStatus.FAILED
        assert statuses == {
            "dependent": NodeRunStatus.SKIPPED,
            "failing": NodeRunStatus.FAILED,
            "healthy": NodeRunStatus.SUCCESS,
        }
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runtime_coordinators_complete_workflow_without_manual_pumping(
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider, model = await _mock_text_model(context, "Automatic Runtime Mock")
        published = await _publish_single_provider_flow(
            context.workflow_service,
            provider.id,
            model.id,
        )
        context.task_runtime.start()
        context.workflow_runtime.start()
        started = await context.workflow_execution.start(
            StartWorkflowRun(published.version.id, {"prompt": "Automatic"})
        )

        async def wait_for_terminal() -> WorkflowRunStatus:
            while True:
                status = (await context.workflow_execution.get_run(started.run.id)).run.status
                if status.terminal:
                    return status
                await asyncio.sleep(0.01)

        assert await asyncio.wait_for(wait_for_terminal(), timeout=3.0) is WorkflowRunStatus.SUCCESS
    finally:
        await context.close()


def _services(context: AppContext) -> tuple[WorkflowService, WorkflowExecutionService]:
    factory = SQLiteWorkflowUnitOfWorkFactory(context.database.sessions, context.events)
    return (
        WorkflowService(
            uow_factory=factory,
            providers=context.provider_service,
            clock=context.clock,
            ids=context.ids,
        ),
        WorkflowExecutionService(
            uow_factory=factory,
            tasks=context.task_service,
            clock=context.clock,
            ids=context.ids,
        ),
    )


async def _mock_text_model(context: AppContext, name: str) -> tuple[Provider, Model]:
    provider = await context.provider_service.create(
        CreateProvider(
            plugin_id="dev.astraweft.mock-provider",
            name=name,
            settings={},
            credentials={"api_key": SecretValue("mock-valid-key")},
        )
    )
    model = next(
        item
        for item in await context.provider_service.sync_models(provider.id)
        if item.remote_model_id == "mock-text-v1"
    )
    return provider, model


async def _publish_single_provider_flow(
    service: WorkflowService,
    provider_id: str,
    model_id: str,
) -> WorkflowDefinitionSnapshot:
    draft = await service.create(CreateWorkflow("Single provider flow"))
    node = await service.provider_node_draft(
        node_key="generate",
        name="Generate",
        provider_id=provider_id,
        model_id=model_id,
        operation="text.generate",
    )
    node = replace(
        node,
        input_bindings={"prompt": {"kind": "workflow_input", "name": "prompt"}},
    )
    saved = await service.save_draft(
        SaveWorkflowDraft(
            version_id=draft.version.id,
            expected_row_version=draft.version.row_version,
            input_schema=_object_schema("prompt", {"type": "string"}),
            output_schema=_object_schema("text", {"type": "string"}),
            output_bindings={"text": {"node": "generate", "port": "text"}},
            nodes=(node,),
            edges=(),
        )
    )
    return await service.publish(saved.version.id)


async def _publish_two_provider_flow(
    service: WorkflowService,
    provider_id: str,
    model_id: str,
) -> WorkflowDefinitionSnapshot:
    draft = await service.create(CreateWorkflow("Two provider flow"))
    first = await service.provider_node_draft(
        node_key="first",
        name="First",
        provider_id=provider_id,
        model_id=model_id,
        operation="text.generate",
    )
    first = replace(
        first,
        input_bindings={"prompt": {"kind": "workflow_input", "name": "prompt"}},
    )
    second = await service.provider_node_draft(
        node_key="second",
        name="Second",
        provider_id=provider_id,
        model_id=model_id,
        operation="text.generate",
        position_x=320,
    )
    saved = await service.save_draft(
        SaveWorkflowDraft(
            version_id=draft.version.id,
            expected_row_version=draft.version.row_version,
            input_schema=_object_schema("prompt", {"type": "string"}),
            output_schema=_object_schema("text", {"type": "string"}),
            output_bindings={"text": {"node": "second", "port": "text"}},
            nodes=(first, second),
            edges=(WorkflowEdgeDraft("first", "text", "second", "prompt"),),
        )
    )
    assert saved.issues == ()
    return await service.publish(saved.version.id)


def _object_schema(field: str, field_schema: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {field: field_schema},
        "required": [field],
        "additionalProperties": False,
    }
