"""Visual workflow list, draft editor, problem panel, and run observer."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Coroutine, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from astraweft.application.comfyui import ComfyUIService
from astraweft.application.providers import ProviderService
from astraweft.application.workflows import (
    CreateWorkflow,
    ImportWorkflow,
    SaveWorkflowDraft,
    StartWorkflowRun,
    WorkflowDefinitionSnapshot,
    WorkflowEdgeDraft,
    WorkflowExecutionService,
    WorkflowNodeDraft,
    WorkflowRunSnapshot,
    WorkflowService,
    WorkflowSummary,
    WorkflowValidationError,
)
from astraweft.domain.comfyui import ComfyUITemplate
from astraweft.domain.workflow import (
    NodeRunStatus,
    WorkflowIssue,
    WorkflowNodeType,
    WorkflowRun,
    WorkflowVersionStatus,
    ports_from_schema,
)
from astraweft.presentation.design_system import fixed_width_font
from astraweft.presentation.widgets.controls import Button
from astraweft.presentation.widgets.data_views import DataTable
from astraweft.presentation.widgets.feedback import EmptyState
from astraweft.presentation.widgets.workflow_canvas import CanvasEdge, CanvasNode, WorkflowCanvas
from astraweft.presentation.widgets.workflow_dialogs import (
    ComfyUINodeDialog,
    ConnectionDialog,
    ProviderNodeDialog,
    WorkflowRunDialog,
)


class WorkflowPage(QWidget):
    """Keep all mutations behind application services while offering a visual editor."""

    workflow_changed = Signal()

    def __init__(
        self,
        definitions: WorkflowService,
        execution: WorkflowExecutionService,
        providers: ProviderService,
        comfyui: ComfyUIService | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("WorkflowPage")
        self._definitions = definitions
        self._execution = execution
        self._providers = providers
        self._comfyui = comfyui
        self._summaries: tuple[WorkflowSummary, ...] = ()
        self._recent_runs: tuple[WorkflowRun, ...] = ()
        self._snapshot: WorkflowDefinitionSnapshot | None = None
        self._nodes: list[WorkflowNodeDraft] = []
        self._edges: list[WorkflowEdgeDraft] = []
        self._input_schema: Mapping[str, object] = _object_schema()
        self._output_schema: Mapping[str, object] = _object_schema()
        self._output_bindings: Mapping[str, object] = {}
        self._dirty = False
        self._run_id: str | None = None
        self._run_snapshot: WorkflowRunSnapshot | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._save_lock = asyncio.Lock()
        self._logger = logging.getLogger("astraweft.presentation.workflows")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_list())
        self._stack.addWidget(self._build_editor())
        self._stack.addWidget(self._build_observer())
        root.addWidget(self._stack)
        self._observer_timer = QTimer(self)
        self._observer_timer.setInterval(450)
        self._observer_timer.timeout.connect(lambda: self._start(self._refresh_observer()))
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(900)
        self._autosave_timer.timeout.connect(lambda: self._start(self._auto_save()))
        QTimer.singleShot(0, self.request_refresh)

    def _build_list(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 27, 30, 24)
        layout.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("工作流")
        title.setObjectName("ContentTitle")
        self._list_summary = QLabel("读取本地工作流…")
        self._list_summary.setObjectName("BodyText")
        titles.addWidget(title)
        titles.addWidget(self._list_summary)
        header.addLayout(titles)
        header.addStretch(1)
        new_button = Button("新建工作流")
        new_button.clicked.connect(self._new_workflow)
        import_button = Button("导入", variant="ghost")
        import_button.clicked.connect(self._import_workflow)
        export_button = Button("导出", variant="ghost")
        export_button.clicked.connect(lambda: self._start(self._export_selected()))
        open_button = Button("打开", variant="ghost")
        open_button.clicked.connect(lambda: self._start(self._open_selected()))
        refresh = Button("刷新", variant="ghost")
        refresh.clicked.connect(self.request_refresh)
        for button in (new_button, import_button, export_button, open_button, refresh):
            header.addWidget(button)
        layout.addLayout(header)

        self._list_table = DataTable("工作流列表")
        self._list_table.doubleClicked.connect(lambda _index: self._start(self._open_selected()))
        layout.addWidget(self._list_table, 1)
        self._list_empty = EmptyState(
            "⌘",
            "还没有工作流",
            "新建空白工作流，或导入 astraweft.workflow/v1 文件。",
        )
        self._list_empty.hide()
        layout.addWidget(self._list_empty, 1)
        return page

    def _build_editor(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(22, 18, 22, 22)
        root.setSpacing(12)
        header = QHBoxLayout()
        back = Button("返回列表", variant="ghost")
        back.clicked.connect(self._show_list)
        titles = QVBoxLayout()
        self._editor_title = QLabel("工作流编辑器")
        self._editor_title.setObjectName("ContentTitle")
        self._editor_meta = QLabel()
        self._editor_meta.setObjectName("BodyText")
        titles.addWidget(self._editor_title)
        titles.addWidget(self._editor_meta)
        header.addWidget(back)
        header.addLayout(titles)
        header.addStretch(1)
        self._draft_button = Button("创建新草稿", variant="ghost")
        self._draft_button.clicked.connect(lambda: self._start(self._create_next_draft()))
        history_button = Button("版本历史", variant="ghost")
        history_button.clicked.connect(lambda: self._start(self._open_history()))
        self._save_button = Button("保存草稿", variant="ghost")
        self._save_button.clicked.connect(lambda: self._start(self._save_action()))
        self._publish_button = Button("验证并发布")
        self._publish_button.clicked.connect(lambda: self._start(self._publish()))
        self._run_button = Button("运行", variant="ghost")
        self._run_button.clicked.connect(self._run_workflow)
        for button in (
            self._draft_button,
            history_button,
            self._save_button,
            self._publish_button,
            self._run_button,
        ):
            header.addWidget(button)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_palette())
        self._canvas = WorkflowCanvas()
        self._canvas.node_selected.connect(self._show_node)
        self._canvas.node_moved.connect(self._move_node)
        splitter.addWidget(self._canvas)
        splitter.addWidget(self._build_inspector())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([210, 850, 320])
        root.addWidget(splitter, 1)
        return page

    def _build_palette(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("SectionCard")
        panel.setMinimumWidth(190)
        panel.setMaximumWidth(230)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        title = QLabel("节点库")
        title.setObjectName("SectionTitle")
        hint = QLabel("添加节点后拖动排布；端口连接始终显式可见。")
        hint.setObjectName("MutedText")
        hint.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(hint)
        self._add_provider_button = Button("+ Provider 模型", variant="ghost")
        self._add_provider_button.clicked.connect(lambda: self._start(self._add_provider_node()))
        self._add_transform_button = Button("+ 文本转换", variant="ghost")
        self._add_transform_button.clicked.connect(self._add_transform_node)
        self._add_comfyui_button = Button("+ ComfyUI", variant="ghost")
        self._add_comfyui_button.clicked.connect(lambda: self._start(self._add_comfyui_node()))
        self._connect_button = Button("连接端口", variant="ghost")
        self._connect_button.clicked.connect(self._connect_nodes)
        self._fit_button = Button("适配画布", variant="ghost")
        self._fit_button.clicked.connect(self._canvas_fit)
        layout.addWidget(self._add_provider_button)
        layout.addWidget(self._add_transform_button)
        layout.addWidget(self._add_comfyui_button)
        layout.addSpacing(8)
        layout.addWidget(self._connect_button)
        layout.addWidget(self._fit_button)
        layout.addStretch(1)
        self._delete_button = Button("删除选中节点", variant="danger")
        self._delete_button.clicked.connect(self._delete_selected)
        layout.addWidget(self._delete_button)
        return panel

    def _build_inspector(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("SectionCard")
        panel.setMinimumWidth(290)
        panel.setMaximumWidth(370)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(9)
        eyebrow = QLabel("NODE INSPECTOR")
        eyebrow.setObjectName("HeroEyebrow")
        self._node_title = QLabel("选择一个节点")
        self._node_title.setObjectName("CardTitle")
        self._node_meta = QLabel("画布节点的端口、类型与问题会显示在这里。")
        self._node_meta.setObjectName("MutedText")
        self._node_meta.setWordWrap(True)
        self._node_name = QLineEdit()
        self._node_name.setObjectName("TextInput")
        self._node_name.setAccessibleName("工作流节点名称")
        self._node_name.setPlaceholderText("节点名称")
        apply_name = Button("应用名称", variant="ghost")
        apply_name.clicked.connect(self._rename_selected)
        self._output_button = Button("设为工作流输出", variant="ghost")
        self._output_button.clicked.connect(self._set_selected_output)
        layout.addWidget(eyebrow)
        layout.addWidget(self._node_title)
        layout.addWidget(self._node_meta)
        layout.addWidget(self._node_name)
        layout.addWidget(apply_name)
        layout.addWidget(self._output_button)
        layout.addSpacing(10)
        problem_title = QLabel("问题面板")
        problem_title.setObjectName("SectionTitle")
        self._issue_summary = QLabel("尚未验证")
        self._issue_summary.setObjectName("MutedText")
        self._issues = QListWidget()
        self._issues.setObjectName("ProblemList")
        self._issues.setAccessibleName("工作流校验问题")
        layout.addWidget(problem_title)
        layout.addWidget(self._issue_summary)
        layout.addWidget(self._issues, 1)
        return panel

    def _build_observer(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(22, 18, 22, 22)
        root.setSpacing(12)
        header = QHBoxLayout()
        back = Button("返回工作流", variant="ghost")
        back.clicked.connect(self._return_from_observer)
        titles = QVBoxLayout()
        self._run_title = QLabel("运行观察")
        self._run_title.setObjectName("ContentTitle")
        self._run_meta = QLabel()
        self._run_meta.setObjectName("BodyText")
        titles.addWidget(self._run_title)
        titles.addWidget(self._run_meta)
        header.addWidget(back)
        header.addLayout(titles)
        header.addStretch(1)
        self._cancel_run = Button("取消运行", variant="danger")
        self._cancel_run.clicked.connect(lambda: self._start(self._cancel_current_run()))
        refresh = Button("刷新", variant="ghost")
        refresh.clicked.connect(lambda: self._start(self._refresh_observer()))
        header.addWidget(self._cancel_run)
        header.addWidget(refresh)
        root.addLayout(header)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._run_canvas = WorkflowCanvas()
        self._run_canvas.node_selected.connect(self._show_run_node)
        splitter.addWidget(self._run_canvas)
        detail = QFrame()
        detail.setObjectName("SectionCard")
        detail.setMinimumWidth(330)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(16, 16, 16, 16)
        self._run_nodes = DataTable("节点运行列表")
        self._run_nodes.clicked.connect(lambda index: self._show_run_row(index.row()))
        self._run_detail = QPlainTextEdit()
        self._run_detail.setObjectName("SchemaViewer")
        self._run_detail.setAccessibleName("工作流节点运行详情")
        self._run_detail.setReadOnly(True)
        self._run_detail.setFont(fixed_width_font())
        detail_layout.addWidget(self._run_nodes, 1)
        detail_layout.addWidget(self._run_detail, 1)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([900, 420])
        root.addWidget(splitter, 1)
        return page

    def request_refresh(self) -> None:
        self._start(self._refresh_list())

    async def _refresh_list(self) -> None:
        self._summaries = await self._definitions.list_summaries()
        self._recent_runs = await self._execution.list_runs(limit=1000)
        active = sum(not run.status.terminal for run in self._recent_runs)
        self._list_summary.setText(
            f"{len(self._summaries)} 个工作流  ·  {active} 个运行中  ·  本地不可变版本"
        )
        self._list_table.setVisible(bool(self._summaries))
        self._list_empty.setVisible(not self._summaries)
        model = QStandardItemModel(0, 6, self)
        model.setHorizontalHeaderLabels(["状态", "名称", "版本", "节点", "最近运行", "更新时间"])
        recent_by_workflow: dict[str, WorkflowRun] = {}
        for run in self._recent_runs:
            recent_by_workflow.setdefault(run.workflow_id, run)
        for summary in self._summaries:
            recent = recent_by_workflow.get(summary.workflow.id)
            values = (
                _version_status(summary.status),
                summary.workflow.name,
                "—" if summary.version_no is None else f"v{summary.version_no}",
                str(summary.node_count),
                "尚未运行" if recent is None else _run_status(recent.status.value),
                summary.workflow.updated_at.astimezone().strftime("%m-%d %H:%M"),
            )
            items = [QStandardItem(value) for value in values]
            items[0].setData(summary.workflow.id, Qt.ItemDataRole.UserRole)
            items[0].setData(summary.editable_version_id, Qt.ItemDataRole.UserRole + 1)
            for item in items:
                item.setEditable(False)
            model.appendRow(items)
        self._list_table.setModel(model)
        header = self._list_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        if self._summaries:
            self._list_table.selectRow(0)

    def _new_workflow(self) -> None:
        name, accepted = QInputDialog.getText(self, "新建工作流", "工作流名称")
        if accepted and name.strip():
            self._start(self._create_workflow(name.strip()))

    async def _create_workflow(self, name: str) -> None:
        snapshot = await self._definitions.create(CreateWorkflow(name))
        self._load_snapshot(snapshot)

    async def _open_selected(self) -> None:
        version_id = self._selected_version_id()
        if version_id is None:
            return
        self._load_snapshot(await self._definitions.get_definition(version_id))

    def _load_snapshot(self, snapshot: WorkflowDefinitionSnapshot) -> None:
        self._snapshot = snapshot
        self._nodes = [
            WorkflowNodeDraft(
                node_key=node.node_key,
                node_type=node.node_type,
                name=node.name,
                provider_id=node.provider_id,
                model_id=node.model_id,
                operation=node.operation,
                input_schema=node.input_schema,
                output_schema=node.output_schema,
                input_bindings=node.input_bindings,
                config=node.config,
                continue_on_error=node.continue_on_error,
                position_x=node.position_x,
                position_y=node.position_y,
            )
            for node in snapshot.nodes
        ]
        by_id = {node.id: node.node_key for node in snapshot.nodes}
        self._edges = [
            WorkflowEdgeDraft(
                by_id[edge.source_node_id],
                edge.source_port,
                by_id[edge.target_node_id],
                edge.target_port,
            )
            for edge in snapshot.edges
        ]
        self._input_schema = snapshot.version.input_schema
        self._output_schema = snapshot.version.output_schema
        self._output_bindings = snapshot.version.output_bindings
        self._dirty = False
        self._autosave_timer.stop()
        self._editor_title.setText(snapshot.workflow.name)
        self._render_editor()
        self._render_issues(snapshot.issues)
        self._stack.setCurrentIndex(1)

    def _render_editor(self) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return
        editable = snapshot.version.status is WorkflowVersionStatus.DRAFT
        dirty = " · 未保存" if self._dirty else ""
        self._editor_meta.setText(
            f"v{snapshot.version.version_no} · {_version_status(snapshot.version.status)}"
            f" · {len(self._nodes)} 个节点{dirty}"
        )
        for button in (
            self._add_provider_button,
            self._add_transform_button,
            self._add_comfyui_button,
            self._connect_button,
            self._delete_button,
            self._save_button,
            self._publish_button,
        ):
            button.setEnabled(editable)
        self._draft_button.setVisible(not editable)
        self._run_button.setEnabled(not editable)
        self._canvas.set_graph(
            tuple(_canvas_node(node) for node in self._nodes),
            tuple(_canvas_edge(edge) for edge in self._edges),
            editable=editable,
        )

    async def _add_provider_node(self) -> None:
        if not self._is_editable():
            return
        providers = await self._providers.list_providers()
        models = await self._providers.list_models()
        if not providers or not models:
            QMessageBox.information(
                self,
                "暂无可用模型",
                "请先在 Provider 页面配置连接并同步模型目录。",
            )
            return
        dialog = ProviderNodeDialog(providers, models, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        provider_id, model_id, operation, name = dialog.selection()
        node = await self._definitions.provider_node_draft(
            node_key=self._new_node_key("provider"),
            name=name,
            provider_id=provider_id,
            model_id=model_id,
            operation=operation,
            position_x=len(self._nodes) * 280,
            position_y=(len(self._nodes) % 3) * 170,
        )
        node = replace(node, input_bindings=self._merge_inputs(node.input_schema))
        self._nodes.append(node)
        self._set_outputs(node)
        self._mark_dirty(select=node.node_key)

    def _add_transform_node(self) -> None:
        if not self._is_editable():
            return
        schema = _single_field_schema("text", {"type": "string", "title": "文本"})
        node = WorkflowNodeDraft(
            node_key=self._new_node_key("transform"),
            node_type=WorkflowNodeType.TRANSFORM,
            name="文本模板",
            provider_id=None,
            model_id=None,
            operation=None,
            input_schema=schema,
            output_schema=schema,
            input_bindings=self._merge_inputs(schema),
            config={"kind": "text_template", "template": "{text}", "output": "text"},
            position_x=len(self._nodes) * 280,
            position_y=(len(self._nodes) % 3) * 170,
        )
        self._nodes.append(node)
        self._set_outputs(node)
        self._mark_dirty(select=node.node_key)

    async def _add_comfyui_node(self) -> None:
        if not self._is_editable():
            return
        if self._comfyui is None:
            QMessageBox.information(self, "ComfyUI 未启用", "当前没有可用的 ComfyUI 执行器。")
            return
        instances = await self._comfyui.list_instances()
        templates: list[ComfyUITemplate] = []
        for instance in instances:
            templates.extend(await self._comfyui.list_templates(instance.id))
        if not instances or not templates:
            QMessageBox.information(
                self,
                "暂无 ComfyUI 模板",
                "请先在 ComfyUI 页面添加实例并导入 API Format 模板。",
            )
            return
        dialog = ComfyUINodeDialog(instances, templates, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        instance_id, template_id, name = dialog.selection()
        template = next(item for item in templates if item.id == template_id)
        output_schema: Mapping[str, object] = {
            "type": "object",
            "properties": {
                "data": {"type": "object"},
                "artifacts": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["data", "artifacts"],
            "additionalProperties": False,
        }
        node = WorkflowNodeDraft(
            node_key=self._new_node_key("comfyui"),
            node_type=WorkflowNodeType.COMFYUI,
            name=name,
            provider_id=None,
            model_id=None,
            operation=None,
            input_schema=template.input_schema,
            output_schema=output_schema,
            input_bindings=self._merge_inputs(template.input_schema),
            config={
                "instance_id": instance_id,
                "template_id": template.id,
                "template_checksum": template.checksum,
                "prompt": template.prompt,
                "input_targets": template.input_targets,
                "output_nodes": list(template.output_nodes),
            },
            position_x=len(self._nodes) * 280,
            position_y=(len(self._nodes) % 3) * 170,
        )
        self._nodes.append(node)
        self._set_outputs(node)
        self._mark_dirty(select=node.node_key)

    def _connect_nodes(self) -> None:
        if not self._is_editable() or len(self._nodes) < 2:
            QMessageBox.information(self, "需要更多节点", "至少添加两个节点后才能连接。")
            return
        dialog = ConnectionDialog(self._nodes, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        source, source_port, target, target_port = dialog.selection()
        self._edges = [
            edge
            for edge in self._edges
            if not (edge.target_node == target and edge.target_port == target_port)
        ]
        self._edges.append(WorkflowEdgeDraft(source, source_port, target, target_port))
        self._nodes = [
            replace(
                node,
                input_bindings={
                    key: value for key, value in node.input_bindings.items() if key != target_port
                },
            )
            if node.node_key == target
            else node
            for node in self._nodes
        ]
        self._prune_workflow_inputs()
        self._mark_dirty(select=target)

    def _delete_selected(self) -> None:
        key = self._canvas.selected_key()
        if key is None or not self._is_editable():
            return
        self._nodes = [node for node in self._nodes if node.node_key != key]
        self._edges = [
            edge for edge in self._edges if edge.source_node != key and edge.target_node != key
        ]
        self._output_bindings = {
            name: binding
            for name, binding in self._output_bindings.items()
            if not (isinstance(binding, Mapping) and binding.get("node") == key)
        }
        self._prune_workflow_inputs()
        self._mark_dirty()

    def _move_node(self, key: str, x: int, y: int) -> None:
        if not self._is_editable():
            return
        self._nodes = [
            replace(node, position_x=x, position_y=y) if node.node_key == key else node
            for node in self._nodes
        ]
        self._dirty = True
        self._autosave_timer.start()
        self._update_editor_meta()

    def _show_node(self, key: str) -> None:
        node = next((item for item in self._nodes if item.node_key == key), None)
        if node is None:
            return
        self._node_title.setText(node.name)
        self._node_name.setText(node.name)
        self._node_meta.setText(
            f"{node.node_type.value} · {len(ports_from_schema(node.input_schema))} 个输入"
            f" · {len(ports_from_schema(node.output_schema))} 个输出\n{node.node_key}"
        )

    def _rename_selected(self) -> None:
        key = self._canvas.selected_key()
        name = self._node_name.text().strip()
        if key is None or not name or not self._is_editable():
            return
        self._nodes = [
            replace(node, name=name) if node.node_key == key else node for node in self._nodes
        ]
        self._mark_dirty(select=key)

    def _set_selected_output(self) -> None:
        key = self._canvas.selected_key()
        node = next((item for item in self._nodes if item.node_key == key), None)
        if node is not None and self._is_editable():
            self._set_outputs(node)
            self._mark_dirty(select=node.node_key)

    async def _save_action(self) -> None:
        await self._save_draft()
        self.workflow_changed.emit()

    async def _save_draft(self) -> WorkflowDefinitionSnapshot:
        async with self._save_lock:
            snapshot = self._snapshot
            if snapshot is None or snapshot.version.status is not WorkflowVersionStatus.DRAFT:
                raise ValueError("当前版本不是可编辑草稿")
            if not self._dirty:
                return snapshot
            saved = await self._definitions.save_draft(
                SaveWorkflowDraft(
                    version_id=snapshot.version.id,
                    expected_row_version=snapshot.version.row_version,
                    input_schema=self._input_schema,
                    output_schema=self._output_schema,
                    output_bindings=self._output_bindings,
                    nodes=tuple(self._nodes),
                    edges=tuple(self._edges),
                )
            )
            self._load_snapshot(saved)
            return saved

    async def _auto_save(self) -> None:
        if self._dirty and self._is_editable():
            await self._save_draft()
            self.workflow_changed.emit()

    async def _publish(self) -> None:
        saved = await self._save_draft() if self._dirty else self._snapshot
        if saved is None:
            return
        published = await self._definitions.publish(saved.version.id)
        self._load_snapshot(published)
        self.workflow_changed.emit()

    async def _create_next_draft(self) -> None:
        if self._snapshot is None:
            return
        self._load_snapshot(await self._definitions.create_draft(self._snapshot.workflow.id))

    async def _open_history(self) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return
        versions = await self._definitions.list_versions(snapshot.workflow.id)
        labels = [
            f"v{version.version_no} · {_version_status(version.status)} · {version.checksum[:10]}"
            for version in versions
        ]
        selected, accepted = QInputDialog.getItem(
            self,
            "版本历史",
            "选择只读版本",
            labels,
            0,
            False,
        )
        if accepted and selected in labels:
            self._load_snapshot(
                await self._definitions.get_definition(versions[labels.index(selected)].id)
            )

    def _run_workflow(self) -> None:
        snapshot = self._snapshot
        if snapshot is None or snapshot.version.status is WorkflowVersionStatus.DRAFT:
            return
        try:
            dialog = WorkflowRunDialog(snapshot.version.input_schema, self)
        except Exception as exc:
            QMessageBox.warning(self, "无法生成输入表单", str(exc))
            return
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._start(self._start_run(snapshot.version.id, dialog.values()))

    async def _start_run(self, version_id: str, inputs: Mapping[str, object]) -> None:
        started = await self._execution.start(StartWorkflowRun(version_id, inputs))
        self._run_id = started.run.id
        self._run_snapshot = await self._execution.advance(started.run.id)
        self._render_observer()
        self._stack.setCurrentIndex(2)
        self._observer_timer.start()

    async def _refresh_observer(self) -> None:
        if self._run_id is None:
            return
        self._run_snapshot = await self._execution.get_run(self._run_id)
        self._render_observer()
        if self._run_snapshot.run.status.terminal:
            self._observer_timer.stop()
            await self._refresh_list()

    def _render_observer(self) -> None:
        snapshot = self._run_snapshot
        if snapshot is None:
            return
        self._run_title.setText(f"运行 · {_run_status(snapshot.run.status.value)}")
        self._run_meta.setText(
            f"{snapshot.run.id} · v{snapshot.version.version_no} · "
            f"{snapshot.run.updated_at.astimezone().strftime('%H:%M:%S')}"
        )
        status_by_key = {item.node_key: item.status.value for item in snapshot.node_runs}
        self._run_canvas.set_graph(
            tuple(
                CanvasNode(
                    key=node.node_key,
                    name=node.name,
                    node_type=node.node_type.value,
                    input_schema=node.input_schema,
                    output_schema=node.output_schema,
                    x=node.position_x,
                    y=node.position_y,
                    status=status_by_key.get(node.node_key),
                )
                for node in snapshot.nodes
            ),
            tuple(
                CanvasEdge(
                    next(
                        node.node_key for node in snapshot.nodes if node.id == edge.source_node_id
                    ),
                    edge.source_port,
                    next(
                        node.node_key for node in snapshot.nodes if node.id == edge.target_node_id
                    ),
                    edge.target_port,
                )
                for edge in snapshot.edges
            ),
            editable=False,
        )
        model = QStandardItemModel(0, 3, self)
        model.setHorizontalHeaderLabels(["节点", "状态", "Task"])
        for node_run in snapshot.node_runs:
            items = [
                QStandardItem(node_run.node_key),
                QStandardItem(_node_status(node_run.status)),
                QStandardItem(node_run.task_id or "—"),
            ]
            items[0].setData(node_run.id, Qt.ItemDataRole.UserRole)
            for item in items:
                item.setEditable(False)
            model.appendRow(items)
        self._run_nodes.setModel(model)
        header = self._run_nodes.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._cancel_run.setEnabled(not snapshot.run.status.terminal)
        if snapshot.node_runs:
            self._run_nodes.selectRow(0)
            self._show_run_row(0)

    def _show_run_node(self, key: str) -> None:
        snapshot = self._run_snapshot
        if snapshot is None:
            return
        for row, node_run in enumerate(snapshot.node_runs):
            if node_run.node_key == key:
                self._run_nodes.selectRow(row)
                self._show_run_row(row)
                return

    def _show_run_row(self, row: int) -> None:
        snapshot = self._run_snapshot
        if snapshot is None or not 0 <= row < len(snapshot.node_runs):
            return
        node_run = snapshot.node_runs[row]
        links = [
            {
                "direction": link.direction.value,
                "port": link.port_name,
                "artifact_id": link.artifact_id,
            }
            for link in snapshot.artifact_links
            if link.node_run_id == node_run.id
        ]
        self._run_detail.setPlainText(
            _json_text(
                {
                    "node": node_run.node_key,
                    "status": node_run.status.value,
                    "resolved_input": node_run.resolved_input,
                    "output": node_run.output,
                    "planned_task_id": node_run.planned_task_id,
                    "task_id": node_run.task_id,
                    "artifacts": links,
                    "error": {
                        "code": node_run.error_code,
                        "message": node_run.error_message,
                    }
                    if node_run.error_code
                    else None,
                }
            )
        )

    async def _cancel_current_run(self) -> None:
        if self._run_id is None:
            return
        self._run_snapshot = await self._execution.cancel(self._run_id)
        self._render_observer()
        self._observer_timer.stop()

    def _return_from_observer(self) -> None:
        self._observer_timer.stop()
        self._stack.setCurrentIndex(1 if self._snapshot is not None else 0)

    def _import_workflow(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "导入工作流",
            "",
            "AstraWeft Workflow (*.json);;JSON (*.json)",
        )
        if path:
            self._start(self._import_path(Path(path)))

    async def _import_path(self, path: Path) -> None:
        document = await asyncio.to_thread(path.read_bytes)
        imported = await self._definitions.import_definition(ImportWorkflow(document))
        await self._refresh_list()
        self._load_snapshot(imported)

    async def _export_selected(self) -> None:
        version_id = self._selected_version_id()
        if version_id is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "导出工作流",
            "workflow.astraweft.json",
            "AstraWeft Workflow (*.json)",
        )
        if path:
            document = await self._definitions.export_definition(version_id)
            await asyncio.to_thread(
                Path(path).write_text,
                document,
                encoding="utf-8",
            )

    def _merge_inputs(self, schema: Mapping[str, object]) -> Mapping[str, object]:
        current_properties = self._input_schema.get("properties", {})
        properties = dict(current_properties) if isinstance(current_properties, Mapping) else {}
        required_value = self._input_schema.get("required", ())
        required = (
            [item for item in required_value if isinstance(item, str)]
            if isinstance(required_value, Sequence)
            and not isinstance(required_value, (str, bytes, bytearray))
            else []
        )
        bindings: dict[str, object] = {}
        for port in ports_from_schema(schema):
            properties.setdefault(port.name, port.schema)
            if port.required and port.name not in required:
                required.append(port.name)
            bindings[port.name] = {"kind": "workflow_input", "name": port.name}
        self._input_schema = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
        return bindings

    def _prune_workflow_inputs(self) -> None:
        current_properties = self._input_schema.get("properties", {})
        available = dict(current_properties) if isinstance(current_properties, Mapping) else {}
        used: dict[str, object] = {}
        required: set[str] = set()
        for node in self._nodes:
            ports = {port.name: port for port in ports_from_schema(node.input_schema)}
            for port_name, binding in node.input_bindings.items():
                if not isinstance(binding, Mapping) or binding.get("kind") != "workflow_input":
                    continue
                input_name = binding.get("name")
                if not isinstance(input_name, str) or input_name == "":
                    continue
                port = ports.get(port_name)
                used[input_name] = available.get(
                    input_name,
                    port.schema if port is not None else {},
                )
                if port is not None and port.required:
                    required.add(input_name)
        self._input_schema = {
            "type": "object",
            "properties": used,
            "required": sorted(required),
            "additionalProperties": False,
        }

    def _set_outputs(self, node: WorkflowNodeDraft) -> None:
        properties_value = node.output_schema.get("properties", {})
        properties = dict(properties_value) if isinstance(properties_value, Mapping) else {}
        required_value = node.output_schema.get("required", ())
        required = (
            [item for item in required_value if isinstance(item, str)]
            if isinstance(required_value, Sequence)
            and not isinstance(required_value, (str, bytes, bytearray))
            else []
        )
        self._output_schema = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
        self._output_bindings = {name: {"node": node.node_key, "port": name} for name in properties}

    def _render_issues(self, issues: Sequence[WorkflowIssue]) -> None:
        self._issues.clear()
        if not issues:
            self._issue_summary.setText("✓ 可以发布")
            self._issues.addItem("未发现 DAG、端口、Schema 或资源错误")
            return
        self._issue_summary.setText(f"{len(issues)} 个问题阻止发布")
        for issue in issues:
            location = ""
            if issue.node_key:
                location += f" [{issue.node_key}]"
            if issue.port_name:
                location += f" · {issue.port_name}"
            self._issues.addItem(f"{issue.message}{location}")

    def _mark_dirty(self, *, select: str | None = None) -> None:
        self._dirty = True
        self._autosave_timer.start()
        self._render_editor()
        self._issue_summary.setText("草稿已变化，请保存后重新验证")
        self._issues.clear()
        self._issues.addItem("未保存的画布变化尚未进入发布校验")
        if select is not None:
            self._canvas.select_node(select)

    def _update_editor_meta(self) -> None:
        snapshot = self._snapshot
        if snapshot is not None:
            self._editor_meta.setText(
                f"v{snapshot.version.version_no} · 草稿 · {len(self._nodes)} 个节点 · 未保存"
            )

    def _new_node_key(self, prefix: str) -> str:
        existing = {node.node_key for node in self._nodes}
        number = 1
        while f"{prefix}_{number}" in existing:
            number += 1
        return f"{prefix}_{number}"

    def _is_editable(self) -> bool:
        return (
            self._snapshot is not None
            and self._snapshot.version.status is WorkflowVersionStatus.DRAFT
        )

    def _selected_version_id(self) -> str | None:
        model = self._list_table.model()
        selection = self._list_table.selectionModel().selectedRows() if model else []
        if not selection or model is None:
            return None
        value = model.index(selection[0].row(), 0).data(Qt.ItemDataRole.UserRole + 1)
        return value if isinstance(value, str) else None

    def _show_list(self) -> None:
        if self._dirty:
            answer = QMessageBox.question(
                self,
                "放弃未保存变化？",
                "画布上有尚未保存的变化。返回列表会放弃这些变化。",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._stack.setCurrentIndex(0)
        self.request_refresh()

    def _canvas_fit(self) -> None:
        self._canvas.fit_graph()

    def _start(self, operation: Coroutine[Any, Any, object]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            operation.close()
            return
        task = loop.create_task(self._guard(operation))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _guard(self, operation: Coroutine[Any, Any, object]) -> None:
        try:
            await operation
        except WorkflowValidationError as exc:
            self._render_issues(exc.issues)
            QMessageBox.warning(
                self,
                "发布前需要修复",
                "问题面板列出了阻止发布的项目。",
            )
        except Exception as exc:
            self._logger.exception("workflow_ui_operation_failed")
            QMessageBox.warning(self, "工作流操作失败", str(exc))


def _canvas_node(node: WorkflowNodeDraft) -> CanvasNode:
    return CanvasNode(
        key=node.node_key,
        name=node.name,
        node_type=node.node_type.value,
        input_schema=node.input_schema,
        output_schema=node.output_schema,
        x=node.position_x,
        y=node.position_y,
    )


def _canvas_edge(edge: WorkflowEdgeDraft) -> CanvasEdge:
    return CanvasEdge(
        edge.source_node,
        edge.source_port,
        edge.target_node,
        edge.target_port,
    )


def _object_schema() -> Mapping[str, object]:
    return {"type": "object", "properties": {}, "additionalProperties": False}


def _single_field_schema(name: str, schema: Mapping[str, object]) -> Mapping[str, object]:
    return {
        "type": "object",
        "properties": {name: schema},
        "required": [name],
        "additionalProperties": False,
    }


def _version_status(status: WorkflowVersionStatus | None) -> str:
    return {
        None: "无版本",
        WorkflowVersionStatus.DRAFT: "草稿",
        WorkflowVersionStatus.PUBLISHED: "已发布",
        WorkflowVersionStatus.ARCHIVED: "历史版本",
    }[status]


def _run_status(status: str) -> str:
    return {
        "CREATED": "已创建",
        "RUNNING": "运行中",
        "WAITING": "等待中",
        "SUCCESS": "成功",
        "FAILED": "失败",
        "CANCELED": "已取消",
    }.get(status, status)


def _node_status(status: NodeRunStatus) -> str:
    return {
        NodeRunStatus.PENDING: "等待上游",
        NodeRunStatus.READY: "已就绪",
        NodeRunStatus.RUNNING: "运行中",
        NodeRunStatus.WAITING_APPROVAL: "等待批准",
        NodeRunStatus.SUCCESS: "成功",
        NodeRunStatus.FAILED: "失败",
        NodeRunStatus.SKIPPED: "已跳过",
        NodeRunStatus.CANCELED: "已取消",
    }[status]


def _json_text(value: object) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, indent=2, sort_keys=True)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain(child) for child in value]
    return value
