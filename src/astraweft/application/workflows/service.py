"""Workflow draft, publication, validation, and portable definition orchestration."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from astraweft.application.comfyui import ComfyUIService
from astraweft.application.providers import ProviderService
from astraweft.application.workflows.commands import (
    CreateWorkflow,
    ImportWorkflow,
    SaveWorkflowDraft,
    WorkflowNodeDraft,
)
from astraweft.application.workflows.events import WorkflowChanged
from astraweft.application.workflows.serialization import (
    WorkflowImportError,
    decode_workflow,
    encode_workflow,
)
from astraweft.application.workflows.transforms import (
    TransformConfigurationError,
    validate_transform_config,
)
from astraweft.domain.provider import Model, Provider
from astraweft.domain.workflow import (
    Workflow,
    WorkflowEdge,
    WorkflowIssue,
    WorkflowIssueSeverity,
    WorkflowNode,
    WorkflowNodeType,
    WorkflowTransitionError,
    WorkflowVersion,
    WorkflowVersionStatus,
    definition_checksum,
    validate_definition,
)
from astraweft.ports.runtime import Clock, IdGenerator
from astraweft.ports.workflows import WorkflowUnitOfWorkFactory

_EMPTY_CHECKSUM = "0" * 64
_SUPPORTED_EXECUTORS = frozenset(
    {
        WorkflowNodeType.PROVIDER_MODEL,
        WorkflowNodeType.TRANSFORM,
        WorkflowNodeType.COMFYUI,
    }
)


class WorkflowNotFoundError(LookupError):
    """A workflow or version is absent from active local state."""


class WorkflowInputError(ValueError):
    """A workflow edit cannot be represented safely."""


class WorkflowValidationError(ValueError):
    """Publication was blocked by one or more actionable definition errors."""

    def __init__(self, issues: tuple[WorkflowIssue, ...]) -> None:
        super().__init__("工作流存在发布错误")
        self.issues = issues


@dataclass(frozen=True, slots=True)
class WorkflowDefinitionSnapshot:
    workflow: Workflow
    version: WorkflowVersion
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]
    issues: tuple[WorkflowIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowSummary:
    workflow: Workflow
    editable_version_id: str | None
    current_version_id: str | None
    version_no: int | None
    status: WorkflowVersionStatus | None
    node_count: int


class WorkflowService:
    """Own immutable version publication and safe graph editing semantics."""

    def __init__(
        self,
        *,
        uow_factory: WorkflowUnitOfWorkFactory,
        providers: ProviderService,
        comfyui: ComfyUIService | None = None,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._uow_factory = uow_factory
        self._providers = providers
        self._comfyui = comfyui
        self._clock = clock
        self._ids = ids

    async def list_workflows(self, *, limit: int = 1000) -> tuple[Workflow, ...]:
        async with self._uow_factory() as uow:
            return await uow.definitions.list(limit=limit)

    async def list_summaries(self, *, limit: int = 1000) -> tuple[WorkflowSummary, ...]:
        async with self._uow_factory() as uow:
            workflows = await uow.definitions.list(limit=limit)
            summaries: list[WorkflowSummary] = []
            for workflow in workflows:
                draft = await uow.definitions.get_draft(workflow.id)
                selected = draft
                if selected is None and workflow.current_version_id is not None:
                    selected = await uow.definitions.get_version(workflow.current_version_id)
                node_count = (
                    0 if selected is None else len(await uow.definitions.get_nodes(selected.id))
                )
                summaries.append(
                    WorkflowSummary(
                        workflow=workflow,
                        editable_version_id=None if selected is None else selected.id,
                        current_version_id=workflow.current_version_id,
                        version_no=None if selected is None else selected.version_no,
                        status=None if selected is None else selected.status,
                        node_count=node_count,
                    )
                )
        return tuple(summaries)

    async def list_versions(self, workflow_id: str) -> tuple[WorkflowVersion, ...]:
        async with self._uow_factory() as uow:
            workflow = await uow.definitions.get(workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError("工作流不存在或已删除")
            return await uow.definitions.list_versions(workflow_id)

    async def get_definition(self, version_id: str) -> WorkflowDefinitionSnapshot:
        async with self._uow_factory() as uow:
            version = await uow.definitions.get_version(version_id)
            if version is None:
                raise WorkflowNotFoundError("工作流版本不存在")
            workflow = await uow.definitions.get(version.workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError("工作流不存在或已删除")
            nodes = await uow.definitions.get_nodes(version.id)
            edges = await uow.definitions.get_edges(version.id)
        issues = await self._collect_issues(version, nodes, edges)
        return WorkflowDefinitionSnapshot(workflow, version, nodes, edges, issues)

    async def create(self, command: CreateWorkflow) -> WorkflowDefinitionSnapshot:
        name = _required_name(command.name)
        if name in {item.name for item in await self.list_workflows()}:
            raise WorkflowInputError("工作流名称已存在")
        description = _description(command.description)
        now = self._clock.now()
        workflow = Workflow(
            id=self._ids.new(),
            name=name,
            description=description,
            current_version_id=None,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        version = WorkflowVersion(
            id=self._ids.new(),
            workflow_id=workflow.id,
            version_no=1,
            status=WorkflowVersionStatus.DRAFT,
            input_schema=_empty_object_schema(),
            output_schema=_empty_object_schema(),
            output_bindings={},
            checksum=_EMPTY_CHECKSUM,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        version = replace(version, checksum=definition_checksum(version, (), ()))
        async with self._uow_factory() as uow:
            await uow.definitions.add(workflow)
            await uow.definitions.add_version(version)
            uow.publish_after_commit(WorkflowChanged(workflow.id, version.id, "created", now))
            await uow.commit()
        issues = await self._collect_issues(version, (), ())
        return WorkflowDefinitionSnapshot(workflow, version, (), (), issues)

    async def create_draft(self, workflow_id: str) -> WorkflowDefinitionSnapshot:
        async with self._uow_factory() as uow:
            workflow = await uow.definitions.get(workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError("工作流不存在或已删除")
            existing = await uow.definitions.get_draft(workflow_id)
            if existing is not None:
                nodes = await uow.definitions.get_nodes(existing.id)
                edges = await uow.definitions.get_edges(existing.id)
                return WorkflowDefinitionSnapshot(
                    workflow,
                    existing,
                    nodes,
                    edges,
                    await self._collect_issues(existing, nodes, edges),
                )
            if workflow.current_version_id is None:
                raise WorkflowInputError("工作流没有可复制的已发布版本")
            source = await uow.definitions.get_version(workflow.current_version_id)
            if source is None:
                raise WorkflowNotFoundError("当前工作流版本不存在")
            source_nodes = await uow.definitions.get_nodes(source.id)
            source_edges = await uow.definitions.get_edges(source.id)
            versions = await uow.definitions.list_versions(workflow.id)
            now = self._clock.now()
            draft = WorkflowVersion(
                id=self._ids.new(),
                workflow_id=workflow.id,
                version_no=max(item.version_no for item in versions) + 1,
                status=WorkflowVersionStatus.DRAFT,
                input_schema=source.input_schema,
                output_schema=source.output_schema,
                output_bindings=source.output_bindings,
                checksum=source.checksum,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            nodes, edges = self._clone_definition(draft.id, source_nodes, source_edges)
            await uow.definitions.add_version(draft)
            await uow.definitions.replace_draft_definition(
                draft,
                nodes,
                edges,
                expected_version=draft.row_version,
            )
            uow.publish_after_commit(WorkflowChanged(workflow.id, draft.id, "drafted", now))
            await uow.commit()
        return WorkflowDefinitionSnapshot(
            workflow,
            draft,
            nodes,
            edges,
            await self._collect_issues(draft, nodes, edges),
        )

    async def save_draft(self, command: SaveWorkflowDraft) -> WorkflowDefinitionSnapshot:
        async with self._uow_factory() as uow:
            version = await uow.definitions.get_version(command.version_id)
            if version is None:
                raise WorkflowNotFoundError("工作流草稿不存在")
            if version.status is not WorkflowVersionStatus.DRAFT:
                raise WorkflowTransitionError("published workflow version is immutable")
            if version.row_version != command.expected_row_version:
                raise WorkflowInputError("草稿已被其他操作修改，请刷新后重试")
            workflow = await uow.definitions.get(version.workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError("工作流不存在或已删除")
            old_nodes = await uow.definitions.get_nodes(version.id)
            old_edges = await uow.definitions.get_edges(version.id)

        updated, nodes, edges = self._build_definition(version, command, old_nodes, old_edges)
        issues = await self._collect_issues(updated, nodes, edges)
        async with self._uow_factory() as uow:
            latest = await uow.definitions.get_version(version.id)
            if latest is None or latest.row_version != version.row_version:
                raise WorkflowInputError("草稿已被其他操作修改，请刷新后重试")
            await uow.definitions.replace_draft_definition(
                updated,
                nodes,
                edges,
                expected_version=version.row_version,
            )
            uow.publish_after_commit(
                WorkflowChanged(workflow.id, updated.id, "saved", updated.updated_at)
            )
            await uow.commit()
        return WorkflowDefinitionSnapshot(workflow, updated, nodes, edges, issues)

    async def validate_version(self, version_id: str) -> tuple[WorkflowIssue, ...]:
        snapshot = await self.get_definition(version_id)
        return snapshot.issues

    async def publish(self, version_id: str) -> WorkflowDefinitionSnapshot:
        snapshot = await self.get_definition(version_id)
        if snapshot.version.status is not WorkflowVersionStatus.DRAFT:
            raise WorkflowTransitionError("only a draft workflow version can be published")
        errors = tuple(
            issue for issue in snapshot.issues if issue.severity is WorkflowIssueSeverity.ERROR
        )
        if errors:
            raise WorkflowValidationError(errors)
        now = self._clock.now()
        published = snapshot.version.publish(now)
        current = snapshot.workflow.with_current_version(published.id, now)
        async with self._uow_factory() as uow:
            latest = await uow.definitions.get_version(snapshot.version.id)
            workflow = await uow.definitions.get(snapshot.workflow.id)
            if latest is None or workflow is None:
                raise WorkflowNotFoundError("工作流在发布前已被删除")
            if latest.row_version != snapshot.version.row_version:
                raise WorkflowInputError("草稿已被其他操作修改，请重新验证")
            if workflow.row_version != snapshot.workflow.row_version:
                raise WorkflowInputError("工作流版本指针已变化，请重新验证")
            if workflow.current_version_id is not None:
                previous = await uow.definitions.get_version(workflow.current_version_id)
                if previous is not None and previous.status is WorkflowVersionStatus.PUBLISHED:
                    archived = previous.archive(now)
                    await uow.definitions.update_version(
                        archived,
                        expected_version=previous.row_version,
                    )
            await uow.definitions.update_version(
                published,
                expected_version=snapshot.version.row_version,
            )
            await uow.definitions.update(current, expected_version=snapshot.workflow.row_version)
            uow.publish_after_commit(WorkflowChanged(current.id, published.id, "published", now))
            await uow.commit()
        return WorkflowDefinitionSnapshot(
            current,
            published,
            snapshot.nodes,
            snapshot.edges,
            (),
        )

    async def provider_node_draft(
        self,
        *,
        node_key: str,
        name: str,
        provider_id: str,
        model_id: str,
        operation: str,
        position_x: int = 0,
        position_y: int = 0,
    ) -> WorkflowNodeDraft:
        providers = {item.id: item for item in await self._providers.list_providers()}
        provider = providers.get(provider_id)
        models = {item.id: item for item in await self._providers.list_models(provider_id)}
        model = models.get(model_id)
        if provider is None or model is None:
            raise WorkflowInputError("Provider 或模型不存在")
        if operation not in model.operations:
            raise WorkflowInputError("模型不支持所选操作")
        return WorkflowNodeDraft(
            node_key=node_key,
            node_type=WorkflowNodeType.PROVIDER_MODEL,
            name=name,
            provider_id=provider.id,
            model_id=model.id,
            operation=operation,
            input_schema=model.parameter_schema,
            output_schema=_provider_output_schema(model.output_schema),
            input_bindings={},
            config={},
            position_x=position_x,
            position_y=position_y,
        )

    async def export_definition(self, version_id: str) -> str:
        snapshot = await self.get_definition(version_id)
        return encode_workflow(
            snapshot.workflow,
            snapshot.version,
            snapshot.nodes,
            snapshot.edges,
        )

    async def import_definition(self, command: ImportWorkflow) -> WorkflowDefinitionSnapshot:
        payload = decode_workflow(command.document)
        preferred_name = _required_name(command.name or payload.name)
        now = self._clock.now()
        workflow = Workflow(
            id=self._ids.new(),
            name=preferred_name,
            description=_description(payload.description),
            current_version_id=None,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        version = WorkflowVersion(
            id=self._ids.new(),
            workflow_id=workflow.id,
            version_no=1,
            status=WorkflowVersionStatus.DRAFT,
            input_schema=payload.input_schema,
            output_schema=payload.output_schema,
            output_bindings=payload.output_bindings,
            checksum=_EMPTY_CHECKSUM,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        command_value = SaveWorkflowDraft(
            version_id=version.id,
            expected_row_version=version.row_version,
            input_schema=payload.input_schema,
            output_schema=payload.output_schema,
            output_bindings=payload.output_bindings,
            nodes=payload.nodes,
            edges=payload.edges,
        )
        imported, nodes, edges = self._build_definition(version, command_value, (), ())
        imported = replace(imported, row_version=1)
        if imported.checksum != payload.checksum:
            raise WorkflowImportError("工作流 checksum 不匹配，文件可能已损坏或被篡改")
        async with self._uow_factory() as uow:
            duplicate = await uow.definitions.find_version_by_checksum(payload.checksum)
        if duplicate is not None:
            return await self.get_definition(duplicate.id)
        workflow = replace(
            workflow,
            name=await self._available_import_name(preferred_name),
        )
        issues = await self._collect_issues(imported, nodes, edges)
        async with self._uow_factory() as uow:
            await uow.definitions.add(workflow)
            await uow.definitions.add_version(imported)
            await uow.definitions.replace_draft_definition(
                imported,
                nodes,
                edges,
                expected_version=imported.row_version,
            )
            uow.publish_after_commit(WorkflowChanged(workflow.id, imported.id, "imported", now))
            await uow.commit()
        return WorkflowDefinitionSnapshot(workflow, imported, nodes, edges, issues)

    def _build_definition(
        self,
        version: WorkflowVersion,
        command: SaveWorkflowDraft,
        old_nodes: tuple[WorkflowNode, ...],
        old_edges: tuple[WorkflowEdge, ...],
    ) -> tuple[WorkflowVersion, tuple[WorkflowNode, ...], tuple[WorkflowEdge, ...]]:
        node_ids = {item.node_key: item.id for item in old_nodes}
        if len({item.node_key for item in command.nodes}) != len(command.nodes):
            raise WorkflowInputError("节点 key 重复")
        try:
            nodes = [
                WorkflowNode(
                    id=node_ids.get(draft.node_key, self._ids.new()),
                    workflow_version_id=version.id,
                    node_key=draft.node_key,
                    node_type=draft.node_type,
                    name=draft.name,
                    provider_id=draft.provider_id,
                    model_id=draft.model_id,
                    operation=draft.operation,
                    input_schema=draft.input_schema,
                    output_schema=draft.output_schema,
                    input_bindings=draft.input_bindings,
                    config=draft.config,
                    continue_on_error=draft.continue_on_error,
                    position_x=draft.position_x,
                    position_y=draft.position_y,
                )
                for draft in command.nodes
            ]
        except ValueError as exc:
            raise WorkflowInputError(str(exc)) from exc
        by_key = {item.node_key: item for item in nodes}
        old_edge_ids = {
            (
                next(
                    (node.node_key for node in old_nodes if node.id == edge.source_node_id),
                    edge.source_node_id,
                ),
                edge.source_port,
                next(
                    (node.node_key for node in old_nodes if node.id == edge.target_node_id),
                    edge.target_node_id,
                ),
                edge.target_port,
            ): edge.id
            for edge in old_edges
        }
        edges: list[WorkflowEdge] = []
        for draft in command.edges:
            source = by_key.get(draft.source_node)
            target = by_key.get(draft.target_node)
            if source is None or target is None:
                raise WorkflowInputError("连接引用了不存在的节点")
            key = (
                draft.source_node,
                draft.source_port,
                draft.target_node,
                draft.target_port,
            )
            try:
                edges.append(
                    WorkflowEdge(
                        id=old_edge_ids.get(key, self._ids.new()),
                        workflow_version_id=version.id,
                        source_node_id=source.id,
                        source_port=draft.source_port,
                        target_node_id=target.id,
                        target_port=draft.target_port,
                    )
                )
            except ValueError as exc:
                raise WorkflowInputError(str(exc)) from exc
        try:
            updated = version.with_draft_definition(
                input_schema=command.input_schema,
                output_schema=command.output_schema,
                output_bindings=command.output_bindings,
                checksum=_EMPTY_CHECKSUM,
                at=self._clock.now(),
            )
            checksum = definition_checksum(updated, nodes, edges)
            json.dumps(_plain_json(command.input_schema), allow_nan=False)
            json.dumps(_plain_json(command.output_schema), allow_nan=False)
            json.dumps(_plain_json(command.output_bindings), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise WorkflowInputError("工作流定义只能包含标准 JSON 值") from exc
        return replace(updated, checksum=checksum), tuple(nodes), tuple(edges)

    async def _collect_issues(
        self,
        version: WorkflowVersion,
        nodes: tuple[WorkflowNode, ...],
        edges: tuple[WorkflowEdge, ...],
    ) -> tuple[WorkflowIssue, ...]:
        issues = list(validate_definition(version, nodes, edges))
        if not nodes:
            issues.append(_issue("empty_workflow", "工作流至少需要一个节点"))
        for label, schema, node_key in (
            ("工作流输入", version.input_schema, None),
            ("工作流输出", version.output_schema, None),
            *((f"节点 {node.node_key} 输入", node.input_schema, node.node_key) for node in nodes),
            *((f"节点 {node.node_key} 输出", node.output_schema, node.node_key) for node in nodes),
        ):
            try:
                Draft202012Validator.check_schema(cast(Mapping[str, Any], _plain_json(schema)))
            except SchemaError:
                issues.append(
                    WorkflowIssue(
                        WorkflowIssueSeverity.ERROR,
                        "schema_invalid",
                        f"{label} Schema 无效",
                        node_key=node_key,
                    )
                )
            if _contains_nonlocal_ref(schema):
                issues.append(
                    WorkflowIssue(
                        WorkflowIssueSeverity.ERROR,
                        "schema_ref_unsafe",
                        f"{label} Schema 包含远程或文件引用",
                        node_key=node_key,
                    )
                )
        actual_checksum = definition_checksum(version, nodes, edges)
        if version.checksum != actual_checksum:
            issues.append(_issue("checksum_mismatch", "工作流定义校验和不一致"))
        issues.extend(await self._runtime_issues(nodes))
        return tuple(issues)

    async def _runtime_issues(self, nodes: tuple[WorkflowNode, ...]) -> tuple[WorkflowIssue, ...]:
        providers = {item.id: item for item in await self._providers.list_providers()}
        models = {item.id: item for item in await self._providers.list_models()}
        comfyui_instances = (
            {}
            if self._comfyui is None
            else {item.id: item for item in await self._comfyui.list_instances()}
        )
        issues: list[WorkflowIssue] = []
        for node in nodes:
            if node.node_type not in _SUPPORTED_EXECUTORS:
                issues.append(
                    _node_issue(node, "executor_unavailable", "该节点执行器尚未在本阶段启用")
                )
                continue
            if node.node_type is WorkflowNodeType.TRANSFORM:
                try:
                    validate_transform_config(node.config)
                except TransformConfigurationError as exc:
                    issues.append(_node_issue(node, "transform_config", str(exc)))
                continue
            if node.node_type is WorkflowNodeType.COMFYUI:
                instance_id = node.config.get("instance_id")
                instance = (
                    comfyui_instances.get(instance_id) if isinstance(instance_id, str) else None
                )
                if instance is None:
                    issues.append(
                        _node_issue(node, "comfyui_instance_missing", "ComfyUI 实例不存在")
                    )
                elif not instance.enabled:
                    issues.append(
                        _node_issue(node, "comfyui_instance_disabled", "ComfyUI 实例已停用")
                    )
                continue
            self._validate_provider_node(node, providers, models, issues)
        return tuple(issues)

    def _validate_provider_node(
        self,
        node: WorkflowNode,
        providers: Mapping[str, Provider],
        models: Mapping[str, Model],
        issues: list[WorkflowIssue],
    ) -> None:
        provider = providers.get(node.provider_id or "")
        model = models.get(node.model_id or "")
        if provider is None:
            issues.append(_node_issue(node, "provider_missing", "Provider 不存在或已删除"))
            return
        if not provider.enabled:
            issues.append(_node_issue(node, "provider_disabled", "Provider 已停用"))
        if model is None or model.provider_id != provider.id:
            issues.append(_node_issue(node, "model_missing", "模型不存在或不属于该 Provider"))
            return
        if not model.enabled:
            issues.append(_node_issue(node, "model_disabled", "模型已停用"))
        if not model.available or model.deprecated:
            issues.append(_node_issue(node, "model_unavailable", "模型当前不可用"))
        if node.operation not in model.operations:
            issues.append(_node_issue(node, "operation_unsupported", "模型不支持该操作"))
        if _plain_json(node.input_schema) != _plain_json(model.parameter_schema):
            issues.append(
                _node_issue(node, "input_schema_stale", "节点输入 Schema 不是当前模型快照")
            )
        if _plain_json(node.output_schema) != _plain_json(
            _provider_output_schema(model.output_schema)
        ):
            issues.append(
                _node_issue(node, "output_schema_stale", "节点输出 Schema 不是当前模型快照")
            )

    def _clone_definition(
        self,
        version_id: str,
        nodes: tuple[WorkflowNode, ...],
        edges: tuple[WorkflowEdge, ...],
    ) -> tuple[tuple[WorkflowNode, ...], tuple[WorkflowEdge, ...]]:
        cloned_nodes = tuple(
            replace(node, id=self._ids.new(), workflow_version_id=version_id) for node in nodes
        )
        old_to_new = {old.id: new.id for old, new in zip(nodes, cloned_nodes, strict=True)}
        cloned_edges = tuple(
            replace(
                edge,
                id=self._ids.new(),
                workflow_version_id=version_id,
                source_node_id=old_to_new[edge.source_node_id],
                target_node_id=old_to_new[edge.target_node_id],
            )
            for edge in edges
        )
        return cloned_nodes, cloned_edges

    async def _available_import_name(self, preferred: str) -> str:
        existing = {item.name for item in await self.list_workflows()}
        if preferred not in existing:
            return preferred
        for number in range(2, 10_000):
            suffix = f" (导入 {number})"
            candidate = preferred[: 160 - len(suffix)] + suffix
            if candidate not in existing:
                return candidate
        raise WorkflowInputError("无法生成唯一的工作流名称")


def _provider_output_schema(schema: Mapping[str, object]) -> Mapping[str, object]:
    result = dict(schema)
    properties_value = schema.get("properties", {})
    properties = dict(properties_value) if isinstance(properties_value, Mapping) else {}
    properties["artifacts"] = {"type": "array", "items": {"type": "string"}}
    result["properties"] = properties
    required_value = schema.get("required", ())
    required = (
        [item for item in required_value if isinstance(item, str)]
        if isinstance(required_value, Sequence)
        and not isinstance(required_value, (str, bytes, bytearray))
        else []
    )
    if "artifacts" not in required:
        required.append("artifacts")
    result["required"] = required
    return result


def _empty_object_schema() -> Mapping[str, object]:
    return {"type": "object", "properties": {}, "additionalProperties": False}


def _contains_nonlocal_ref(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) == "$ref" and isinstance(child, str) and not child.startswith("#/"):
                return True
            if _contains_nonlocal_ref(child):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_nonlocal_ref(child) for child in value)
    return False


def _required_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 160:
        raise WorkflowInputError("工作流名称不能为空且不能超过 160 个字符")
    return name


def _description(value: str) -> str:
    if len(value) > 4000:
        raise WorkflowInputError("工作流描述不能超过 4000 个字符")
    return value


def _issue(code: str, message: str) -> WorkflowIssue:
    return WorkflowIssue(WorkflowIssueSeverity.ERROR, code, message)


def _node_issue(node: WorkflowNode, code: str, message: str) -> WorkflowIssue:
    return WorkflowIssue(
        WorkflowIssueSeverity.ERROR,
        code,
        message,
        node_key=node.node_key,
    )


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(child) for child in value]
    return value
