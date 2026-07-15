"""ComfyUI instance and API-prompt catalog orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from jsonschema import Draft202012Validator, SchemaError

from astraweft.application.comfyui.commands import (
    CreateComfyUIInstance,
    EnsureComfyUIExecution,
    ImportComfyUITemplate,
    UpdateComfyUIInstance,
)
from astraweft.application.comfyui.events import (
    ComfyUIExecutionChanged,
    ComfyUIInstanceChanged,
    ComfyUITemplateChanged,
)
from astraweft.domain.comfyui import (
    ComfyUIExecution,
    ComfyUIExecutionStatus,
    ComfyUIHealth,
    ComfyUIInstance,
    ComfyUITemplate,
    comfyui_prompt_checksum,
    normalize_comfyui_base_url,
    patch_api_prompt,
)
from astraweft.ports.artifacts import ArtifactWriteError
from astraweft.ports.comfyui import (
    ComfyUIArtifactWriter,
    ComfyUIClient,
    ComfyUIOutputFile,
    ComfyUIUnitOfWorkFactory,
)
from astraweft.ports.runtime import Clock, IdGenerator


class ComfyUIInstanceNotFoundError(LookupError):
    """A ComfyUI instance is absent or has been deleted."""


class ComfyUITemplateNotFoundError(LookupError):
    """A ComfyUI template is absent."""


class ComfyUIInputError(ValueError):
    """ComfyUI configuration is invalid and safe to show in the GUI."""


class ComfyUIOperationError(RuntimeError):
    """A ComfyUI operation failed with a stable code and safe message."""

    def __init__(self, message: str, *, code: str = "comfyui_operation_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ComfyUITestResult:
    instance_id: str
    health: ComfyUIHealth
    message: str
    version: str | None
    node_count: int | None


class ComfyUIService:
    """Keep network probes outside database transactions and preserve snapshots."""

    def __init__(
        self,
        *,
        uow_factory: ComfyUIUnitOfWorkFactory,
        client: ComfyUIClient,
        artifacts: ComfyUIArtifactWriter,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._uow_factory = uow_factory
        self._client = client
        self._artifacts = artifacts
        self._clock = clock
        self._ids = ids
        self._execution_locks: dict[str, asyncio.Lock] = {}

    async def list_instances(self) -> tuple[ComfyUIInstance, ...]:
        async with self._uow_factory() as uow:
            return await uow.instances.list()

    async def get_instance(self, instance_id: str) -> ComfyUIInstance:
        async with self._uow_factory() as uow:
            instance = await uow.instances.get(instance_id)
        if instance is None:
            raise ComfyUIInstanceNotFoundError("ComfyUI 实例不存在或已删除")
        return instance

    async def create_instance(self, command: CreateComfyUIInstance) -> ComfyUIInstance:
        name = _required_name(command.name)
        base_url = _normalized_url(command.base_url)
        await self._assert_name_available(name)
        now = self._clock.now()
        instance = ComfyUIInstance(
            id=self._ids.new(),
            name=name,
            base_url=base_url,
            enabled=command.enabled,
            health=ComfyUIHealth.UNKNOWN,
            version=None,
            python_version=None,
            capabilities={},
            node_catalog_hash=None,
            last_error_code=None,
            last_checked_at=None,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        async with self._uow_factory() as uow:
            await uow.instances.add(instance)
            uow.publish_after_commit(ComfyUIInstanceChanged(instance.id, "created", now))
            await uow.commit()
        return instance

    async def update_instance(self, command: UpdateComfyUIInstance) -> ComfyUIInstance:
        current = await self.get_instance(command.instance_id)
        name = _required_name(command.name)
        await self._assert_name_available(name, excluding_instance_id=current.id)
        now = self._clock.now()
        updated = replace(
            current,
            name=name,
            base_url=_normalized_url(command.base_url),
            enabled=command.enabled,
            health=ComfyUIHealth.UNKNOWN,
            last_error_code=None,
            updated_at=now,
            row_version=current.row_version + 1,
        )
        async with self._uow_factory() as uow:
            await uow.instances.update(updated, expected_version=current.row_version)
            uow.publish_after_commit(ComfyUIInstanceChanged(updated.id, "updated", now))
            await uow.commit()
        return updated

    async def set_enabled(self, instance_id: str, enabled: bool) -> ComfyUIInstance:
        current = await self.get_instance(instance_id)
        now = self._clock.now()
        updated = replace(
            current,
            enabled=enabled,
            updated_at=now,
            row_version=current.row_version + 1,
        )
        async with self._uow_factory() as uow:
            await uow.instances.update(updated, expected_version=current.row_version)
            uow.publish_after_commit(ComfyUIInstanceChanged(updated.id, "updated", now))
            await uow.commit()
        return updated

    async def delete_instance(self, instance_id: str) -> None:
        current = await self.get_instance(instance_id)
        now = self._clock.now()
        deleted = replace(
            current,
            enabled=False,
            deleted_at=now,
            updated_at=now,
            row_version=current.row_version + 1,
        )
        async with self._uow_factory() as uow:
            await uow.instances.update(deleted, expected_version=current.row_version)
            uow.publish_after_commit(ComfyUIInstanceChanged(current.id, "deleted", now))
            await uow.commit()

    async def test_connection(self, instance_id: str) -> ComfyUITestResult:
        instance = await self.get_instance(instance_id)
        now = self._clock.now()
        try:
            probe = await self._client.probe(instance)
        except Exception:
            checked = instance.with_probe(
                health=ComfyUIHealth.UNAVAILABLE,
                version=None,
                python_version=None,
                capabilities={},
                node_catalog_hash=None,
                error_code="unavailable",
                checked_at=now,
            )
            async with self._uow_factory() as uow:
                await uow.instances.update(checked, expected_version=instance.row_version)
                uow.publish_after_commit(ComfyUIInstanceChanged(instance.id, "health_checked", now))
                await uow.commit()
            return ComfyUITestResult(
                instance_id=instance.id,
                health=checked.health,
                message="无法连接 ComfyUI，请确认它已启动且地址正确",
                version=None,
                node_count=None,
            )
        checked = instance.with_probe(
            health=ComfyUIHealth.HEALTHY,
            version=probe.version,
            python_version=probe.python_version,
            capabilities=probe.capabilities,
            node_catalog_hash=probe.node_catalog_hash,
            error_code=None,
            checked_at=now,
        )
        async with self._uow_factory() as uow:
            await uow.instances.update(checked, expected_version=instance.row_version)
            uow.publish_after_commit(ComfyUIInstanceChanged(instance.id, "health_checked", now))
            await uow.commit()
        node_count = probe.capabilities.get("node_count")
        return ComfyUITestResult(
            instance_id=instance.id,
            health=checked.health,
            message="连接成功",
            version=probe.version,
            node_count=node_count if isinstance(node_count, int) else None,
        )

    async def import_template(self, command: ImportComfyUITemplate) -> ComfyUITemplate:
        await self.get_instance(command.instance_id)
        prompt, schema, targets, outputs = _validated_template(command)
        async with self._uow_factory() as uow:
            current = await uow.templates.list_for_instance(command.instance_id)
        name = _required_name(command.name)
        if any(item.name.casefold() == name.casefold() for item in current):
            raise ComfyUIInputError("同一 ComfyUI 实例下已存在同名模板")
        now = self._clock.now()
        template = ComfyUITemplate(
            id=self._ids.new(),
            instance_id=command.instance_id,
            name=name,
            prompt=prompt,
            checksum=comfyui_prompt_checksum(prompt),
            input_schema=schema,
            input_targets=targets,
            output_nodes=outputs,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        async with self._uow_factory() as uow:
            await uow.templates.add(template)
            uow.publish_after_commit(
                ComfyUITemplateChanged(template.id, template.instance_id, "imported", now)
            )
            await uow.commit()
        return template

    async def get_template(self, template_id: str) -> ComfyUITemplate:
        async with self._uow_factory() as uow:
            template = await uow.templates.get(template_id)
        if template is None:
            raise ComfyUITemplateNotFoundError("ComfyUI 模板不存在")
        return template

    async def list_templates(self, instance_id: str) -> tuple[ComfyUITemplate, ...]:
        await self.get_instance(instance_id)
        async with self._uow_factory() as uow:
            return await uow.templates.list_for_instance(instance_id)

    async def list_executions(self, *, limit: int = 1000) -> tuple[ComfyUIExecution, ...]:
        async with self._uow_factory() as uow:
            return await uow.executions.list_recent(limit=limit)

    async def list_active_executions(
        self,
        *,
        limit: int = 1000,
    ) -> tuple[ComfyUIExecution, ...]:
        async with self._uow_factory() as uow:
            return await uow.executions.list_by_status(
                frozenset(status for status in ComfyUIExecutionStatus if not status.terminal),
                limit=limit,
            )

    async def get_execution(self, execution_id: str) -> ComfyUIExecution:
        async with self._uow_factory() as uow:
            execution = await uow.executions.get(execution_id)
        if execution is None:
            raise ComfyUIOperationError(
                "ComfyUI 执行记录不存在",
                code="execution_not_found",
            )
        return execution

    async def ensure_execution(
        self,
        command: EnsureComfyUIExecution,
    ) -> ComfyUIExecution:
        if command.timeout_seconds <= 0:
            raise ComfyUIInputError("ComfyUI 执行超时必须大于零")
        instance = await self.get_instance(command.instance_id)
        if not instance.enabled:
            raise ComfyUIOperationError("ComfyUI 实例已停用", code="disabled")
        async with self._uow_factory() as uow:
            existing = await uow.executions.get(command.execution_id)
        if existing is not None:
            _assert_same_execution(existing, command)
            return existing
        if comfyui_prompt_checksum(command.prompt) != command.template_checksum:
            raise ComfyUIInputError("ComfyUI 模板快照校验和不一致")
        try:
            resolved_prompt = patch_api_prompt(
                command.prompt,
                command.input_targets,
                command.inputs,
            )
        except ValueError as exc:
            raise ComfyUIInputError(str(exc)) from exc
        now = self._clock.now()
        execution = ComfyUIExecution(
            id=command.execution_id,
            node_run_id=command.node_run_id,
            instance_id=command.instance_id,
            template_id=command.template_id,
            template_checksum=command.template_checksum,
            workflow_checksum=command.workflow_checksum,
            prompt=resolved_prompt,
            output_nodes=command.output_nodes,
            client_id=f"astraweft-{command.execution_id}",
            status=ComfyUIExecutionStatus.PLANNED,
            remote_prompt_id=None,
            progress=None,
            output=None,
            artifact_ids=(),
            error_code=None,
            error_message=None,
            poll_after_at=None,
            timeout_at=now + timedelta(seconds=command.timeout_seconds),
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        async with self._uow_factory() as uow:
            await uow.executions.add(execution)
            uow.publish_after_commit(
                ComfyUIExecutionChanged(
                    execution.id,
                    execution.node_run_id,
                    execution.status,
                    now,
                )
            )
            await uow.commit()
        return execution

    async def advance_execution(self, execution_id: str) -> ComfyUIExecution:
        lock = self._execution_locks.setdefault(execution_id, asyncio.Lock())
        async with lock:
            return await self._advance_execution_locked(execution_id)

    async def cancel_execution(self, execution_id: str) -> ComfyUIExecution:
        lock = self._execution_locks.setdefault(execution_id, asyncio.Lock())
        async with lock:
            execution = await self.get_execution(execution_id)
            if execution.status.terminal:
                return execution
            now = self._clock.now()
            if execution.status is ComfyUIExecutionStatus.PLANNED:
                canceled = execution.transition(ComfyUIExecutionStatus.CANCELED, now)
                await self._persist_execution(execution, canceled)
                return canceled
            if execution.status is ComfyUIExecutionStatus.MATERIALIZING:
                return execution
            if execution.remote_prompt_id is None:
                attention = execution.transition(
                    ComfyUIExecutionStatus.NEEDS_ATTENTION,
                    now,
                    error_code="cancel_identity_missing",
                    error_message="取消时缺少 ComfyUI 任务编号，请人工核对队列",
                )
                await self._persist_execution(execution, attention)
                return attention
            canceling = execution.transition(ComfyUIExecutionStatus.CANCELING, now)
            await self._persist_execution(execution, canceling)
            instance = await self.get_instance(execution.instance_id)
            try:
                await self._client.cancel(instance, execution.remote_prompt_id)
            except Exception:
                attention = canceling.transition(
                    ComfyUIExecutionStatus.NEEDS_ATTENTION,
                    self._clock.now(),
                    error_code="cancel_outcome_unknown",
                    error_message="无法确认 ComfyUI 是否已取消，请人工核对队列",
                )
                await self._persist_execution(canceling, attention)
                return attention
            return canceling

    async def _advance_execution_locked(self, execution_id: str) -> ComfyUIExecution:
        execution = await self.get_execution(execution_id)
        if execution.status.terminal:
            return execution
        now = self._clock.now()
        if now >= execution.timeout_at:
            timed_out = execution.transition(
                ComfyUIExecutionStatus.FAILED,
                now,
                error_code="timeout",
                error_message="ComfyUI 执行超过本地超时限制",
            )
            await self._persist_execution(execution, timed_out)
            return timed_out
        instance = await self.get_instance(execution.instance_id)
        if execution.status is ComfyUIExecutionStatus.PLANNED:
            submitting = execution.transition(ComfyUIExecutionStatus.SUBMITTING, now)
            await self._persist_execution(execution, submitting)
            return await self._submit_execution(instance, submitting)
        if execution.status is ComfyUIExecutionStatus.SUBMITTING:
            return await self._recover_submission(instance, execution)
        if execution.remote_prompt_id is None:
            attention = execution.transition(
                ComfyUIExecutionStatus.NEEDS_ATTENTION,
                now,
                error_code="remote_identity_missing",
                error_message="ComfyUI 执行缺少远端任务编号",
            )
            await self._persist_execution(execution, attention)
            return attention
        try:
            snapshot = await self._client.snapshot(instance, execution.remote_prompt_id)
        except Exception:
            refreshed = execution.refresh(
                now,
                progress=self._client.latest_progress(execution.remote_prompt_id),
                poll_after_at=now + timedelta(seconds=2),
            )
            await self._persist_execution(execution, refreshed)
            return refreshed
        if (
            execution.status is ComfyUIExecutionStatus.CANCELING
            and snapshot.error_code == "remote_execution_interrupted"
        ):
            canceled = execution.transition(ComfyUIExecutionStatus.CANCELED, now)
            await self._persist_execution(execution, canceled)
            return canceled
        if snapshot.status in {
            ComfyUIExecutionStatus.QUEUED,
            ComfyUIExecutionStatus.RUNNING,
        }:
            if snapshot.status is execution.status:
                updated = execution.refresh(
                    now,
                    progress=snapshot.progress,
                    poll_after_at=now + timedelta(seconds=1),
                )
            else:
                updated = execution.transition(
                    snapshot.status,
                    now,
                    progress=snapshot.progress,
                    poll_after_at=now + timedelta(seconds=1),
                )
            await self._persist_execution(execution, updated)
            await self._client.ensure_progress_watch(
                instance,
                prompt_id=execution.remote_prompt_id,
                client_id=execution.client_id,
            )
            return updated
        if snapshot.status is ComfyUIExecutionStatus.MATERIALIZING:
            materializing = (
                execution
                if execution.status is ComfyUIExecutionStatus.MATERIALIZING
                else execution.transition(
                    ComfyUIExecutionStatus.MATERIALIZING,
                    now,
                    progress=100,
                    output=snapshot.outputs,
                )
            )
            if materializing is not execution:
                await self._persist_execution(execution, materializing)
            return await self._materialize_outputs(instance, materializing, snapshot.files)
        if snapshot.status in {
            ComfyUIExecutionStatus.FAILED,
            ComfyUIExecutionStatus.NEEDS_ATTENTION,
            ComfyUIExecutionStatus.CANCELED,
        }:
            terminal = execution.transition(
                snapshot.status,
                now,
                error_code=snapshot.error_code,
                error_message=snapshot.error_message,
            )
            await self._persist_execution(execution, terminal)
            return terminal
        refreshed = execution.refresh(now, poll_after_at=now + timedelta(seconds=1))
        await self._persist_execution(execution, refreshed)
        return refreshed

    async def _submit_execution(
        self,
        instance: ComfyUIInstance,
        submitting: ComfyUIExecution,
    ) -> ComfyUIExecution:
        try:
            submitted = await self._client.submit(
                instance,
                prompt=submitting.prompt,
                client_id=submitting.client_id,
                execution_id=submitting.id,
                workflow_checksum=submitting.workflow_checksum,
            )
        except Exception:
            attention = submitting.transition(
                ComfyUIExecutionStatus.NEEDS_ATTENTION,
                self._clock.now(),
                error_code="submission_outcome_unknown",
                error_message="无法确认 ComfyUI 是否已接收任务，已停止自动重提",
            )
            await self._persist_execution(submitting, attention)
            return attention
        queued = submitting.transition(
            ComfyUIExecutionStatus.QUEUED,
            self._clock.now(),
            remote_prompt_id=submitted.prompt_id,
            progress=0,
            poll_after_at=self._clock.now() + timedelta(seconds=1),
        )
        await self._persist_execution(submitting, queued)
        await self._client.ensure_progress_watch(
            instance,
            prompt_id=submitted.prompt_id,
            client_id=submitting.client_id,
        )
        return queued

    async def _recover_submission(
        self,
        instance: ComfyUIInstance,
        execution: ComfyUIExecution,
    ) -> ComfyUIExecution:
        try:
            prompt_id = await self._client.find_execution(instance, execution.id)
        except Exception:
            refreshed = execution.refresh(
                self._clock.now(),
                poll_after_at=self._clock.now() + timedelta(seconds=2),
            )
            await self._persist_execution(execution, refreshed)
            return refreshed
        if prompt_id is None:
            attention = execution.transition(
                ComfyUIExecutionStatus.NEEDS_ATTENTION,
                self._clock.now(),
                error_code="submission_reconciliation_failed",
                error_message="未找到可对账的 ComfyUI 任务，已停止自动重提",
            )
            await self._persist_execution(execution, attention)
            return attention
        queued = execution.transition(
            ComfyUIExecutionStatus.QUEUED,
            self._clock.now(),
            remote_prompt_id=prompt_id,
            progress=0,
            poll_after_at=self._clock.now() + timedelta(seconds=1),
        )
        await self._persist_execution(execution, queued)
        return queued

    async def _materialize_outputs(
        self,
        instance: ComfyUIInstance,
        execution: ComfyUIExecution,
        files: tuple[ComfyUIOutputFile, ...],
    ) -> ComfyUIExecution:
        selected_files = tuple(
            output for output in files if output.node_id in execution.output_nodes
        )
        if not selected_files:
            failed = execution.transition(
                ComfyUIExecutionStatus.FAILED,
                self._clock.now(),
                error_code="no_output_files",
                error_message="ComfyUI 已完成，但没有返回可保存的成果文件",
            )
            await self._persist_execution(execution, failed)
            return failed
        artifacts = []
        try:
            for output in selected_files:
                artifact_id = _artifact_id(execution.id, output)
                artifacts.append(
                    await self._artifacts.materialize(
                        owner_id=execution.id,
                        artifact_id=artifact_id,
                        instance=instance,
                        output=output,
                        client=self._client,
                        created_at=execution.created_at,
                    )
                )
        except (ArtifactWriteError, OSError):
            failed = execution.transition(
                ComfyUIExecutionStatus.FAILED,
                self._clock.now(),
                error_code="artifact_materialization_failed",
                error_message="ComfyUI 成果保存到本地失败",
            )
            await self._persist_execution(execution, failed)
            return failed
        artifact_ids = tuple(artifact.id for artifact in artifacts)
        raw_output = execution.output or {}
        selected_output = {
            node_id: value
            for node_id, value in raw_output.items()
            if node_id in execution.output_nodes
        }
        normalized_output: Mapping[str, object] = {
            "data": selected_output,
            "artifacts": list(artifact_ids),
        }
        success = execution.transition(
            ComfyUIExecutionStatus.SUCCESS,
            self._clock.now(),
            output=normalized_output,
            artifact_ids=artifact_ids,
        )
        async with self._uow_factory() as uow:
            for artifact in artifacts:
                await uow.artifacts.add(artifact)
            await uow.executions.update(success, expected_version=execution.row_version)
            uow.publish_after_commit(
                ComfyUIExecutionChanged(
                    success.id,
                    success.node_run_id,
                    success.status,
                    success.updated_at,
                )
            )
            await uow.commit()
        return success

    async def _persist_execution(
        self,
        previous: ComfyUIExecution,
        updated: ComfyUIExecution,
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.executions.update(updated, expected_version=previous.row_version)
            uow.publish_after_commit(
                ComfyUIExecutionChanged(
                    updated.id,
                    updated.node_run_id,
                    updated.status,
                    updated.updated_at,
                )
            )
            await uow.commit()

    async def _assert_name_available(
        self,
        name: str,
        *,
        excluding_instance_id: str | None = None,
    ) -> None:
        instances = await self.list_instances()
        if any(
            instance.id != excluding_instance_id and instance.name.casefold() == name.casefold()
            for instance in instances
        ):
            raise ComfyUIInputError("ComfyUI 实例名称已存在")


def _required_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 160:
        raise ComfyUIInputError("名称不能为空且不能超过 160 个字符")
    return name


def _normalized_url(value: str) -> str:
    try:
        return normalize_comfyui_base_url(value)
    except ValueError as exc:
        raise ComfyUIInputError("地址必须是本机 HTTP 地址，或安全的 HTTPS 地址") from exc


def _validated_template(
    command: ImportComfyUITemplate,
) -> tuple[
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    tuple[str, ...],
]:
    try:
        checksum = comfyui_prompt_checksum(command.prompt)
        del checksum
        Draft202012Validator.check_schema(command.input_schema)
    except (ValueError, SchemaError) as exc:
        raise ComfyUIInputError("模板或输入结构不是有效的 ComfyUI API 格式") from exc
    schema = command.input_schema
    if schema.get("type") != "object":
        raise ComfyUIInputError("ComfyUI 模板输入结构必须是对象")
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ComfyUIInputError("ComfyUI 模板输入字段无效")
    if set(command.input_targets) != set(properties):
        raise ComfyUIInputError("每个模板输入字段都必须映射到一个 ComfyUI 节点参数")
    for port, target in command.input_targets.items():
        if not isinstance(target, Mapping):
            raise ComfyUIInputError(f"输入映射无效：{port}")
        node_id = target.get("node_id")
        input_name = target.get("input_name")
        node = command.prompt.get(node_id) if isinstance(node_id, str) else None
        node_inputs = node.get("inputs") if isinstance(node, Mapping) else None
        if (
            not isinstance(input_name, str)
            or not isinstance(node_inputs, Mapping)
            or input_name not in node_inputs
        ):
            raise ComfyUIInputError(f"输入映射指向不存在的节点参数：{port}")
    outputs = tuple(dict.fromkeys(command.output_nodes))
    if not outputs or any(node_id not in command.prompt for node_id in outputs):
        raise ComfyUIInputError("至少选择一个存在的 ComfyUI 输出节点")
    return command.prompt, schema, command.input_targets, outputs


def _assert_same_execution(
    execution: ComfyUIExecution,
    command: EnsureComfyUIExecution,
) -> None:
    if (
        execution.node_run_id != command.node_run_id
        or execution.instance_id != command.instance_id
        or execution.template_id != command.template_id
        or execution.template_checksum != command.template_checksum
        or execution.workflow_checksum != command.workflow_checksum
        or execution.output_nodes != command.output_nodes
    ):
        raise ComfyUIOperationError(
            "ComfyUI 执行编号已被不同的执行意图占用",
            code="execution_identity_conflict",
        )


def _artifact_id(execution_id: str, output: ComfyUIOutputFile) -> str:
    identity = "|".join(
        (
            execution_id,
            output.node_id,
            output.folder_type,
            output.subfolder,
            output.filename,
        )
    )
    return str(uuid5(NAMESPACE_URL, f"astraweft:comfyui:{identity}"))
