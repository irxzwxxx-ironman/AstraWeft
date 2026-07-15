"""Durable Provider submit, poll, logging, and artifact orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from astraweft.application.providers import (
    ProviderInputError,
    ProviderNotFoundError,
    ProviderOperationError,
    ProviderService,
)
from astraweft.application.tasks.commands import CreateTask
from astraweft.application.tasks.events import TaskChanged
from astraweft.application.tracing import TraceContext, current_trace_id
from astraweft.domain.task import (
    Artifact,
    AttemptPhase,
    AttemptStatus,
    RequestLog,
    Task,
    TaskAttempt,
    TaskStatus,
    TaskTransitionError,
)
from astraweft.ports.artifacts import ArtifactLifecycle, ArtifactWriteError, ArtifactWriter
from astraweft.ports.runtime import Clock, IdGenerator
from astraweft.ports.tasks import TaskUnitOfWork, TaskUnitOfWorkFactory
from astraweft_provider_sdk import (
    CancelResult,
    ProviderCallInfo,
    ProviderError,
    ProviderOutput,
    ProviderRequest,
    ProviderTimeoutError,
    RemoteTaskSnapshot,
    SchemaContractError,
    validate_instance,
)


class TaskNotFoundError(LookupError):
    """A local task does not exist."""


class TaskInputError(ValueError):
    """Task input is invalid and safe to display."""


class TaskExecutionError(RuntimeError):
    """A task cannot be advanced from its current durable state."""


class ArtifactNotFoundError(LookupError):
    """An artifact record does not exist."""


class ArtifactLifecycleError(RuntimeError):
    """An artifact cannot safely transition between library and trash."""


@dataclass(frozen=True, slots=True)
class ArtifactTrashPreview:
    artifact: Artifact
    file_exists: bool
    task_reference: bool
    workflow_reference_count: int

    @property
    def can_purge(self) -> bool:
        return self.artifact.deleted_at is not None and self.workflow_reference_count == 0


class TaskService:
    """Persist intent before every external action and results after it returns."""

    def __init__(
        self,
        *,
        providers: ProviderService,
        uow_factory: TaskUnitOfWorkFactory,
        artifacts: ArtifactWriter,
        artifact_lifecycle: ArtifactLifecycle,
        clock: Clock,
        ids: IdGenerator,
        traces: TraceContext,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._providers = providers
        self._uow_factory = uow_factory
        self._artifacts = artifacts
        self._artifact_lifecycle = artifact_lifecycle
        self._clock = clock
        self._ids = ids
        self._traces = traces
        self._max_attempts = max_attempts

    async def create(self, command: CreateTask) -> Task:
        if command.task_id is not None:
            async with self._uow_factory() as uow:
                existing = await uow.tasks.get(command.task_id)
            if existing is not None:
                if not _matches_explicit_intent(existing, command):
                    raise TaskInputError("显式任务 ID 已被不同的执行意图占用")
                return existing
        execution = await self._providers.resolve_execution(
            command.provider_id,
            command.model_id,
            command.operation,
        )
        try:
            inputs = dict(execution.model.default_params)
            inputs.update(command.inputs)
            try:
                validate_instance(inputs, execution.model.parameter_schema)
            except SchemaContractError as exc:
                raise TaskInputError(f"任务参数无效：{exc}") from None
            now = self._clock.now()
            task_id = command.task_id or self._ids.new()
            task = Task(
                id=task_id,
                provider_id=execution.provider.id,
                model_id=execution.model.id,
                status=TaskStatus.CREATED,
                operation=command.operation,
                input=inputs,
                provider_config_snapshot={
                    "plugin_id": execution.provider.plugin_id,
                    "plugin_version": execution.provider.plugin_version,
                    "endpoint": execution.provider.endpoint,
                    "settings": execution.provider.config,
                    "remote_model_id": execution.model.remote_model_id,
                    "model_source_hash": execution.model.source_hash,
                },
                normalized_output=None,
                remote_task_id=None,
                idempotency_key=f"astraweft-task-{task_id}",
                priority=command.priority,
                progress=0,
                poll_after_at=None,
                timeout_at=now + timedelta(seconds=command.timeout_seconds),
                cancel_requested_at=None,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            queued = task.transition(TaskStatus.QUEUED, now)
            async with self._uow_factory() as uow:
                await uow.tasks.add(task)
                await uow.tasks.update(queued, expected_version=task.row_version)
                uow.publish_after_commit(TaskChanged(queued.id, queued.status, now))
                await uow.commit()
            return queued
        finally:
            with suppress(Exception):
                await execution.close()

    async def create_and_run(self, command: CreateTask) -> Task:
        task = await self.create(command)
        return await self.run_until_terminal(task.id)

    async def get(self, task_id: str) -> Task:
        async with self._uow_factory() as uow:
            task = await uow.tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError("任务不存在")
        return task

    async def list_tasks(self, *, limit: int = 1000) -> tuple[Task, ...]:
        async with self._uow_factory() as uow:
            return await uow.tasks.list_recent(limit=limit)

    async def list_ready(self, *, limit: int = 100) -> tuple[Task, ...]:
        async with self._uow_factory() as uow:
            return await uow.tasks.list_ready(self._clock.now(), limit=limit)

    async def list_attempts(self, task_id: str) -> tuple[TaskAttempt, ...]:
        async with self._uow_factory() as uow:
            return await uow.attempts.list_for_task(task_id)

    async def list_request_logs(self, *, limit: int = 1000) -> tuple[RequestLog, ...]:
        async with self._uow_factory() as uow:
            return await uow.request_logs.list_recent(limit=limit)

    async def purge_request_logs(self, *, retention_days: int) -> int:
        if retention_days == 0:
            return 0
        if retention_days < 1:
            raise ValueError("request log retention must be non-negative")
        cutoff = self._clock.now() - timedelta(days=retention_days)
        async with self._uow_factory() as uow:
            purged = await uow.request_logs.delete_before(cutoff)
            await uow.commit()
        return purged

    async def list_artifacts(
        self, task_id: str | None = None, *, limit: int = 1000
    ) -> tuple[Artifact, ...]:
        async with self._uow_factory() as uow:
            if task_id is not None:
                return await uow.artifacts.list_for_task(task_id)
            return await uow.artifacts.list_recent(limit=limit)

    async def list_trashed_artifacts(self, *, limit: int = 1000) -> tuple[Artifact, ...]:
        async with self._uow_factory() as uow:
            return await uow.artifacts.list_trashed(limit=limit)

    async def preview_artifact_trash(self, artifact_id: str) -> ArtifactTrashPreview:
        async with self._uow_factory() as uow:
            artifact = await uow.artifacts.get(artifact_id)
            if artifact is None:
                raise ArtifactNotFoundError("产物不存在")
            workflow_references = await uow.artifacts.workflow_reference_count(artifact_id)
        return ArtifactTrashPreview(
            artifact=artifact,
            file_exists=await self._artifact_lifecycle.exists(
                artifact,
                trashed=artifact.deleted_at is not None,
            ),
            task_reference=artifact.task_id is not None,
            workflow_reference_count=workflow_references,
        )

    async def trash_artifact(self, artifact_id: str) -> Artifact:
        preview = await self.preview_artifact_trash(artifact_id)
        artifact = preview.artifact
        if artifact.deleted_at is not None:
            return artifact
        if not preview.file_exists:
            raise ArtifactLifecycleError("产物文件缺失，未移入回收站")
        trashed = artifact.move_to_trash(self._clock.now())
        await self._artifact_lifecycle.move_to_trash(artifact)
        try:
            async with self._uow_factory() as uow:
                await uow.artifacts.update_deleted_at(trashed)
                await uow.commit()
        except Exception:
            with suppress(Exception):
                await self._artifact_lifecycle.restore_from_trash(artifact)
            raise
        return trashed

    async def restore_artifact(self, artifact_id: str) -> Artifact:
        preview = await self.preview_artifact_trash(artifact_id)
        artifact = preview.artifact
        if artifact.deleted_at is None:
            return artifact
        if not preview.file_exists:
            raise ArtifactLifecycleError("回收站文件缺失，未恢复产物")
        restored = artifact.restore_from_trash()
        await self._artifact_lifecycle.restore_from_trash(artifact)
        try:
            async with self._uow_factory() as uow:
                await uow.artifacts.update_deleted_at(restored)
                await uow.commit()
        except Exception:
            with suppress(Exception):
                await self._artifact_lifecycle.move_to_trash(artifact)
            raise
        return restored

    async def purge_artifact(self, artifact_id: str, *, confirm_sha256: str) -> None:
        preview = await self.preview_artifact_trash(artifact_id)
        artifact = preview.artifact
        if artifact.deleted_at is None:
            raise ArtifactLifecycleError("产物必须先移入回收站")
        if confirm_sha256 != artifact.sha256:
            raise ArtifactLifecycleError("永久删除确认与产物不匹配")
        if preview.workflow_reference_count:
            raise ArtifactLifecycleError(
                f"产物仍被 {preview.workflow_reference_count} 个工作流端口引用"
            )
        await self._artifact_lifecycle.purge_from_trash(artifact)
        async with self._uow_factory() as uow:
            await uow.artifacts.delete(artifact.id)
            await uow.commit()

    async def purge_expired_artifacts(self, *, retention_days: int) -> int:
        if retention_days < 1:
            raise ValueError("artifact trash retention must be positive")
        cutoff = self._clock.now() - timedelta(days=retention_days)
        artifacts = await self.list_trashed_artifacts(limit=10_000)
        purged = 0
        for artifact in artifacts:
            if artifact.deleted_at is None or artifact.deleted_at > cutoff:
                continue
            preview = await self.preview_artifact_trash(artifact.id)
            if not preview.can_purge:
                continue
            await self.purge_artifact(artifact.id, confirm_sha256=artifact.sha256)
            purged += 1
        return purged

    async def cancel(self, task_id: str) -> Task:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            task = await self._required_task(uow, task_id)
            if task.status.terminal:
                return task
            requested = task.request_cancel(now)
            await uow.tasks.update(requested, expected_version=task.row_version)
            if requested.remote_task_id is None and requested.status in {
                TaskStatus.CREATED,
                TaskStatus.QUEUED,
                TaskStatus.RETRY_WAIT,
            }:
                canceled = requested.transition(TaskStatus.CANCELED, now)
                await uow.tasks.update(canceled, expected_version=requested.row_version)
                uow.publish_after_commit(TaskChanged(task.id, canceled.status, now))
                await uow.commit()
                return canceled
            prepared = requested
            if prepared.status in {TaskStatus.RETRY_WAIT, TaskStatus.RECOVERING}:
                prepared = prepared.transition(TaskStatus.POLLING, now)
                await uow.tasks.update(prepared, expected_version=requested.row_version)
            if prepared.status not in {TaskStatus.RUNNING, TaskStatus.POLLING}:
                raise TaskExecutionError("任务当前不能取消")
            canceling = prepared.transition(TaskStatus.CANCELING, now)
            await uow.tasks.update(canceling, expected_version=prepared.row_version)
            attempt = TaskAttempt(
                id=self._ids.new(),
                task_id=task.id,
                attempt_no=await uow.attempts.next_attempt_no(task.id),
                phase=AttemptPhase.CANCEL,
                status=AttemptStatus.RUNNING,
                error_code=None,
                error_message=None,
                provider_error={},
                retryable=None,
                retry_after_at=None,
                started_at=now,
                ended_at=None,
            )
            await uow.attempts.add(attempt)
            uow.publish_after_commit(TaskChanged(task.id, canceling.status, now))
            await uow.commit()
        return await self._cancel_remote(canceling, attempt)

    async def recover_pending(self) -> tuple[Task, ...]:
        """Recover non-terminal tasks without blindly repeating remote submits."""
        statuses = frozenset(status for status in TaskStatus if not status.terminal)
        async with self._uow_factory() as uow:
            pending = await uow.tasks.list_by_status(statuses)
        recovered: list[Task] = []
        for task in pending:
            current = task
            if task.status in {TaskStatus.RUNNING, TaskStatus.POLLING}:
                if task.remote_task_id is None:
                    current = await self._mark_needs_attention(
                        task.id, "远程任务标识缺失，已停止自动恢复"
                    )
                else:
                    current = await self._mark_recovering(task.id)
            elif task.status is TaskStatus.SUBMITTING:
                current = await self._recover_uncertain_submit(task)
            elif task.status is TaskStatus.CREATED:
                current = await self._queue_created(task.id)
            elif task.status is TaskStatus.CANCELING:
                current = await self._mark_needs_attention(
                    task.id, "取消操作在重启时状态不确定，请人工确认"
                )
            if not current.status.terminal:
                try:
                    current = await self.run_until_terminal(current.id)
                except TaskExecutionError:
                    current = await self.get(current.id)
            recovered.append(current)
        return tuple(recovered)

    async def run_once(self, task_id: str) -> Task:
        task = await self.get(task_id)
        if task.status.terminal:
            return task
        if self._expired(task):
            return await self._mark_timed_out(task.id)
        if task.status in {TaskStatus.QUEUED, TaskStatus.RETRY_WAIT}:
            if task.status is TaskStatus.RETRY_WAIT and task.remote_task_id is not None:
                return await self._poll(task.id)
            return await self._submit(task.id)
        if task.status in {TaskStatus.POLLING, TaskStatus.RUNNING, TaskStatus.RECOVERING}:
            return await self._poll(task.id)
        raise TaskExecutionError(f"任务当前处于 {task.status}，无法自动推进")

    async def run_until_terminal(self, task_id: str, *, max_cycles: int = 100) -> Task:
        if max_cycles < 1:
            raise ValueError("max_cycles must be positive")
        task = await self.get(task_id)
        for _ in range(max_cycles):
            if task.status.terminal:
                return task
            await self._wait_until_ready(task)
            task = await self.run_once(task.id)
        return task

    async def _submit(self, task_id: str) -> Task:
        task, attempt = await self._begin_attempt(task_id, AttemptPhase.SUBMIT)
        if task.model_id is None:
            return await self._fail_attempt(
                task,
                attempt,
                code="model_missing",
                message="任务缺少模型",
                retryable=False,
                safe_details={},
                latency_ms=0,
                trace_id=self._trace_id(),
            )
        try:
            execution = await self._providers.resolve_execution(
                task.provider_id,
                task.model_id,
                task.operation,
            )
        except (ProviderNotFoundError, ProviderInputError, ProviderOperationError) as exc:
            return await self._fail_attempt(
                task,
                attempt,
                code=getattr(exc, "code", "provider_unavailable"),
                message=str(exc),
                retryable=False,
                safe_details={},
                latency_ms=0,
                trace_id=self._trace_id(),
            )
        trace_id = self._trace_id()
        started = self._clock.monotonic()
        try:
            request = ProviderRequest(
                operation=task.operation,
                remote_model_id=execution.model.remote_model_id,
                inputs=task.input,
                idempotency_key=task.idempotency_key,
                trace_id=trace_id,
                timeout_seconds=self._remaining_seconds(task),
                metadata={"task_id": task.id},
            )
            async with asyncio.timeout(request.timeout_seconds):
                result = await execution.client.submit(request)
            latency_ms = self._latency_ms(started)
            if result.mode == "accepted":
                return await self._finish_accepted(
                    task,
                    attempt,
                    remote_task_id=result.remote_task_id or "",
                    progress=result.progress,
                    poll_after_seconds=result.poll_after_seconds,
                    provider_request_id=result.provider_request_id,
                    call=result.call,
                    latency_ms=latency_ms,
                    trace_id=trace_id,
                )
            if result.output is None:  # pragma: no cover - SDK invariant
                raise TaskExecutionError("Provider 未返回同步结果")
            return await self._finish_output(
                task,
                attempt,
                result.output,
                response_summary={
                    "mode": result.mode,
                    "provider_request_id": result.provider_request_id,
                    "progress": result.progress,
                },
                latency_ms=latency_ms,
                trace_id=trace_id,
                call=result.call,
                artifact_allowed_hosts=execution.allowed_network,
            )
        except ProviderError as exc:
            return await self._fail_provider_error(
                task, attempt, exc, self._latency_ms(started), trace_id
            )
        except TimeoutError:
            timeout_error = ProviderTimeoutError("Provider 请求超过任务时限")
            return await self._fail_provider_error(
                task,
                attempt,
                timeout_error,
                self._latency_ms(started),
                trace_id,
            )
        except (ArtifactWriteError, TaskExecutionError) as exc:
            return await self._fail_attempt(
                task,
                attempt,
                code="artifact_write_failed"
                if isinstance(exc, ArtifactWriteError)
                else "execution_error",
                message=str(exc),
                retryable=False,
                safe_details={},
                latency_ms=self._latency_ms(started),
                trace_id=trace_id,
            )
        except Exception:
            return await self._fail_attempt(
                task,
                attempt,
                code="plugin_error",
                message="Provider 插件发生未知错误，需要人工确认",
                retryable=False,
                safe_details={},
                latency_ms=self._latency_ms(started),
                trace_id=trace_id,
                force_attention=True,
            )
        finally:
            with suppress(Exception):
                await execution.close()

    async def _poll(self, task_id: str) -> Task:
        task, attempt = await self._begin_attempt(task_id, AttemptPhase.POLL)
        if task.model_id is None or task.remote_task_id is None:
            return await self._fail_attempt(
                task,
                attempt,
                code="remote_identity_missing",
                message="远程任务标识缺失，需要人工处理",
                retryable=False,
                safe_details={},
                latency_ms=0,
                trace_id=self._trace_id(),
                force_attention=True,
            )
        try:
            execution = await self._providers.resolve_execution(
                task.provider_id,
                task.model_id,
                task.operation,
                allow_inactive=True,
            )
        except (ProviderNotFoundError, ProviderInputError, ProviderOperationError) as exc:
            return await self._fail_attempt(
                task,
                attempt,
                code=getattr(exc, "code", "provider_unavailable"),
                message=str(exc),
                retryable=False,
                safe_details={},
                latency_ms=0,
                trace_id=self._trace_id(),
                force_attention=True,
            )
        trace_id = self._trace_id()
        started = self._clock.monotonic()
        try:
            async with asyncio.timeout(self._remaining_seconds(task)):
                snapshot = await execution.client.get_task(task.remote_task_id)
            latency_ms = self._latency_ms(started)
            return await self._finish_snapshot(
                task,
                attempt,
                snapshot,
                latency_ms=latency_ms,
                trace_id=trace_id,
                artifact_allowed_hosts=execution.allowed_network,
            )
        except ProviderError as exc:
            return await self._fail_provider_error(
                task, attempt, exc, self._latency_ms(started), trace_id
            )
        except TimeoutError:
            return await self._mark_timed_out(task.id, attempt=attempt, trace_id=trace_id)
        except Exception:
            return await self._fail_attempt(
                task,
                attempt,
                code="plugin_error",
                message="Provider 插件轮询失败，将按策略重试",
                retryable=True,
                safe_details={},
                latency_ms=self._latency_ms(started),
                trace_id=trace_id,
            )
        finally:
            with suppress(Exception):
                await execution.close()

    async def _begin_attempt(self, task_id: str, phase: AttemptPhase) -> tuple[Task, TaskAttempt]:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            task = await uow.tasks.get(task_id)
            if task is None:
                raise TaskNotFoundError("任务不存在")
            original = task
            if phase is AttemptPhase.SUBMIT:
                if task.status is TaskStatus.RETRY_WAIT and task.remote_task_id is None:
                    task = task.transition(TaskStatus.QUEUED, now)
                    await uow.tasks.update(task, expected_version=original.row_version)
                    original = task
                if task.status is not TaskStatus.QUEUED:
                    raise TaskExecutionError("只有排队中的任务可以提交")
                task = task.transition(TaskStatus.SUBMITTING, now)
                await uow.tasks.update(task, expected_version=original.row_version)
            else:
                if (
                    task.status is TaskStatus.RETRY_WAIT and task.remote_task_id is not None
                ) or task.status in {TaskStatus.RUNNING, TaskStatus.RECOVERING}:
                    task = task.transition(TaskStatus.POLLING, now)
                    await uow.tasks.update(task, expected_version=original.row_version)
                elif task.status is not TaskStatus.POLLING:
                    raise TaskExecutionError("只有远程运行中的任务可以轮询")
            attempt = TaskAttempt(
                id=self._ids.new(),
                task_id=task.id,
                attempt_no=await uow.attempts.next_attempt_no(task.id),
                phase=phase,
                status=AttemptStatus.RUNNING,
                error_code=None,
                error_message=None,
                provider_error={},
                retryable=None,
                retry_after_at=None,
                started_at=now,
                ended_at=None,
            )
            await uow.attempts.add(attempt)
            uow.publish_after_commit(TaskChanged(task.id, task.status, now))
            await uow.commit()
        return task, attempt

    async def _finish_accepted(
        self,
        task: Task,
        attempt: TaskAttempt,
        *,
        remote_task_id: str,
        progress: int | None,
        poll_after_seconds: float | None,
        provider_request_id: str | None,
        call: ProviderCallInfo | None,
        latency_ms: int,
        trace_id: str,
    ) -> Task:
        now = self._clock.now()
        poll_at = now + timedelta(seconds=poll_after_seconds or 0)
        async with self._uow_factory() as uow:
            latest = await self._required_task(uow, task.id)
            running = latest.transition(
                TaskStatus.RUNNING,
                now,
                remote_task_id=remote_task_id,
                progress=progress,
            )
            await uow.tasks.update(running, expected_version=latest.row_version)
            polling = running.transition(
                TaskStatus.POLLING,
                now,
                progress=progress,
                poll_after_at=poll_at,
            )
            await uow.tasks.update(polling, expected_version=running.row_version)
            await self._complete_attempt(uow, attempt, now)
            await uow.request_logs.add(
                self._request_log(
                    task,
                    attempt,
                    trace_id=trace_id,
                    latency_ms=latency_ms,
                    response={
                        "mode": "accepted",
                        "provider_request_id": provider_request_id,
                        "remote_task_id_present": True,
                    },
                    call=call,
                )
            )
            uow.publish_after_commit(TaskChanged(task.id, polling.status, now))
            await uow.commit()
        return polling

    async def _cancel_remote(self, task: Task, attempt: TaskAttempt) -> Task:
        if task.model_id is None or task.remote_task_id is None:
            return await self._finish_cancel_failure(
                task,
                attempt,
                code="remote_identity_missing",
                message="远程任务标识缺失",
            )
        try:
            execution = await self._providers.resolve_execution(
                task.provider_id,
                task.model_id,
                task.operation,
                allow_inactive=True,
            )
        except (ProviderNotFoundError, ProviderInputError, ProviderOperationError) as exc:
            return await self._finish_cancel_failure(
                task,
                attempt,
                code=getattr(exc, "code", "provider_unavailable"),
                message=str(exc),
            )
        trace_id = self._trace_id()
        started = self._clock.monotonic()
        try:
            result = await execution.client.cancel_task(task.remote_task_id)
            return await self._finish_cancel_result(
                task,
                attempt,
                result,
                latency_ms=self._latency_ms(started),
                trace_id=trace_id,
                call=result.call,
            )
        except ProviderError as exc:
            return await self._finish_cancel_failure(
                task,
                attempt,
                code=exc.code,
                message=exc.user_message,
                latency_ms=self._latency_ms(started),
                trace_id=trace_id,
                call=exc.call,
            )
        except Exception:
            return await self._finish_cancel_failure(
                task,
                attempt,
                code="plugin_error",
                message="Provider 插件取消失败，请稍后重试",
                latency_ms=self._latency_ms(started),
                trace_id=trace_id,
            )
        finally:
            with suppress(Exception):
                await execution.close()

    async def _finish_cancel_result(
        self,
        task: Task,
        attempt: TaskAttempt,
        result: CancelResult,
        *,
        latency_ms: int,
        trace_id: str,
        call: ProviderCallInfo | None = None,
    ) -> Task:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            latest = await self._required_task(uow, task.id)
            target = TaskStatus.CANCELED if result.terminal else TaskStatus.POLLING
            updated = latest.transition(target, now)
            await uow.tasks.update(updated, expected_version=latest.row_version)
            await self._complete_attempt(uow, attempt, now)
            await uow.request_logs.add(
                self._request_log(
                    task,
                    attempt,
                    trace_id=trace_id,
                    latency_ms=latency_ms,
                    response={
                        "accepted": result.accepted,
                        "terminal": result.terminal,
                        "message": result.message,
                    },
                    call=call or result.call,
                )
            )
            uow.publish_after_commit(TaskChanged(task.id, updated.status, now))
            await uow.commit()
        return updated

    async def _finish_cancel_failure(
        self,
        task: Task,
        attempt: TaskAttempt,
        *,
        code: str,
        message: str,
        latency_ms: int = 0,
        trace_id: str | None = None,
        call: ProviderCallInfo | None = None,
    ) -> Task:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            latest = await self._required_task(uow, task.id)
            updated = latest.transition(TaskStatus.POLLING, now)
            failed = replace(
                attempt,
                status=AttemptStatus.FAILED,
                error_code=code,
                error_message=message,
                provider_error={},
                retryable=True,
                ended_at=now,
            )
            await uow.tasks.update(updated, expected_version=latest.row_version)
            await uow.attempts.update(failed, expected_status=AttemptStatus.RUNNING)
            await uow.request_logs.add(
                self._request_log(
                    task,
                    attempt,
                    trace_id=trace_id or self._trace_id(),
                    latency_ms=latency_ms,
                    response={"cancel_failed": True},
                    error_code=code,
                    call=call,
                )
            )
            uow.publish_after_commit(TaskChanged(task.id, updated.status, now))
            await uow.commit()
        return updated

    async def _finish_snapshot(
        self,
        task: Task,
        attempt: TaskAttempt,
        snapshot: RemoteTaskSnapshot,
        *,
        latency_ms: int,
        trace_id: str,
        artifact_allowed_hosts: tuple[str, ...],
    ) -> Task:
        if snapshot.state == "succeeded":
            if snapshot.output is None:  # pragma: no cover - SDK invariant
                raise TaskExecutionError("远程任务成功但没有输出")
            return await self._finish_output(
                task,
                attempt,
                snapshot.output,
                response_summary={"state": snapshot.state, "progress": snapshot.progress},
                latency_ms=latency_ms,
                trace_id=trace_id,
                call=snapshot.call,
                artifact_allowed_hosts=artifact_allowed_hosts,
            )
        if snapshot.state == "failed":
            if snapshot.error is None:  # pragma: no cover - SDK invariant
                raise TaskExecutionError("远程任务失败但没有错误")
            return await self._fail_attempt(
                task,
                attempt,
                code=snapshot.error.code,
                message=snapshot.error.message,
                retryable=snapshot.error.retryable,
                safe_details=snapshot.error.safe_details,
                latency_ms=latency_ms,
                trace_id=trace_id,
                call=snapshot.call,
            )
        now = self._clock.now()
        async with self._uow_factory() as uow:
            latest = await self._required_task(uow, task.id)
            if snapshot.state == "canceled":
                updated = latest.transition(TaskStatus.CANCELED, now, progress=snapshot.progress)
            else:
                poll_at = now + timedelta(seconds=snapshot.poll_after_seconds or 1.0)
                updated = latest.schedule_poll(
                    now,
                    progress=snapshot.progress,
                    poll_after_at=poll_at,
                )
            await uow.tasks.update(updated, expected_version=latest.row_version)
            await self._complete_attempt(uow, attempt, now)
            await uow.request_logs.add(
                self._request_log(
                    task,
                    attempt,
                    trace_id=trace_id,
                    latency_ms=latency_ms,
                    response={"state": snapshot.state, "progress": snapshot.progress},
                    call=snapshot.call,
                )
            )
            uow.publish_after_commit(TaskChanged(task.id, updated.status, now))
            await uow.commit()
        return updated

    async def _finish_output(
        self,
        task: Task,
        attempt: TaskAttempt,
        output: ProviderOutput,
        *,
        response_summary: Mapping[str, object],
        latency_ms: int,
        trace_id: str,
        call: ProviderCallInfo | None = None,
        artifact_allowed_hosts: tuple[str, ...] = (),
    ) -> Task:
        now = self._clock.now()
        artifacts = [
            await self._artifacts.write(
                task_id=task.id,
                artifact_id=self._ids.new(),
                remote=remote,
                created_at=now,
                allowed_hosts=artifact_allowed_hosts,
                trace_id=trace_id,
            )
            for remote in output.artifacts
        ]
        normalized = {
            "data": output.data,
            "finish_reason": output.finish_reason,
            "artifact_ids": [artifact.id for artifact in artifacts],
        }
        usage = output.usage
        async with self._uow_factory() as uow:
            latest = await self._required_task(uow, task.id)
            success = latest.transition(
                TaskStatus.SUCCESS,
                now,
                normalized_output=normalized,
            )
            await uow.tasks.update(success, expected_version=latest.row_version)
            await self._complete_attempt(uow, attempt, now)
            for artifact in artifacts:
                await uow.artifacts.add(artifact)
            await uow.request_logs.add(
                self._request_log(
                    task,
                    attempt,
                    trace_id=trace_id,
                    latency_ms=latency_ms,
                    response={
                        **dict(response_summary),
                        "artifact_count": len(artifacts),
                        "finish_reason": output.finish_reason,
                    },
                    usage={} if usage is None else usage.units,
                    amount_micros=None if usage is None else usage.cost_micros,
                    currency=None if usage is None else usage.currency,
                    call=call,
                )
            )
            uow.publish_after_commit(TaskChanged(task.id, success.status, now))
            await uow.commit()
        return success

    async def _fail_provider_error(
        self,
        task: Task,
        attempt: TaskAttempt,
        error: ProviderError,
        latency_ms: int,
        trace_id: str,
    ) -> Task:
        return await self._fail_attempt(
            task,
            attempt,
            code=error.code,
            message=error.user_message,
            retryable=error.retryable,
            retry_after_seconds=error.retry_after_seconds,
            safe_details={
                **dict(error.safe_details),
                "provider_code": error.provider_code,
                "provider_request_id": error.provider_request_id,
            },
            latency_ms=latency_ms,
            trace_id=trace_id,
            call=error.call,
        )

    async def _fail_attempt(
        self,
        task: Task,
        attempt: TaskAttempt,
        *,
        code: str,
        message: str,
        retryable: bool,
        safe_details: Mapping[str, object],
        latency_ms: int,
        trace_id: str,
        retry_after_seconds: float | None = None,
        force_attention: bool = False,
        call: ProviderCallInfo | None = None,
    ) -> Task:
        now = self._clock.now()
        can_retry = (
            retryable and attempt.attempt_no < self._max_attempts and not self._expired(task)
        )
        retry_at = now + timedelta(seconds=retry_after_seconds or 1.0) if can_retry else None
        async with self._uow_factory() as uow:
            latest = await self._required_task(uow, task.id)
            target = (
                TaskStatus.RETRY_WAIT
                if can_retry
                else TaskStatus.NEEDS_ATTENTION
                if force_attention
                else TaskStatus.FAILED
            )
            try:
                updated = latest.transition(target, now, poll_after_at=retry_at)
            except TaskTransitionError:
                updated = latest.transition(TaskStatus.NEEDS_ATTENTION, now)
            failed_attempt = replace(
                attempt,
                status=AttemptStatus.FAILED,
                error_code=code,
                error_message=message,
                provider_error=safe_details,
                retryable=retryable,
                retry_after_at=retry_at,
                ended_at=now,
            )
            await uow.tasks.update(updated, expected_version=latest.row_version)
            await uow.attempts.update(
                failed_attempt,
                expected_status=AttemptStatus.RUNNING,
            )
            await uow.request_logs.add(
                self._request_log(
                    task,
                    attempt,
                    trace_id=trace_id,
                    latency_ms=latency_ms,
                    response={"failed": True, "retryable": retryable},
                    error_code=code,
                    call=call,
                )
            )
            uow.publish_after_commit(TaskChanged(task.id, updated.status, now))
            await uow.commit()
        return updated

    async def _mark_timed_out(
        self,
        task_id: str,
        *,
        attempt: TaskAttempt | None = None,
        trace_id: str | None = None,
    ) -> Task:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            latest = await self._required_task(uow, task_id)
            timed_out = latest.transition(TaskStatus.TIMED_OUT, now)
            await uow.tasks.update(timed_out, expected_version=latest.row_version)
            if attempt is not None:
                failed = replace(
                    attempt,
                    status=AttemptStatus.FAILED,
                    error_code="timeout",
                    error_message="任务超过截止时间",
                    provider_error={},
                    retryable=False,
                    ended_at=now,
                )
                await uow.attempts.update(failed, expected_status=AttemptStatus.RUNNING)
                await uow.request_logs.add(
                    self._request_log(
                        latest,
                        attempt,
                        trace_id=trace_id or self._trace_id(),
                        latency_ms=0,
                        response={"timed_out": True},
                        error_code="timeout",
                    )
                )
            uow.publish_after_commit(TaskChanged(task_id, timed_out.status, now))
            await uow.commit()
        return timed_out

    async def _queue_created(self, task_id: str) -> Task:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            latest = await self._required_task(uow, task_id)
            queued = latest.transition(TaskStatus.QUEUED, now)
            await uow.tasks.update(queued, expected_version=latest.row_version)
            uow.publish_after_commit(TaskChanged(task_id, queued.status, now))
            await uow.commit()
        return queued

    async def _mark_recovering(self, task_id: str) -> Task:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            latest = await self._required_task(uow, task_id)
            await self._close_interrupted_attempts(uow, task_id, now)
            recovering = latest.transition(TaskStatus.RECOVERING, now)
            await uow.tasks.update(recovering, expected_version=latest.row_version)
            uow.publish_after_commit(TaskChanged(task_id, recovering.status, now))
            await uow.commit()
        return recovering

    async def _recover_uncertain_submit(self, task: Task) -> Task:
        if task.normalized_output is not None:
            return await self._mark_needs_attention(
                task.id,
                "本地已有输出摘要但任务未完成，禁止自动重复提交",
            )
        if task.model_id is None:
            return await self._mark_needs_attention(task.id, "任务缺少模型，无法安全恢复")
        try:
            execution = await self._providers.resolve_execution(
                task.provider_id,
                task.model_id,
                task.operation,
                allow_inactive=True,
            )
        except (ProviderNotFoundError, ProviderInputError, ProviderOperationError):
            return await self._mark_needs_attention(
                task.id,
                "Provider 或模型不可用，无法确认提交结果",
            )
        try:
            if execution.descriptor.idempotency == "none":
                return await self._mark_needs_attention(
                    task.id,
                    "Provider 不支持幂等提交，已禁止自动重复计费",
                )
        finally:
            with suppress(Exception):
                await execution.close()
        now = self._clock.now()
        async with self._uow_factory() as uow:
            latest = await self._required_task(uow, task.id)
            await self._close_interrupted_attempts(uow, task.id, now)
            retrying = latest.transition(
                TaskStatus.RETRY_WAIT,
                now,
                poll_after_at=now,
            )
            await uow.tasks.update(retrying, expected_version=latest.row_version)
            uow.publish_after_commit(TaskChanged(task.id, retrying.status, now))
            await uow.commit()
        return retrying

    async def _mark_needs_attention(self, task_id: str, message: str) -> Task:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            latest = await self._required_task(uow, task_id)
            await self._close_interrupted_attempts(uow, task_id, now)
            attention = latest.transition(TaskStatus.NEEDS_ATTENTION, now)
            await uow.tasks.update(attention, expected_version=latest.row_version)
            await uow.request_logs.add(
                RequestLog(
                    id=self._ids.new(),
                    attempt_id=None,
                    provider_id=latest.provider_id,
                    model_id=latest.model_id,
                    trace_id=self._trace_id(),
                    operation=latest.operation,
                    method=None,
                    url_template=None,
                    http_status=None,
                    latency_ms=0,
                    request_summary={"recovery": True},
                    response_summary={"message": message, "next_action": "人工确认"},
                    usage={},
                    amount_micros=None,
                    currency=None,
                    error_code="recovery_uncertain",
                    created_at=now,
                )
            )
            uow.publish_after_commit(TaskChanged(task_id, attention.status, now))
            await uow.commit()
        return attention

    async def _close_interrupted_attempts(
        self,
        uow: TaskUnitOfWork,
        task_id: str,
        ended_at: datetime,
    ) -> None:
        for attempt in await uow.attempts.list_for_task(task_id):
            if attempt.status is not AttemptStatus.RUNNING:
                continue
            interrupted = replace(
                attempt,
                status=AttemptStatus.FAILED,
                error_code="process_interrupted",
                error_message="进程在操作完成前退出，已进入恢复流程",
                provider_error={},
                retryable=True,
                retry_after_at=ended_at,
                ended_at=ended_at,
            )
            await uow.attempts.update(
                interrupted,
                expected_status=AttemptStatus.RUNNING,
            )

    async def _complete_attempt(
        self, uow: TaskUnitOfWork, attempt: TaskAttempt, ended_at: datetime
    ) -> None:
        completed = replace(attempt, status=AttemptStatus.SUCCESS, ended_at=ended_at)
        await uow.attempts.update(
            completed,
            expected_status=AttemptStatus.RUNNING,
        )

    async def _required_task(self, uow: TaskUnitOfWork, task_id: str) -> Task:
        task = await uow.tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError("任务不存在")
        return task

    def _request_log(
        self,
        task: Task,
        attempt: TaskAttempt,
        *,
        trace_id: str,
        latency_ms: int,
        response: Mapping[str, object],
        usage: Mapping[str, object] | None = None,
        amount_micros: int | None = None,
        currency: str | None = None,
        error_code: str | None = None,
        call: ProviderCallInfo | None = None,
    ) -> RequestLog:
        return RequestLog(
            id=self._ids.new(),
            attempt_id=attempt.id,
            provider_id=task.provider_id,
            model_id=task.model_id,
            trace_id=trace_id,
            operation=task.operation,
            method=None if call is None else call.method,
            url_template=None if call is None else call.url_template,
            http_status=None if call is None else call.http_status,
            latency_ms=latency_ms,
            request_summary={
                "field_names": sorted(task.input),
                "field_types": {key: type(value).__name__ for key, value in task.input.items()},
            },
            response_summary=response,
            usage={} if usage is None else usage,
            amount_micros=amount_micros,
            currency=currency,
            error_code=error_code,
            created_at=self._clock.now(),
        )

    async def _wait_until_ready(self, task: Task) -> None:
        if task.poll_after_at is None:
            return
        seconds = (task.poll_after_at - self._clock.now()).total_seconds()
        if seconds > 0:
            await asyncio.sleep(min(seconds, 30.0))

    def _expired(self, task: Task) -> bool:
        return task.timeout_at is not None and self._clock.now() >= task.timeout_at

    def _remaining_seconds(self, task: Task) -> float:
        if task.timeout_at is None:
            return 300.0
        remaining = (task.timeout_at - self._clock.now()).total_seconds()
        return max(0.001, remaining)

    def _latency_ms(self, started: float) -> int:
        return max(0, int((self._clock.monotonic() - started) * 1000))

    def _trace_id(self) -> str:
        trace_id = current_trace_id()
        if trace_id is not None:
            return trace_id
        with self._traces.start() as generated:
            return generated


def _matches_explicit_intent(task: Task, command: CreateTask) -> bool:
    return (
        task.provider_id == command.provider_id
        and task.model_id == command.model_id
        and task.operation == command.operation
        and all(task.input.get(name) == value for name, value in command.inputs.items())
    )
