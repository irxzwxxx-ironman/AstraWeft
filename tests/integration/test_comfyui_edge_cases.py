"""ComfyUI reconciliation, cancellation, failure, and catalog edge cases."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from astraweft.application.comfyui import (
    ComfyUIInputError,
    ComfyUIInstanceNotFoundError,
    ComfyUIOperationError,
    ComfyUITemplateNotFoundError,
    CreateComfyUIInstance,
    EnsureComfyUIExecution,
    ImportComfyUITemplate,
    UpdateComfyUIInstance,
)
from astraweft.bootstrap.container import build_app_context
from astraweft.domain.comfyui import (
    ComfyUIExecutionStatus,
    ComfyUIHealth,
    ComfyUIInstance,
)
from astraweft.domain.workflow import (
    NodeRun,
    NodeRunStatus,
    Workflow,
    WorkflowNode,
    WorkflowNodeType,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowVersion,
    WorkflowVersionStatus,
)
from astraweft.infrastructure.database import (
    SQLiteComfyUIUnitOfWorkFactory,
    SQLiteWorkflowUnitOfWorkFactory,
)
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.artifacts import ArtifactDownloadResult
from astraweft.ports.comfyui import (
    ComfyUIOutputFile,
    ComfyUIProbe,
    ComfyUIRemoteSnapshot,
    ComfyUISubmitResult,
)


class ControlledComfyUIClient:
    def __init__(self) -> None:
        self.submit_error = False
        self.find_result: str | None = None
        self.find_error = False
        self.snapshot_result = ComfyUIRemoteSnapshot(ComfyUIExecutionStatus.QUEUED, 10, {}, ())
        self.snapshot_error = False
        self.cancel_error = False
        self.download_error = False
        self.submit_count = 0
        self.watch_count = 0

    async def probe(self, _instance: ComfyUIInstance) -> ComfyUIProbe:
        return ComfyUIProbe("test", "3.12", {"node_count": 2}, "a" * 64)

    async def submit(
        self,
        _instance: ComfyUIInstance,
        *,
        prompt: Mapping[str, object],
        client_id: str,
        execution_id: str,
        workflow_checksum: str,
    ) -> ComfyUISubmitResult:
        del prompt, client_id, workflow_checksum
        self.submit_count += 1
        if self.submit_error:
            raise OSError("uncertain")
        return ComfyUISubmitResult(f"prompt-{execution_id}", 1)

    async def find_execution(self, _instance: ComfyUIInstance, _execution_id: str) -> str | None:
        if self.find_error:
            raise OSError("offline")
        return self.find_result

    async def snapshot(self, _instance: ComfyUIInstance, _prompt_id: str) -> ComfyUIRemoteSnapshot:
        if self.snapshot_error:
            raise OSError("offline")
        return self.snapshot_result

    async def ensure_progress_watch(
        self,
        _instance: ComfyUIInstance,
        *,
        prompt_id: str,
        client_id: str,
    ) -> None:
        assert prompt_id and client_id
        self.watch_count += 1

    def latest_progress(self, _prompt_id: str) -> int | None:
        return 33

    async def cancel(self, _instance: ComfyUIInstance, _prompt_id: str) -> bool:
        if self.cancel_error:
            raise OSError("cancel uncertain")
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
        del max_bytes, timeout_seconds
        if self.download_error:
            raise OSError("download failed")
        payload = b"artifact"
        target.write_bytes(payload)
        return ArtifactDownloadResult(
            len(payload), hashlib.sha256(payload).hexdigest(), "image/png"
        )

    async def close(self) -> None:
        return None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_comfyui_catalog_validation_and_soft_delete(tmp_path: Path) -> None:
    client = ControlledComfyUIClient()
    context = await build_app_context(
        tmp_path,
        secret_store_override=SessionSecretStore(),
        comfyui_client_override=client,
    )
    service = context.comfyui_service
    try:
        first = await service.create_instance(
            CreateComfyUIInstance("Local", "http://localhost:8188")
        )
        with pytest.raises(ComfyUIInputError, match="名称已存在"):
            await service.create_instance(CreateComfyUIInstance(" local ", "http://localhost:8189"))
        with pytest.raises(ComfyUIInputError, match="名称不能为空"):
            await service.create_instance(CreateComfyUIInstance("", "http://localhost:8189"))
        with pytest.raises(ComfyUIInputError, match="地址"):
            await service.create_instance(
                CreateComfyUIInstance("Unsafe", "http://192.168.1.20:8188")
            )
        updated = await service.update_instance(
            UpdateComfyUIInstance(first.id, "Renamed", "http://127.0.0.1:8188", False)
        )
        assert updated.name == "Renamed"
        assert updated.enabled is False
        enabled = await service.set_enabled(first.id, True)
        assert enabled.enabled is True
        result = await service.test_connection(first.id)
        assert result.health is ComfyUIHealth.HEALTHY

        prompt = {"1": {"class_type": "SaveImage", "inputs": {"images": "x"}}}
        with pytest.raises(ComfyUIInputError, match="API 格式"):
            await service.import_template(
                ImportComfyUITemplate(
                    first.id,
                    "Invalid prompt",
                    {},
                    {"type": "object", "properties": {}},
                    {},
                    ("1",),
                )
            )
        with pytest.raises(ComfyUIInputError, match="必须是对象"):
            await service.import_template(
                ImportComfyUITemplate(
                    first.id,
                    "Invalid schema",
                    prompt,
                    {"type": "array"},
                    {},
                    ("1",),
                )
            )
        with pytest.raises(ComfyUIInputError, match="映射无效"):
            await service.import_template(
                ImportComfyUITemplate(
                    first.id,
                    "Invalid target",
                    prompt,
                    {"type": "object", "properties": {"prompt": {"type": "string"}}},
                    {"prompt": "bad"},
                    ("1",),
                )
            )
        with pytest.raises(ComfyUIInputError, match="不存在"):
            await service.import_template(
                ImportComfyUITemplate(
                    first.id,
                    "Missing input",
                    prompt,
                    {"type": "object", "properties": {"prompt": {"type": "string"}}},
                    {"prompt": {"node_id": "1", "input_name": "missing"}},
                    ("1",),
                )
            )
        with pytest.raises(ComfyUIInputError, match="映射"):
            await service.import_template(
                ImportComfyUITemplate(
                    first.id,
                    "Bad",
                    prompt,
                    {
                        "type": "object",
                        "properties": {"prompt": {"type": "string"}},
                    },
                    {},
                    ("1",),
                )
            )
        with pytest.raises(ComfyUIInputError, match="输出"):
            await service.import_template(
                ImportComfyUITemplate(
                    first.id,
                    "Bad output",
                    prompt,
                    {"type": "object", "properties": {}},
                    {},
                    ("404",),
                )
            )
        with pytest.raises(ComfyUIOperationError, match="执行记录"):
            await service.get_execution("missing")
        await service.delete_instance(first.id)
        assert await service.list_instances() == ()
        with pytest.raises(ComfyUIInstanceNotFoundError):
            await service.get_instance(first.id)
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_comfyui_execution_reconciliation_cancel_and_failure_paths(
    tmp_path: Path,
) -> None:
    client = ControlledComfyUIClient()
    context = await build_app_context(
        tmp_path,
        secret_store_override=SessionSecretStore(),
        comfyui_client_override=client,
    )
    service = context.comfyui_service
    try:
        instance = await service.create_instance(
            CreateComfyUIInstance("Runtime", "http://localhost:8188")
        )
        prompt = {"1": {"class_type": "Text", "inputs": {"text": "old"}}}
        template = await service.import_template(
            ImportComfyUITemplate(
                instance.id,
                "Runtime template",
                prompt,
                {
                    "type": "object",
                    "properties": {"prompt": {"type": "string"}},
                    "required": ["prompt"],
                },
                {"prompt": {"node_id": "1", "input_name": "text"}},
                ("1",),
            )
        )
        node_run_ids = await _create_node_runs(context, 10)

        queued = await service.ensure_execution(
            _command(node_run_ids[0], "execution-0", instance.id, template)
        )
        queued = await service.advance_execution(queued.id)
        assert queued.status is ComfyUIExecutionStatus.QUEUED
        assert client.submit_count == 1
        refreshed = await service.advance_execution(queued.id)
        assert refreshed.status is ComfyUIExecutionStatus.QUEUED
        assert refreshed.progress == 10
        client.snapshot_result = ComfyUIRemoteSnapshot(ComfyUIExecutionStatus.RUNNING, 45, {}, ())
        running = await service.advance_execution(queued.id)
        assert running.status is ComfyUIExecutionStatus.RUNNING
        client.snapshot_error = True
        offline = await service.advance_execution(queued.id)
        assert offline.progress == 33
        client.snapshot_error = False
        client.snapshot_result = ComfyUIRemoteSnapshot(
            ComfyUIExecutionStatus.FAILED,
            50,
            {},
            (),
            "remote_failed",
            "remote failed",
        )
        failed = await service.advance_execution(queued.id)
        assert failed.status is ComfyUIExecutionStatus.FAILED
        assert failed.error_code == "remote_failed"

        recovering = await service.ensure_execution(
            _command(node_run_ids[1], "execution-1", instance.id, template)
        )
        submitting = recovering.transition(
            ComfyUIExecutionStatus.SUBMITTING,
            context.clock.now(),
        )
        factory = SQLiteComfyUIUnitOfWorkFactory(context.database.sessions, context.events)
        async with factory() as uow:
            await uow.executions.update(submitting, expected_version=recovering.row_version)
            await uow.commit()
        client.find_result = "recovered-prompt"
        recovered = await service.advance_execution(recovering.id)
        assert recovered.remote_prompt_id == "recovered-prompt"
        assert client.submit_count == 1

        client.submit_error = True
        uncertain = await service.ensure_execution(
            _command(node_run_ids[2], "execution-2", instance.id, template)
        )
        uncertain = await service.advance_execution(uncertain.id)
        assert uncertain.status is ComfyUIExecutionStatus.NEEDS_ATTENTION
        assert uncertain.error_code == "submission_outcome_unknown"
        client.submit_error = False

        planned_cancel = await service.ensure_execution(
            _command(node_run_ids[3], "execution-3", instance.id, template)
        )
        planned_cancel = await service.cancel_execution(planned_cancel.id)
        assert planned_cancel.status is ComfyUIExecutionStatus.CANCELED

        remote_cancel = await service.ensure_execution(
            _command(node_run_ids[4], "execution-4", instance.id, template)
        )
        remote_cancel = await service.advance_execution(remote_cancel.id)
        remote_cancel = await service.cancel_execution(remote_cancel.id)
        assert remote_cancel.status is ComfyUIExecutionStatus.CANCELING
        client.snapshot_result = ComfyUIRemoteSnapshot(
            ComfyUIExecutionStatus.FAILED,
            None,
            {},
            (),
            "remote_execution_interrupted",
            "interrupted",
        )
        canceled = await service.advance_execution(remote_cancel.id)
        assert canceled.status is ComfyUIExecutionStatus.CANCELED

        no_files = await service.ensure_execution(
            _command(node_run_ids[5], "execution-5", instance.id, template)
        )
        no_files = await service.advance_execution(no_files.id)
        client.snapshot_result = ComfyUIRemoteSnapshot(
            ComfyUIExecutionStatus.MATERIALIZING,
            100,
            {"1": {}},
            (),
        )
        no_files = await service.advance_execution(no_files.id)
        assert no_files.error_code == "no_output_files"

        download_failure = await service.ensure_execution(
            _command(node_run_ids[6], "execution-6", instance.id, template)
        )
        download_failure = await service.advance_execution(download_failure.id)
        client.download_error = True
        client.snapshot_result = ComfyUIRemoteSnapshot(
            ComfyUIExecutionStatus.MATERIALIZING,
            100,
            {"1": {"images": []}},
            (ComfyUIOutputFile("1", "failed.png", "", "output", "image"),),
        )
        download_failure = await service.advance_execution(download_failure.id)
        assert download_failure.error_code == "artifact_materialization_failed"
        client.download_error = False

        timeout = await service.ensure_execution(
            _command(
                node_run_ids[7],
                "execution-7",
                instance.id,
                template,
                timeout_seconds=0.000001,
            )
        )
        timeout = await service.advance_execution(timeout.id)
        assert timeout.error_code == "timeout"

        original = await service.ensure_execution(
            _command(node_run_ids[8], "execution-8", instance.id, template)
        )
        with pytest.raises(ComfyUIOperationError, match="不同"):
            await service.ensure_execution(
                _command(node_run_ids[9], original.id, instance.id, template)
            )
        assert len(await service.list_executions()) == 9
        assert all(not item.status.terminal for item in await service.list_active_executions())
    finally:
        await context.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_comfyui_execution_validation_selected_outputs_and_recovery_edges(
    tmp_path: Path,
) -> None:
    client = ControlledComfyUIClient()
    context = await build_app_context(
        tmp_path,
        secret_store_override=SessionSecretStore(),
        comfyui_client_override=client,
    )
    service = context.comfyui_service
    try:
        instance = await service.create_instance(
            CreateComfyUIInstance("Selected outputs", "http://localhost:8188")
        )
        prompt = {
            "1": {"class_type": "SaveImage", "inputs": {"text": "old"}},
            "2": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}},
        }
        template = await service.import_template(
            ImportComfyUITemplate(
                instance.id,
                "Selected template",
                prompt,
                {
                    "type": "object",
                    "properties": {"prompt": {"type": "string"}},
                    "required": ["prompt"],
                },
                {"prompt": {"node_id": "1", "input_name": "text"}},
                ("1",),
            )
        )
        assert (await service.get_template(template.id)).id == template.id
        with pytest.raises(ComfyUITemplateNotFoundError):
            await service.get_template("missing")
        with pytest.raises(ComfyUIInstanceNotFoundError):
            await service.list_templates("missing")

        node_run_ids = await _create_node_runs(context, 6)
        command = _command(node_run_ids[0], "selected-0", instance.id, template)
        with pytest.raises(ComfyUIInputError, match="超时"):
            await service.ensure_execution(replace(command, timeout_seconds=0))
        await service.set_enabled(instance.id, False)
        with pytest.raises(ComfyUIOperationError, match="停用"):
            await service.ensure_execution(command)
        await service.set_enabled(instance.id, True)
        with pytest.raises(ComfyUIInputError, match="校验和"):
            await service.ensure_execution(replace(command, template_checksum="c" * 64))
        with pytest.raises(ComfyUIInputError, match="target"):
            await service.ensure_execution(replace(command, input_targets={}))

        selected = await service.ensure_execution(command)
        assert await service.ensure_execution(command) == selected
        selected = await service.advance_execution(selected.id)
        client.snapshot_result = ComfyUIRemoteSnapshot(
            ComfyUIExecutionStatus.MATERIALIZING,
            100,
            {"1": {"images": ["selected"]}, "2": {"images": ["ignored"]}},
            (
                ComfyUIOutputFile("1", "selected.png", "", "output", "image"),
                ComfyUIOutputFile("2", "ignored.png", "", "output", "image"),
            ),
        )
        selected = await service.advance_execution(selected.id)
        assert selected.status is ComfyUIExecutionStatus.SUCCESS
        assert len(selected.artifact_ids) == 1
        assert selected.output is not None
        assert tuple(selected.output["data"]) == ("1",)  # type: ignore[arg-type]
        assert await service.advance_execution(selected.id) == selected
        assert await service.cancel_execution(selected.id) == selected

        recovering = await service.ensure_execution(
            _command(node_run_ids[1], "selected-1", instance.id, template)
        )
        submitting = recovering.transition(ComfyUIExecutionStatus.SUBMITTING, context.clock.now())
        factory = SQLiteComfyUIUnitOfWorkFactory(context.database.sessions, context.events)
        async with factory() as uow:
            await uow.executions.update(submitting, expected_version=recovering.row_version)
            await uow.commit()
        client.find_error = True
        recovering = await service.advance_execution(recovering.id)
        assert recovering.status is ComfyUIExecutionStatus.SUBMITTING
        client.find_error = False
        client.find_result = None
        recovering = await service.advance_execution(recovering.id)
        assert recovering.error_code == "submission_reconciliation_failed"

        missing_identity = await service.ensure_execution(
            _command(node_run_ids[2], "selected-2", instance.id, template)
        )
        submitting = missing_identity.transition(
            ComfyUIExecutionStatus.SUBMITTING, context.clock.now()
        )
        async with factory() as uow:
            await uow.executions.update(submitting, expected_version=missing_identity.row_version)
            await uow.commit()
        missing_identity = await service.cancel_execution(missing_identity.id)
        assert missing_identity.error_code == "cancel_identity_missing"

        uncertain_cancel = await service.ensure_execution(
            _command(node_run_ids[3], "selected-3", instance.id, template)
        )
        uncertain_cancel = await service.advance_execution(uncertain_cancel.id)
        client.cancel_error = True
        uncertain_cancel = await service.cancel_execution(uncertain_cancel.id)
        assert uncertain_cancel.error_code == "cancel_outcome_unknown"
        client.cancel_error = False

        unexpected = await service.ensure_execution(
            _command(node_run_ids[4], "selected-4", instance.id, template)
        )
        unexpected = await service.advance_execution(unexpected.id)
        client.snapshot_result = ComfyUIRemoteSnapshot(ComfyUIExecutionStatus.PLANNED, None, {}, ())
        unexpected = await service.advance_execution(unexpected.id)
        assert unexpected.status is ComfyUIExecutionStatus.QUEUED

        materializing = await service.ensure_execution(
            _command(node_run_ids[5], "selected-5", instance.id, template)
        )
        materializing = await service.advance_execution(materializing.id)
        frozen = materializing.transition(
            ComfyUIExecutionStatus.MATERIALIZING,
            context.clock.now(),
            progress=100,
            output={"1": {}},
        )
        async with factory() as uow:
            await uow.executions.update(frozen, expected_version=materializing.row_version)
            await uow.commit()
        assert (
            await service.cancel_execution(frozen.id)
        ).status is ComfyUIExecutionStatus.MATERIALIZING
        client.snapshot_result = ComfyUIRemoteSnapshot(
            ComfyUIExecutionStatus.MATERIALIZING,
            100,
            {"1": {}},
            (ComfyUIOutputFile("1", "recovered.png", "", "output", "image"),),
        )
        assert (await service.advance_execution(frozen.id)).status is ComfyUIExecutionStatus.SUCCESS
    finally:
        await context.close()


async def _create_node_runs(context: object, count: int) -> tuple[str, ...]:
    app = context
    now = app.clock.now()  # type: ignore[attr-defined]
    ids = app.ids  # type: ignore[attr-defined]
    workflow = Workflow(ids.new(), "Comfy facts", "", None, 1, now, now)
    version = WorkflowVersion(
        ids.new(),
        workflow.id,
        1,
        WorkflowVersionStatus.DRAFT,
        {"type": "object", "properties": {}},
        {"type": "object", "properties": {}},
        {},
        "a" * 64,
        1,
        now,
        now,
    )
    nodes = tuple(
        WorkflowNode(
            ids.new(),
            version.id,
            f"node_{index}",
            WorkflowNodeType.TRANSFORM,
            f"Node {index}",
            None,
            None,
            None,
            {"type": "object", "properties": {}},
            {"type": "object", "properties": {}},
            {},
            {"kind": "project", "fields": {}},
            False,
            index * 10,
            0,
        )
        for index in range(count)
    )
    run = WorkflowRun(
        ids.new(),
        workflow.id,
        version.id,
        WorkflowRunStatus.RUNNING,
        {},
        None,
        version.checksum,
        1,
        now,
        now,
        started_at=now,
    )
    node_runs = tuple(
        NodeRun(
            ids.new(),
            run.id,
            node.id,
            node.node_key,
            NodeRunStatus.RUNNING,
            {},
            None,
            None,
            None,
            None,
            None,
            1,
            now,
            now,
            started_at=now,
        )
        for node in nodes
    )
    factory = SQLiteWorkflowUnitOfWorkFactory(app.database.sessions, app.events)  # type: ignore[attr-defined]
    async with factory() as uow:
        await uow.definitions.add(workflow)
        await uow.definitions.add_version(version)
        await uow.definitions.replace_draft_definition(
            version, nodes, (), expected_version=version.row_version
        )
        await uow.runs.add(run)
        await uow.runs.add_node_runs(node_runs)
        await uow.commit()
    return tuple(item.id for item in node_runs)


def _command(
    node_run_id: str,
    execution_id: str,
    instance_id: str,
    template: object,
    *,
    timeout_seconds: float = 60,
) -> EnsureComfyUIExecution:
    return EnsureComfyUIExecution(
        execution_id,
        node_run_id,
        instance_id,
        template.id,  # type: ignore[attr-defined]
        template.checksum,  # type: ignore[attr-defined]
        "b" * 64,
        template.prompt,  # type: ignore[attr-defined]
        template.output_nodes,  # type: ignore[attr-defined]
        template.input_targets,  # type: ignore[attr-defined]
        {"prompt": "new"},
        timeout_seconds,
    )
