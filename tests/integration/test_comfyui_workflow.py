"""ComfyUI catalog, durable execution, artifact, and Workflow integration."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

import pytest

from astraweft.application.comfyui import (
    ComfyUIInputError,
    CreateComfyUIInstance,
    ImportComfyUITemplate,
)
from astraweft.application.workflows import (
    CreateWorkflow,
    SaveWorkflowDraft,
    StartWorkflowRun,
    WorkflowNodeDraft,
)
from astraweft.bootstrap.container import build_app_context
from astraweft.domain.comfyui import ComfyUIExecutionStatus, ComfyUIHealth, ComfyUIInstance
from astraweft.domain.workflow import WorkflowNodeType, WorkflowRunStatus
from astraweft.infrastructure.database import SQLiteTaskUnitOfWorkFactory
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.artifacts import ArtifactDownloadResult
from astraweft.ports.comfyui import (
    ComfyUIOutputFile,
    ComfyUIProbe,
    ComfyUIRemoteSnapshot,
    ComfyUISubmitResult,
)


class SuccessfulComfyUIClient:
    def __init__(self) -> None:
        self.prompt_id = "remote-prompt-1"
        self.submissions: list[Mapping[str, object]] = []
        self.closed = False

    async def probe(self, _instance: ComfyUIInstance) -> ComfyUIProbe:
        return ComfyUIProbe(
            version="0.3.50",
            python_version="3.12",
            capabilities={"node_count": 2},
            node_catalog_hash="c" * 64,
        )

    async def submit(
        self,
        _instance: ComfyUIInstance,
        *,
        prompt: Mapping[str, object],
        client_id: str,
        execution_id: str,
        workflow_checksum: str,
    ) -> ComfyUISubmitResult:
        self.submissions.append(
            {
                "prompt": prompt,
                "client_id": client_id,
                "execution_id": execution_id,
                "workflow_checksum": workflow_checksum,
            }
        )
        return ComfyUISubmitResult(self.prompt_id, 1)

    async def find_execution(self, _instance: ComfyUIInstance, _execution_id: str) -> str | None:
        return self.prompt_id

    async def snapshot(self, _instance: ComfyUIInstance, _prompt_id: str) -> ComfyUIRemoteSnapshot:
        return ComfyUIRemoteSnapshot(
            status=ComfyUIExecutionStatus.MATERIALIZING,
            progress=100,
            outputs={
                "9": {"images": [{"filename": "astraweft.png", "subfolder": "", "type": "output"}]}
            },
            files=(ComfyUIOutputFile("9", "astraweft.png", "", "output", "image"),),
        )

    async def ensure_progress_watch(
        self,
        _instance: ComfyUIInstance,
        *,
        prompt_id: str,
        client_id: str,
    ) -> None:
        assert prompt_id == self.prompt_id
        assert client_id

    def latest_progress(self, _prompt_id: str) -> int | None:
        return 50

    async def cancel(self, _instance: ComfyUIInstance, _prompt_id: str) -> bool:
        return True

    async def download_output(
        self,
        _instance: ComfyUIInstance,
        _output: ComfyUIOutputFile,
        *,
        target: Path,
        max_bytes: int,
        timeout_seconds: float,
    ) -> ArtifactDownloadResult:
        assert max_bytes > 0
        assert timeout_seconds > 0
        payload = b"comfyui-image"
        target.write_bytes(payload)
        return ArtifactDownloadResult(
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            content_type="image/png",
        )

    async def close(self) -> None:
        self.closed = True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_comfyui_template_runs_inside_published_workflow(tmp_path: Path) -> None:
    client = SuccessfulComfyUIClient()
    context = await build_app_context(
        tmp_path,
        secret_store_override=SessionSecretStore(),
        comfyui_client_override=client,
    )
    try:
        instance = await context.comfyui_service.create_instance(
            CreateComfyUIInstance("Local ComfyUI", "http://127.0.0.1:8188")
        )
        health = await context.comfyui_service.test_connection(instance.id)
        assert health.health is ComfyUIHealth.HEALTHY
        assert health.node_count == 2

        prompt = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "placeholder"}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        }
        input_schema = {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
            "additionalProperties": False,
        }
        template_command = ImportComfyUITemplate(
            instance_id=instance.id,
            name="Poster",
            prompt=prompt,
            input_schema=input_schema,
            input_targets={"prompt": {"node_id": "1", "input_name": "text"}},
            output_nodes=("9",),
        )
        template = await context.comfyui_service.import_template(template_command)
        with pytest.raises(ComfyUIInputError, match="同名"):
            await context.comfyui_service.import_template(template_command)

        created = await context.workflow_service.create(CreateWorkflow("Comfy poster"))
        output_schema = {
            "type": "object",
            "properties": {"artifacts": {"type": "array", "items": {"type": "string"}}},
            "required": ["artifacts"],
            "additionalProperties": False,
        }
        node_output_schema = {
            "type": "object",
            "properties": {
                "data": {"type": "object"},
                "artifacts": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["data", "artifacts"],
            "additionalProperties": False,
        }
        saved = await context.workflow_service.save_draft(
            SaveWorkflowDraft(
                version_id=created.version.id,
                expected_row_version=created.version.row_version,
                input_schema=input_schema,
                output_schema=output_schema,
                output_bindings={"artifacts": {"node": "render", "port": "artifacts"}},
                nodes=(
                    WorkflowNodeDraft(
                        node_key="render",
                        node_type=WorkflowNodeType.COMFYUI,
                        name="Render in ComfyUI",
                        provider_id=None,
                        model_id=None,
                        operation=None,
                        input_schema=template.input_schema,
                        output_schema=node_output_schema,
                        input_bindings={"prompt": {"kind": "workflow_input", "name": "prompt"}},
                        config={
                            "instance_id": instance.id,
                            "template_id": template.id,
                            "template_checksum": template.checksum,
                            "prompt": template.prompt,
                            "input_targets": template.input_targets,
                            "output_nodes": list(template.output_nodes),
                        },
                    ),
                ),
                edges=(),
            )
        )
        assert saved.issues == ()
        published = await context.workflow_service.publish(saved.version.id)
        snapshot = await context.workflow_execution.start(
            StartWorkflowRun(published.version.id, {"prompt": "星际织机"})
        )
        for _ in range(6):
            snapshot = await context.workflow_execution.advance(snapshot.run.id)
            if snapshot.run.status.terminal:
                break

        assert snapshot.run.status is WorkflowRunStatus.SUCCESS
        assert snapshot.run.output is not None
        artifact_ids = snapshot.run.output["artifacts"]
        assert isinstance(artifact_ids, tuple)
        assert len(artifact_ids) == 1
        assert client.submissions[0]["prompt"]["1"]["inputs"]["text"] == "星际织机"  # type: ignore[index]
        executions = await context.comfyui_service.list_executions()
        assert executions[0].status is ComfyUIExecutionStatus.SUCCESS

        task_uow = SQLiteTaskUnitOfWorkFactory(context.database.sessions, context.events)
        async with task_uow() as uow:
            artifacts = await uow.artifacts.list_recent()
        assert artifacts[0].id == artifact_ids[0]
        assert (context.paths.artifact_dir / artifacts[0].relative_path).read_bytes() == (
            b"comfyui-image"
        )
    finally:
        await context.close()
    assert client.closed is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_comfyui_probe_failure_is_persisted(tmp_path: Path) -> None:
    client = SuccessfulComfyUIClient()

    async def fail_probe(_instance: ComfyUIInstance) -> ComfyUIProbe:
        raise OSError("offline")

    client.probe = fail_probe  # type: ignore[method-assign]
    context = await build_app_context(
        tmp_path,
        secret_store_override=SessionSecretStore(),
        comfyui_client_override=client,
    )
    try:
        instance = await context.comfyui_service.create_instance(
            CreateComfyUIInstance("Offline", "http://localhost:8188")
        )
        result = await context.comfyui_service.test_connection(instance.id)
        assert result.health is ComfyUIHealth.UNAVAILABLE
        stored = await context.comfyui_service.get_instance(instance.id)
        assert stored.health is ComfyUIHealth.UNAVAILABLE
        assert stored.last_error_code == "unavailable"
    finally:
        await context.close()
