"""Durable DAG scheduling, Task delegation, recovery, and Artifact lineage."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, cast

from jsonschema import Draft202012Validator

from astraweft.application.comfyui import (
    ComfyUIInputError,
    ComfyUIOperationError,
    ComfyUIService,
    EnsureComfyUIExecution,
)
from astraweft.application.tasks import (
    CreateTask,
    TaskInputError,
    TaskNotFoundError,
    TaskService,
)
from astraweft.application.workflows.commands import StartWorkflowRun
from astraweft.application.workflows.events import NodeRunChanged, WorkflowRunChanged
from astraweft.application.workflows.transforms import (
    TransformConfigurationError,
    execute_transform,
)
from astraweft.domain.comfyui import ComfyUIExecution, ComfyUIExecutionStatus
from astraweft.domain.task import Task, TaskStatus
from astraweft.domain.workflow import (
    ArtifactLink,
    ArtifactLinkDirection,
    NodeRun,
    NodeRunStatus,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeType,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowVersion,
    WorkflowVersionStatus,
    definition_checksum,
    ports_from_schema,
)
from astraweft.ports.runtime import Clock, IdGenerator
from astraweft.ports.workflows import WorkflowUnitOfWorkFactory


class WorkflowRunNotFoundError(LookupError):
    """A workflow run or one of its immutable definition records is absent."""


class WorkflowRunInputError(ValueError):
    """A workflow run input or resolved node value violates its frozen Schema."""


@dataclass(frozen=True, slots=True)
class WorkflowRunSnapshot:
    run: WorkflowRun
    version: WorkflowVersion
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]
    node_runs: tuple[NodeRun, ...]
    artifact_links: tuple[ArtifactLink, ...]


class WorkflowExecutionService:
    """Advance a persisted graph without evaluating user-authored code."""

    def __init__(
        self,
        *,
        uow_factory: WorkflowUnitOfWorkFactory,
        tasks: TaskService,
        comfyui: ComfyUIService | None = None,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._uow_factory = uow_factory
        self._tasks = tasks
        self._comfyui = comfyui
        self._clock = clock
        self._ids = ids
        self._locks: dict[str, asyncio.Lock] = {}

    async def list_runs(self, *, limit: int = 1000) -> tuple[WorkflowRun, ...]:
        async with self._uow_factory() as uow:
            return await uow.runs.list_recent(limit=limit)

    async def list_active_runs(self, *, limit: int = 1000) -> tuple[WorkflowRun, ...]:
        async with self._uow_factory() as uow:
            return await uow.runs.list_by_status(
                frozenset(
                    {
                        WorkflowRunStatus.CREATED,
                        WorkflowRunStatus.RUNNING,
                        WorkflowRunStatus.WAITING,
                    }
                ),
                limit=limit,
            )

    async def get_run(self, run_id: str) -> WorkflowRunSnapshot:
        async with self._uow_factory() as uow:
            run = await uow.runs.get(run_id)
            if run is None:
                raise WorkflowRunNotFoundError("工作流运行不存在")
            version = await uow.definitions.get_version(run.workflow_version_id)
            if version is None:
                raise WorkflowRunNotFoundError("运行引用的工作流版本不存在")
            nodes = await uow.definitions.get_nodes(version.id)
            edges = await uow.definitions.get_edges(version.id)
            node_runs = await uow.runs.list_node_runs(run.id)
            links: list[ArtifactLink] = []
            for node_run in node_runs:
                links.extend(await uow.runs.list_artifact_links(node_run.id))
        return WorkflowRunSnapshot(run, version, nodes, edges, node_runs, tuple(links))

    async def start(self, command: StartWorkflowRun) -> WorkflowRunSnapshot:
        async with self._uow_factory() as uow:
            version = await uow.definitions.get_version(command.version_id)
            if version is None:
                raise WorkflowRunNotFoundError("工作流版本不存在")
            if version.status not in {
                WorkflowVersionStatus.PUBLISHED,
                WorkflowVersionStatus.ARCHIVED,
            }:
                raise WorkflowRunInputError("只有已发布的工作流版本可以运行")
            workflow = await uow.definitions.get(version.workflow_id)
            if workflow is None:
                raise WorkflowRunNotFoundError("工作流不存在或已删除")
            nodes = await uow.definitions.get_nodes(version.id)
            edges = await uow.definitions.get_edges(version.id)
        _validate_instance(command.inputs, version.input_schema, "工作流输入")
        if definition_checksum(version, nodes, edges) != version.checksum:
            raise WorkflowRunInputError("工作流定义校验和不一致，已阻止运行")

        now = self._clock.now()
        created = WorkflowRun(
            id=self._ids.new(),
            workflow_id=workflow.id,
            workflow_version_id=version.id,
            status=WorkflowRunStatus.CREATED,
            input=command.inputs,
            output=None,
            definition_checksum=version.checksum,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        node_runs = tuple(
            NodeRun(
                id=self._ids.new(),
                workflow_run_id=created.id,
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
            for node in nodes
        )
        running = created.transition(WorkflowRunStatus.RUNNING, now)
        async with self._uow_factory() as uow:
            await uow.runs.add(created)
            await uow.runs.add_node_runs(node_runs)
            await uow.runs.update(running, expected_version=created.row_version)
            uow.publish_after_commit(WorkflowRunChanged(running.id, running.status, now))
            await uow.commit()
        return WorkflowRunSnapshot(running, version, nodes, edges, node_runs, ())

    async def advance(self, run_id: str) -> WorkflowRunSnapshot:
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            return await self._advance_locked(run_id)

    async def cancel(self, run_id: str) -> WorkflowRunSnapshot:
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            snapshot = await self.get_run(run_id)
            if snapshot.run.status.terminal:
                return snapshot
            now = self._clock.now()
            requested = snapshot.run.request_cancel(now)
            await self._persist_run(snapshot.run, requested)
            for node_run in snapshot.node_runs:
                if node_run.task_id is not None and not node_run.status.terminal:
                    with suppress(Exception):
                        await self._tasks.cancel(node_run.task_id)
                if (
                    self._comfyui is not None
                    and node_run.comfyui_execution_id is not None
                    and not node_run.status.terminal
                ):
                    with suppress(Exception):
                        await self._comfyui.cancel_execution(node_run.comfyui_execution_id)
            refreshed = await self.get_run(run_id)
            node_updates = tuple(
                (node_run, node_run.transition(NodeRunStatus.CANCELED, self._clock.now()))
                for node_run in refreshed.node_runs
                if not node_run.status.terminal
            )
            if node_updates:
                await self._persist_nodes(node_updates, ())
            final_snapshot = await self.get_run(run_id)
            canceled = final_snapshot.run.transition(WorkflowRunStatus.CANCELED, self._clock.now())
            await self._persist_run(final_snapshot.run, canceled)
            return await self.get_run(run_id)

    async def _advance_locked(self, run_id: str) -> WorkflowRunSnapshot:
        snapshot = await self.get_run(run_id)
        if snapshot.run.status.terminal:
            return snapshot
        if snapshot.run.status is WorkflowRunStatus.CREATED:
            running_run = snapshot.run.transition(WorkflowRunStatus.RUNNING, self._clock.now())
            await self._persist_run(snapshot.run, running_run)
            snapshot = await self.get_run(run_id)

        by_node = {item.workflow_node_id: item for item in snapshot.node_runs}
        node_defs = {item.id: item for item in snapshot.nodes}

        recovery_updates: list[tuple[NodeRun, NodeRun]] = []
        for node_run in tuple(by_node.values()):
            if node_run.status is not NodeRunStatus.RUNNING:
                continue
            node = node_defs[node_run.workflow_node_id]
            if node.node_type is WorkflowNodeType.COMFYUI:
                recovered = await self._recover_comfyui_node(
                    snapshot.version,
                    node,
                    node_run,
                )
                if recovered is not None:
                    recovery_updates.append((node_run, recovered))
                    by_node[node_run.workflow_node_id] = recovered
                continue
            if node.node_type is not WorkflowNodeType.PROVIDER_MODEL:
                continue
            if node_run.planned_task_id is None or node_run.resolved_input is None:
                failed = node_run.transition(
                    NodeRunStatus.FAILED,
                    self._clock.now(),
                    error_code="execution_intent_missing",
                    error_message="节点缺少可恢复的任务执行意图",
                )
                recovery_updates.append((node_run, failed))
                by_node[node_run.workflow_node_id] = failed
                continue
            if node_run.task_id is None:
                try:
                    task = await self._ensure_task(node, node_run)
                    attached = node_run.attach_task(self._clock.now(), task.id)
                except (TaskInputError, TaskNotFoundError, ValueError) as exc:
                    attached = node_run.transition(
                        NodeRunStatus.FAILED,
                        self._clock.now(),
                        error_code="task_intent_failed",
                        error_message=str(exc),
                    )
                recovery_updates.append((node_run, attached))
                by_node[node_run.workflow_node_id] = attached
        if recovery_updates:
            await self._persist_nodes(tuple(recovery_updates), ())

        reconciliation: list[tuple[NodeRun, NodeRun]] = []
        output_links: list[ArtifactLink] = []
        for node_run in tuple(by_node.values()):
            node_definition = node_defs[node_run.workflow_node_id]
            if (
                node_definition.node_type is not WorkflowNodeType.PROVIDER_MODEL
                or node_run.status is not NodeRunStatus.RUNNING
                or node_run.task_id is None
            ):
                continue
            try:
                task = await self._tasks.get(node_run.task_id)
            except TaskNotFoundError:
                failed = node_run.transition(
                    NodeRunStatus.FAILED,
                    self._clock.now(),
                    error_code="task_missing",
                    error_message="节点关联的任务不存在",
                )
                reconciliation.append((node_run, failed))
                by_node[node_run.workflow_node_id] = failed
                continue
            terminal, new_links = await self._node_from_task(
                node_run,
                node_definition,
                task,
            )
            if terminal is not None:
                reconciliation.append((node_run, terminal))
                output_links.extend(new_links)
                by_node[node_run.workflow_node_id] = terminal
        if reconciliation:
            await self._persist_nodes(tuple(reconciliation), tuple(output_links))

        comfyui_updates: list[tuple[NodeRun, NodeRun]] = []
        comfyui_links: list[ArtifactLink] = []
        for node_run in tuple(by_node.values()):
            node_definition = node_defs[node_run.workflow_node_id]
            if (
                node_definition.node_type is not WorkflowNodeType.COMFYUI
                or node_run.status is not NodeRunStatus.RUNNING
                or node_run.comfyui_execution_id is None
                or self._comfyui is None
            ):
                continue
            try:
                execution = await self._comfyui.advance_execution(node_run.comfyui_execution_id)
                terminal, new_links = self._node_from_comfyui(
                    node_run,
                    node_definition,
                    execution,
                )
            except (ComfyUIInputError, ComfyUIOperationError, ValueError) as exc:
                terminal = node_run.transition(
                    NodeRunStatus.FAILED,
                    self._clock.now(),
                    error_code="comfyui_execution_failed",
                    error_message=str(exc),
                )
                new_links = ()
            if terminal is not None:
                comfyui_updates.append((node_run, terminal))
                comfyui_links.extend(new_links)
                by_node[node_run.workflow_node_id] = terminal
        if comfyui_updates:
            await self._persist_nodes(tuple(comfyui_updates), tuple(comfyui_links))

        incoming: dict[str, list[WorkflowEdge]] = defaultdict(list)
        for edge in snapshot.edges:
            incoming[edge.target_node_id].append(edge)
        scheduling: list[tuple[NodeRun, NodeRun]] = []
        for node in snapshot.nodes:
            node_run = by_node[node.id]
            if node_run.status is not NodeRunStatus.PENDING:
                continue
            upstream = [by_node[edge.source_node_id] for edge in incoming[node.id]]
            if any(
                item.status.terminal and item.status is not NodeRunStatus.SUCCESS
                for item in upstream
            ):
                updated = node_run.transition(
                    NodeRunStatus.SKIPPED,
                    self._clock.now(),
                    error_code="upstream_failed",
                    error_message="上游节点未成功，依赖节点已跳过",
                )
            elif all(item.status is NodeRunStatus.SUCCESS for item in upstream):
                updated = node_run.transition(NodeRunStatus.READY, self._clock.now())
            else:
                continue
            scheduling.append((node_run, updated))
            by_node[node.id] = updated
        if scheduling:
            await self._persist_nodes(tuple(scheduling), ())

        starting: list[tuple[NodeRun, NodeRun]] = []
        input_links: list[ArtifactLink] = []
        for node in snapshot.nodes:
            node_run = by_node[node.id]
            if node_run.status is not NodeRunStatus.READY:
                continue
            try:
                resolved = _resolve_inputs(
                    node,
                    snapshot.run,
                    by_node,
                    incoming[node.id],
                )
                _validate_instance(resolved, node.input_schema, f"节点 {node.node_key} 输入")
                running_node = node_run.transition(
                    NodeRunStatus.RUNNING,
                    self._clock.now(),
                    resolved_input=resolved,
                    planned_task_id=(
                        self._ids.new()
                        if node.node_type is WorkflowNodeType.PROVIDER_MODEL
                        else None
                    ),
                    planned_comfyui_execution_id=(
                        self._ids.new() if node.node_type is WorkflowNodeType.COMFYUI else None
                    ),
                )
                input_links.extend(self._input_links(running_node, incoming[node.id], by_node))
            except (WorkflowRunInputError, ValueError) as exc:
                running_node = node_run.transition(
                    NodeRunStatus.FAILED,
                    self._clock.now(),
                    error_code="input_resolution_failed",
                    error_message=str(exc),
                )
            starting.append((node_run, running_node))
            by_node[node.id] = running_node
        if starting:
            await self._persist_nodes(tuple(starting), tuple(input_links))

        execution_updates: list[tuple[NodeRun, NodeRun]] = []
        for node in snapshot.nodes:
            node_run = by_node[node.id]
            if node_run.status is not NodeRunStatus.RUNNING:
                continue
            if node.node_type is WorkflowNodeType.TRANSFORM and node_run.task_id is None:
                try:
                    if node_run.resolved_input is None:
                        raise TransformConfigurationError("转换节点缺少解析输入")
                    output = execute_transform(node.config, node_run.resolved_input)
                    _validate_instance(output, node.output_schema, f"节点 {node.node_key} 输出")
                    completed = node_run.transition(
                        NodeRunStatus.SUCCESS,
                        self._clock.now(),
                        output=output,
                    )
                except (TransformConfigurationError, WorkflowRunInputError) as exc:
                    completed = node_run.transition(
                        NodeRunStatus.FAILED,
                        self._clock.now(),
                        error_code="transform_failed",
                        error_message=str(exc),
                    )
                execution_updates.append((node_run, completed))
                by_node[node.id] = completed
            elif node.node_type is WorkflowNodeType.PROVIDER_MODEL and node_run.task_id is None:
                try:
                    task = await self._ensure_task(node, node_run)
                    completed = node_run.attach_task(self._clock.now(), task.id)
                except (TaskInputError, TaskNotFoundError, ValueError) as exc:
                    completed = node_run.transition(
                        NodeRunStatus.FAILED,
                        self._clock.now(),
                        error_code="task_creation_failed",
                        error_message=str(exc),
                    )
                execution_updates.append((node_run, completed))
                by_node[node.id] = completed
            elif (
                node.node_type is WorkflowNodeType.COMFYUI and node_run.comfyui_execution_id is None
            ):
                try:
                    execution = await self._ensure_comfyui_execution(
                        snapshot.version,
                        node,
                        node_run,
                    )
                    completed = node_run.attach_comfyui_execution(self._clock.now(), execution.id)
                except (ComfyUIInputError, ComfyUIOperationError, ValueError) as exc:
                    completed = node_run.transition(
                        NodeRunStatus.FAILED,
                        self._clock.now(),
                        error_code="comfyui_intent_failed",
                        error_message=str(exc),
                    )
                execution_updates.append((node_run, completed))
                by_node[node.id] = completed
        if execution_updates:
            await self._persist_nodes(tuple(execution_updates), ())

        final_run = await self._finish_if_terminal(
            snapshot.run, snapshot.version, by_node, snapshot.nodes
        )
        if final_run is not None:
            await self._persist_run(snapshot.run, final_run)
        return await self.get_run(run_id)

    async def _recover_comfyui_node(
        self,
        version: WorkflowVersion,
        node: WorkflowNode,
        node_run: NodeRun,
    ) -> NodeRun | None:
        if self._comfyui is None:
            return node_run.transition(
                NodeRunStatus.FAILED,
                self._clock.now(),
                error_code="comfyui_unavailable",
                error_message="ComfyUI 执行器未启用",
            )
        if node_run.planned_comfyui_execution_id is None or node_run.resolved_input is None:
            return node_run.transition(
                NodeRunStatus.FAILED,
                self._clock.now(),
                error_code="comfyui_intent_missing",
                error_message="节点缺少可恢复的 ComfyUI 执行意图",
            )
        if node_run.comfyui_execution_id is not None:
            try:
                await self._comfyui.get_execution(node_run.comfyui_execution_id)
            except ComfyUIOperationError as exc:
                return node_run.transition(
                    NodeRunStatus.FAILED,
                    self._clock.now(),
                    error_code="comfyui_execution_missing",
                    error_message=str(exc),
                )
            return None
        try:
            execution = await self._ensure_comfyui_execution(version, node, node_run)
            return node_run.attach_comfyui_execution(self._clock.now(), execution.id)
        except (ComfyUIInputError, ComfyUIOperationError, ValueError) as exc:
            return node_run.transition(
                NodeRunStatus.FAILED,
                self._clock.now(),
                error_code="comfyui_intent_failed",
                error_message=str(exc),
            )

    async def _ensure_comfyui_execution(
        self,
        version: WorkflowVersion,
        node: WorkflowNode,
        node_run: NodeRun,
    ) -> ComfyUIExecution:
        if (
            self._comfyui is None
            or node_run.planned_comfyui_execution_id is None
            or node_run.resolved_input is None
        ):
            raise WorkflowRunInputError("ComfyUI 节点执行意图不完整")
        config = node.config
        instance_id = config.get("instance_id")
        template_id = config.get("template_id")
        template_checksum = config.get("template_checksum")
        prompt = config.get("prompt")
        output_nodes = config.get("output_nodes")
        input_targets = config.get("input_targets")
        if (
            not isinstance(instance_id, str)
            or (template_id is not None and not isinstance(template_id, str))
            or not isinstance(template_checksum, str)
            or not isinstance(prompt, Mapping)
            or not isinstance(output_nodes, Sequence)
            or isinstance(output_nodes, (str, bytes, bytearray))
            or not all(isinstance(value, str) for value in output_nodes)
            or not isinstance(input_targets, Mapping)
        ):
            raise WorkflowRunInputError("ComfyUI 节点配置不完整")
        return await self._comfyui.ensure_execution(
            EnsureComfyUIExecution(
                execution_id=node_run.planned_comfyui_execution_id,
                node_run_id=node_run.id,
                instance_id=instance_id,
                template_id=template_id,
                template_checksum=template_checksum,
                workflow_checksum=version.checksum,
                prompt=prompt,
                output_nodes=tuple(output_nodes),
                input_targets=input_targets,
                inputs=node_run.resolved_input,
            )
        )

    def _node_from_comfyui(
        self,
        node_run: NodeRun,
        node_definition: WorkflowNode,
        execution: ComfyUIExecution,
    ) -> tuple[NodeRun | None, tuple[ArtifactLink, ...]]:
        if not execution.status.terminal:
            return None, ()
        now = self._clock.now()
        if execution.status is ComfyUIExecutionStatus.SUCCESS:
            output = execution.output
            if output is None:
                return (
                    node_run.transition(
                        NodeRunStatus.FAILED,
                        now,
                        error_code="comfyui_output_missing",
                        error_message="ComfyUI 成功记录缺少本地输出",
                    ),
                    (),
                )
            try:
                _validate_instance(
                    output,
                    node_definition.output_schema,
                    f"节点 {node_definition.node_key} 输出",
                )
            except WorkflowRunInputError as exc:
                return (
                    node_run.transition(
                        NodeRunStatus.FAILED,
                        now,
                        error_code="comfyui_output_invalid",
                        error_message=str(exc),
                    ),
                    (),
                )
            completed = node_run.transition(NodeRunStatus.SUCCESS, now, output=output)
            links = tuple(
                ArtifactLink(
                    id=self._ids.new(),
                    node_run_id=node_run.id,
                    artifact_id=artifact_id,
                    direction=ArtifactLinkDirection.OUTPUT,
                    port_name="artifacts",
                    created_at=now,
                )
                for artifact_id in execution.artifact_ids
            )
            return completed, links
        if execution.status is ComfyUIExecutionStatus.CANCELED:
            return node_run.transition(NodeRunStatus.CANCELED, now), ()
        return (
            node_run.transition(
                NodeRunStatus.FAILED,
                now,
                error_code=execution.error_code or "comfyui_failed",
                error_message=execution.error_message
                or f"ComfyUI 执行以 {execution.status.value} 结束",
            ),
            (),
        )

    async def _ensure_task(self, node: WorkflowNode, node_run: NodeRun) -> Task:
        if (
            node.provider_id is None
            or node.model_id is None
            or node.operation is None
            or node_run.planned_task_id is None
            or node_run.resolved_input is None
        ):
            raise WorkflowRunInputError("Provider 节点执行意图不完整")
        return await self._tasks.create(
            CreateTask(
                provider_id=node.provider_id,
                model_id=node.model_id,
                operation=node.operation,
                inputs=node_run.resolved_input,
                task_id=node_run.planned_task_id,
            )
        )

    async def _node_from_task(
        self,
        node_run: NodeRun,
        node_definition: WorkflowNode,
        task: Task,
    ) -> tuple[NodeRun | None, tuple[ArtifactLink, ...]]:
        if not task.status.terminal:
            return None, ()
        now = self._clock.now()
        if task.status is TaskStatus.SUCCESS:
            try:
                output, artifact_ids = await self._task_output(task)
                _validate_instance(
                    output,
                    node_definition.output_schema,
                    f"节点 {node_definition.node_key} 输出",
                )
                node = node_run.transition(NodeRunStatus.SUCCESS, now, output=output)
                links = tuple(
                    ArtifactLink(
                        id=self._ids.new(),
                        node_run_id=node_run.id,
                        artifact_id=artifact_id,
                        direction=ArtifactLinkDirection.OUTPUT,
                        port_name="artifacts",
                        created_at=now,
                    )
                    for artifact_id in artifact_ids
                )
                return node, links
            except WorkflowRunInputError as exc:
                return (
                    node_run.transition(
                        NodeRunStatus.FAILED,
                        now,
                        error_code="task_output_invalid",
                        error_message=str(exc),
                    ),
                    (),
                )
        if task.status is TaskStatus.CANCELED:
            return node_run.transition(NodeRunStatus.CANCELED, now), ()
        return (
            node_run.transition(
                NodeRunStatus.FAILED,
                now,
                error_code=f"task_{task.status.value.lower()}",
                error_message=f"Provider 任务以 {task.status.value} 结束",
            ),
            (),
        )

    async def _task_output(self, task: Task) -> tuple[Mapping[str, object], tuple[str, ...]]:
        normalized = task.normalized_output
        if normalized is None:
            raise WorkflowRunInputError("成功任务缺少规范化 data 输出")
        data_value = normalized.get("data")
        if not isinstance(data_value, Mapping):
            raise WorkflowRunInputError("成功任务缺少规范化 data 输出")
        data = {str(key): value for key, value in data_value.items()}
        artifact_value = normalized.get("artifact_ids", ())
        if not isinstance(artifact_value, Sequence) or isinstance(
            artifact_value, (str, bytes, bytearray)
        ):
            raise WorkflowRunInputError("成功任务的 artifact_ids 无效")
        artifact_ids = tuple(item for item in artifact_value if isinstance(item, str))
        if len(artifact_ids) != len(artifact_value):
            raise WorkflowRunInputError("成功任务包含无效 Artifact ID")
        persisted = {item.id for item in await self._tasks.list_artifacts(task.id)}
        if any(item not in persisted for item in artifact_ids):
            raise WorkflowRunInputError("任务输出引用的 Artifact 尚未本地持久化")
        data["artifacts"] = list(artifact_ids)
        return data, artifact_ids

    def _input_links(
        self,
        node_run: NodeRun,
        edges: Sequence[WorkflowEdge],
        by_node: Mapping[str, NodeRun],
    ) -> tuple[ArtifactLink, ...]:
        now = self._clock.now()
        links: list[ArtifactLink] = []
        for edge in edges:
            if edge.source_port != "artifacts":
                continue
            upstream = by_node[edge.source_node_id]
            if upstream.output is None:
                continue
            value = upstream.output.get("artifacts", ())
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
                continue
            links.extend(
                ArtifactLink(
                    id=self._ids.new(),
                    node_run_id=node_run.id,
                    artifact_id=artifact_id,
                    direction=ArtifactLinkDirection.INPUT,
                    port_name=edge.target_port,
                    created_at=now,
                )
                for artifact_id in value
                if isinstance(artifact_id, str)
            )
        return tuple(links)

    async def _finish_if_terminal(
        self,
        run: WorkflowRun,
        version: WorkflowVersion,
        by_node: Mapping[str, NodeRun],
        nodes: tuple[WorkflowNode, ...],
    ) -> WorkflowRun | None:
        if not all(item.status.terminal for item in by_node.values()):
            return None
        now = self._clock.now()
        if any(item.status is not NodeRunStatus.SUCCESS for item in by_node.values()):
            return run.transition(WorkflowRunStatus.FAILED, now)
        node_by_key = {node.node_key: node for node in nodes}
        output: dict[str, object] = {}
        for name, binding in version.output_bindings.items():
            if not isinstance(binding, Mapping):
                return run.transition(WorkflowRunStatus.FAILED, now)
            node_key = binding.get("node")
            port = binding.get("port")
            if not isinstance(node_key, str) or not isinstance(port, str):
                return run.transition(WorkflowRunStatus.FAILED, now)
            node = node_by_key.get(node_key)
            if node is None:
                return run.transition(WorkflowRunStatus.FAILED, now)
            node_output = by_node[node.id].output
            if node_output is None or port not in node_output:
                return run.transition(WorkflowRunStatus.FAILED, now)
            output[name] = node_output[port]
        try:
            _validate_instance(output, version.output_schema, "工作流输出")
        except WorkflowRunInputError:
            return run.transition(WorkflowRunStatus.FAILED, now)
        return run.transition(WorkflowRunStatus.SUCCESS, now, output=output)

    async def _persist_nodes(
        self,
        updates: tuple[tuple[NodeRun, NodeRun], ...],
        links: tuple[ArtifactLink, ...],
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.runs.update_node_runs(
                tuple((updated, previous.row_version) for previous, updated in updates)
            )
            for _previous, updated in updates:
                uow.publish_after_commit(
                    NodeRunChanged(
                        updated.workflow_run_id,
                        updated.id,
                        updated.status,
                        updated.updated_at,
                    )
                )
            for link in links:
                await uow.runs.add_artifact_link(link)
            await uow.commit()

    async def _persist_run(self, previous: WorkflowRun, updated: WorkflowRun) -> None:
        async with self._uow_factory() as uow:
            await uow.runs.update(updated, expected_version=previous.row_version)
            uow.publish_after_commit(
                WorkflowRunChanged(updated.id, updated.status, updated.updated_at)
            )
            await uow.commit()


def _resolve_inputs(
    node: WorkflowNode,
    run: WorkflowRun,
    by_node: Mapping[str, NodeRun],
    incoming: Sequence[WorkflowEdge],
) -> dict[str, object]:
    resolved: dict[str, object] = {}
    required_ports = {port.name for port in ports_from_schema(node.input_schema) if port.required}
    for port, binding in node.input_bindings.items():
        if not isinstance(binding, Mapping):
            raise WorkflowRunInputError(f"节点 {node.node_key} 的 {port} 绑定无效")
        kind = binding.get("kind")
        if kind == "workflow_input":
            name = binding.get("name")
            if not isinstance(name, str):
                raise WorkflowRunInputError(f"节点 {node.node_key} 的工作流输入不存在")
            if name not in run.input:
                if port not in required_ports:
                    continue
                raise WorkflowRunInputError(f"节点 {node.node_key} 的工作流输入不存在")
            resolved[port] = run.input[name]
        elif kind == "constant" and "value" in binding:
            resolved[port] = binding["value"]
        else:
            raise WorkflowRunInputError(f"节点 {node.node_key} 的 {port} 绑定类型无效")
    for edge in incoming:
        upstream = by_node[edge.source_node_id]
        if upstream.output is None or edge.source_port not in upstream.output:
            raise WorkflowRunInputError(f"节点 {node.node_key} 的上游输出尚未物化")
        resolved[edge.target_port] = upstream.output[edge.source_port]
    return resolved


def _validate_instance(
    value: Mapping[str, object],
    schema: Mapping[str, object],
    label: str,
) -> None:
    errors = sorted(
        Draft202012Validator(cast(Mapping[str, Any], _plain_json(schema))).iter_errors(
            _plain_json(value)
        ),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if errors:
        path = ".".join(str(part) for part in errors[0].absolute_path)
        location = f" ({path}) " if path else ""
        raise WorkflowRunInputError(f"{label}{location}无效：{errors[0].message}")


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(child) for child in value]
    return value
