"""Workflow draft, validation, immutable publication, and import/export tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from astraweft.application.providers import CreateProvider
from astraweft.application.workflows import (
    CreateWorkflow,
    ImportWorkflow,
    SaveWorkflowDraft,
    WorkflowEdgeDraft,
    WorkflowInputError,
    WorkflowNodeDraft,
    WorkflowNotFoundError,
    WorkflowService,
    WorkflowValidationError,
)
from astraweft.bootstrap.container import build_app_context
from astraweft.bootstrap.context import AppContext
from astraweft.domain.workflow import (
    WorkflowNodeType,
    WorkflowTransitionError,
    WorkflowVersionStatus,
)
from astraweft.infrastructure.database import SQLiteWorkflowUnitOfWorkFactory
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.secrets import SecretValue


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workflow_service_publishes_immutable_version_and_clones_next_draft(
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Definition Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        models = await context.provider_service.sync_models(provider.id)
        model = next(item for item in models if item.remote_model_id == "mock-text-v1")
        service = _workflow_service(context)
        created = await service.create(CreateWorkflow("Story loom", "Product workflow"))
        assert {issue.code for issue in created.issues} == {"empty_workflow"}
        with pytest.raises(WorkflowValidationError) as empty_error:
            await service.publish(created.version.id)
        assert {issue.code for issue in empty_error.value.issues} == {"empty_workflow"}
        with pytest.raises(WorkflowInputError, match="已存在"):
            await service.create(CreateWorkflow("Story loom"))

        node = await service.provider_node_draft(
            node_key="generate",
            name="Generate story",
            provider_id=provider.id,
            model_id=model.id,
            operation="text.generate",
        )
        node = replace(
            node,
            input_bindings={"prompt": {"kind": "workflow_input", "name": "prompt"}},
        )
        saved = await service.save_draft(
            SaveWorkflowDraft(
                version_id=created.version.id,
                expected_row_version=created.version.row_version,
                input_schema=_object_schema("prompt"),
                output_schema=_object_schema("text"),
                output_bindings={"text": {"node": "generate", "port": "text"}},
                nodes=(node,),
                edges=(),
            )
        )
        assert saved.issues == ()
        published = await service.publish(saved.version.id)
        assert published.version.status is WorkflowVersionStatus.PUBLISHED
        assert published.workflow.current_version_id == published.version.id

        draft = await service.create_draft(published.workflow.id)
        assert draft.version.status is WorkflowVersionStatus.DRAFT
        assert draft.version.version_no == 2
        assert draft.version.checksum == published.version.checksum
        assert draft.nodes[0].id != published.nodes[0].id
        assert draft.nodes[0].node_key == published.nodes[0].node_key
        assert (await service.get_definition(published.version.id)).version == published.version
        assert await service.validate_version(draft.version.id) == ()
        republished = await service.publish(draft.version.id)
        assert republished.version.version_no == 2
        assert (
            await service.get_definition(published.version.id)
        ).version.status is WorkflowVersionStatus.ARCHIVED
        with pytest.raises(WorkflowTransitionError, match="draft"):
            await service.publish(republished.version.id)
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_export_import_verifies_checksum_and_keeps_import_as_draft(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source = await build_app_context(source_root, secret_store_override=SessionSecretStore())
    target = await build_app_context(target_root, secret_store_override=SessionSecretStore())
    try:
        provider = await source.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Export Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = next(
            item
            for item in await source.provider_service.sync_models(provider.id)
            if item.remote_model_id == "mock-text-v1"
        )
        source_service = _workflow_service(source)
        created = await source_service.create(CreateWorkflow("Portable flow"))
        node = await source_service.provider_node_draft(
            node_key="generate",
            name="Generate",
            provider_id=provider.id,
            model_id=model.id,
            operation="text.generate",
        )
        node = replace(
            node,
            input_bindings={"prompt": {"kind": "workflow_input", "name": "prompt"}},
        )
        saved = await source_service.save_draft(
            SaveWorkflowDraft(
                version_id=created.version.id,
                expected_row_version=created.version.row_version,
                input_schema=_object_schema("prompt"),
                output_schema=_object_schema("text"),
                output_bindings={"text": {"node": "generate", "port": "text"}},
                nodes=(node,),
                edges=(),
            )
        )
        exported = await source_service.export_definition(saved.version.id)

        target_service = _workflow_service(target)
        imported = await target_service.import_definition(ImportWorkflow(exported))
        assert imported.version.status is WorkflowVersionStatus.DRAFT
        assert imported.version.checksum == saved.version.checksum
        assert "provider_missing" in {issue.code for issue in imported.issues}
        assert (await target_service.import_definition(ImportWorkflow(exported))).version.id == (
            imported.version.id
        )

        tampered = json.loads(exported)
        tampered["definition"]["nodes"][0]["name"] = "Tampered"
        with pytest.raises(ValueError, match="checksum"):
            await target_service.import_definition(
                ImportWorkflow(json.dumps(tampered), name="Tampered flow")
            )
    finally:
        await source.close()
        await target.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workflow_service_rejects_invalid_edits_and_reports_runtime_issues(
    tmp_path: Path,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    try:
        service = _workflow_service(context)
        created = await service.create(CreateWorkflow("Boundary flow"))
        assert (await service.list_summaries())[0].editable_version_id == created.version.id
        assert (await service.list_versions(created.workflow.id))[0].id == created.version.id
        assert (await service.create_draft(created.workflow.id)).version.id == created.version.id

        with pytest.raises(WorkflowNotFoundError):
            await service.list_versions("missing")
        with pytest.raises(WorkflowNotFoundError):
            await service.get_definition("missing")
        with pytest.raises(WorkflowNotFoundError):
            await service.create_draft("missing")
        with pytest.raises(WorkflowNotFoundError):
            await service.save_draft(SaveWorkflowDraft("missing", 1, {}, {}, {}, (), ()))
        with pytest.raises(WorkflowInputError, match="其他操作"):
            await service.save_draft(
                SaveWorkflowDraft(
                    created.version.id,
                    999,
                    created.version.input_schema,
                    created.version.output_schema,
                    {},
                    (),
                    (),
                )
            )
        with pytest.raises(WorkflowInputError, match="Provider"):
            await service.provider_node_draft(
                node_key="provider",
                name="Missing",
                provider_id="missing",
                model_id="missing",
                operation="text.generate",
            )

        provider = await context.provider_service.create(
            CreateProvider(
                plugin_id="dev.astraweft.mock-provider",
                name="Boundary Mock",
                settings={},
                credentials={"api_key": SecretValue("mock-valid-key")},
            )
        )
        model = next(
            item
            for item in await context.provider_service.sync_models(provider.id)
            if item.remote_model_id == "mock-text-v1"
        )
        with pytest.raises(WorkflowInputError, match="不支持"):
            await service.provider_node_draft(
                node_key="provider",
                name="Unsupported",
                provider_id=provider.id,
                model_id=model.id,
                operation="video.generate",
            )

        transform = WorkflowNodeDraft(
            node_key="transform",
            node_type=WorkflowNodeType.TRANSFORM,
            name="Unsafe transform",
            provider_id=None,
            model_id=None,
            operation=None,
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {}},
            input_bindings={},
            config={"kind": "shell", "command": "false"},
        )
        base = SaveWorkflowDraft(
            created.version.id,
            created.version.row_version,
            created.version.input_schema,
            created.version.output_schema,
            {},
            (transform,),
            (),
        )
        with pytest.raises(WorkflowInputError, match="key 重复"):
            await service.save_draft(replace(base, nodes=(transform, transform)))
        with pytest.raises(WorkflowInputError, match="identity"):
            await service.save_draft(
                replace(base, nodes=(replace(transform, node_key="Invalid Key"),))
            )
        with pytest.raises(WorkflowInputError, match="不存在的节点"):
            await service.save_draft(
                replace(
                    base,
                    edges=(WorkflowEdgeDraft("transform", "out", "missing", "in"),),
                )
            )
        with pytest.raises(WorkflowInputError, match="标准 JSON"):
            await service.save_draft(
                replace(base, input_schema={"type": "object", "default": float("nan")})
            )

        unsafe = await service.save_draft(
            replace(
                base,
                input_schema={
                    "type": "object",
                    "properties": {},
                    "$ref": "file:///tmp/unsafe.json",
                },
                output_schema={
                    "type": "object",
                    "properties": {"value": {"type": "invalid"}},
                },
            )
        )
        codes = {issue.code for issue in unsafe.issues}
        assert {"schema_ref_unsafe", "schema_invalid", "transform_config"} <= codes

        unsupported = await service.save_draft(
            SaveWorkflowDraft(
                unsafe.version.id,
                unsafe.version.row_version,
                _object_schema("prompt"),
                _object_schema("text"),
                {},
                (
                    replace(
                        transform,
                        node_type=WorkflowNodeType.COMFYUI,
                        config={},
                    ),
                ),
                (),
            )
        )
        assert {"comfyui_config", "comfyui_instance_missing"} <= {
            issue.code for issue in unsupported.issues
        }
    finally:
        await context.close()


def _workflow_service(context: AppContext) -> WorkflowService:
    return WorkflowService(
        uow_factory=SQLiteWorkflowUnitOfWorkFactory(context.database.sessions, context.events),
        providers=context.provider_service,
        clock=context.clock,
        ids=context.ids,
    )


def _object_schema(field: str) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {field: {"type": "string"}},
        "required": [field],
        "additionalProperties": False,
    }
