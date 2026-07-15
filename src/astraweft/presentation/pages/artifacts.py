"""Local verified artifact library."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Coroutine, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from astraweft.application.query import QueryService
from astraweft.application.tasks import TaskService
from astraweft.domain.task import Artifact
from astraweft.ports.query import ArtifactQuery
from astraweft.presentation.design_system import fixed_width_font
from astraweft.presentation.i18n import Translator
from astraweft.presentation.thumbnails import ThumbnailCache
from astraweft.presentation.widgets.controls import Button, SelectInput
from astraweft.presentation.widgets.data_views import DataTable
from astraweft.presentation.widgets.feedback import EmptyState


class ArtifactsPage(QWidget):
    """Verified local artifact metadata with safe folder access."""

    def __init__(
        self,
        service: TaskService,
        root_path: Path,
        queries: QueryService | None = None,
        thumbnails: ThumbnailCache | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("ArtifactsPage")
        self._service = service
        self._root_path = root_path
        self._queries = queries
        self._translator = translator or Translator()
        self._thumbnails = thumbnails or ThumbnailCache(root_path.parent / "cache" / "thumbnails")
        self._next_cursor: str | None = None
        self._artifacts: tuple[Artifact, ...] = ()
        self._showing_trash = False
        self._tasks: set[asyncio.Task[Any]] = set()
        self._logger = logging.getLogger("astraweft.presentation.artifacts")
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 27, 30, 24)
        root.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel(self._translator.text("本地产物库", "Local Artifact Library"))
        title.setObjectName("ContentTitle")
        self._summary = QLabel(
            self._translator.text(
                "文件使用 SHA-256 校验，并保留来源 Task",
                "Files are verified with SHA-256 and retain their source task",
            )
        )
        self._summary.setObjectName("BodyText")
        titles.addWidget(title)
        titles.addWidget(self._summary)
        header.addLayout(titles)
        header.addStretch(1)
        folder = Button(
            self._translator.text("打开产物目录", "Open artifact folder"),
            variant="ghost",
        )
        folder.clicked.connect(self._open_folder)
        self._toggle = Button(self._translator.text("查看回收站", "View trash"), variant="ghost")
        self._toggle.clicked.connect(self._toggle_trash)
        self._lifecycle = Button(
            self._translator.text("移入回收站", "Move to trash"), variant="ghost"
        )
        self._lifecycle.setEnabled(False)
        self._lifecycle.clicked.connect(self._request_lifecycle_action)
        self._purge = Button(
            self._translator.text("永久删除", "Delete permanently"), variant="danger"
        )
        self._purge.setEnabled(False)
        self._purge.hide()
        self._purge.clicked.connect(self._request_purge)
        refresh = Button(self._translator.text("刷新", "Refresh"), variant="ghost")
        refresh.clicked.connect(self.request_refresh)
        self._load_more = Button(self._translator.text("加载更多", "Load more"), variant="ghost")
        self._load_more.clicked.connect(lambda: self._start(self._load_next()))
        self._load_more.hide()
        header.addWidget(folder)
        header.addWidget(self._toggle)
        header.addWidget(self._lifecycle)
        header.addWidget(self._purge)
        header.addWidget(self._load_more)
        header.addWidget(refresh)
        root.addLayout(header)

        filters = QHBoxLayout()
        filters.setSpacing(10)
        filter_label = QLabel(self._translator.text("筛选", "Filters"))
        filter_label.setObjectName("SectionTitle")
        self._kind_filter = SelectInput(
            self._translator.text("按产物类型筛选", "Filter by artifact type")
        )
        for chinese, english, kind in (
            ("全部类型", "All types", None),
            ("图片", "Image", "IMAGE"),
            ("视频", "Video", "VIDEO"),
            ("音频", "Audio", "AUDIO"),
            ("文本", "Text", "TEXT"),
            ("JSON", "JSON", "JSON"),
        ):
            self._kind_filter.addItem(self._translator.text(chinese, english), kind)
        self._period_filter = SelectInput(
            self._translator.text("按产物创建时间筛选", "Filter by creation time")
        )
        for chinese, english, days in (
            ("全部时间", "All time", None),
            ("最近 7 天", "Last 7 days", 7),
            ("最近 30 天", "Last 30 days", 30),
        ):
            self._period_filter.addItem(self._translator.text(chinese, english), days)
        self._kind_filter.currentIndexChanged.connect(self.request_refresh)
        self._period_filter.currentIndexChanged.connect(self.request_refresh)
        filters.addWidget(filter_label)
        filters.addWidget(self._kind_filter)
        filters.addWidget(self._period_filter)
        filters.addStretch(1)
        root.addLayout(filters)

        self._table = DataTable(self._translator.text("本地产物", "Local artifacts"))
        self._table.clicked.connect(lambda index: self._show_detail(index.row()))
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._table)
        self._splitter.addWidget(self._build_detail())
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setSizes([760, 420])
        root.addWidget(self._splitter, 1)
        self._empty = EmptyState(
            "▦",
            self._translator.text("还没有产物", "No artifacts yet"),
            self._translator.text(
                "图片、视频、音频、文本和 JSON 会保存在这里。",
                "Images, video, audio, text, and JSON are saved here.",
            ),
        )
        self._empty.hide()
        root.addWidget(self._empty, 1)
        QTimer.singleShot(0, self.request_refresh)

    def request_refresh(self) -> None:
        self._start(self._refresh())

    async def _refresh(self) -> None:
        try:
            if self._queries is not None:
                page = await self._queries.search_artifacts(
                    query=self._artifact_query(),
                    limit=100,
                )
                self._artifacts = page.items
                self._next_cursor = page.next_cursor
            elif self._showing_trash:
                self._artifacts = await self._service.list_trashed_artifacts(limit=1000)
                self._next_cursor = None
            else:
                self._artifacts = await self._service.list_artifacts(limit=1000)
                self._next_cursor = None
        except Exception:
            self._logger.exception("artifacts_load_failed")
            self._summary.setText(self._translator.text("产物读取失败", "Unable to load artifacts"))
            return
        self._render()

    async def _load_next(self) -> None:
        if self._queries is None or self._next_cursor is None:
            return
        try:
            page = await self._queries.search_artifacts(
                query=self._artifact_query(),
                cursor=self._next_cursor,
                limit=100,
            )
        except Exception:
            self._logger.exception("artifacts_next_page_failed")
            self._summary.setText(
                self._translator.text("加载更多产物失败", "Unable to load more artifacts")
            )
            return
        self._artifacts += page.items
        self._next_cursor = page.next_cursor
        self._render()

    def _render(self) -> None:
        size = sum(item.size_bytes for item in self._artifacts)
        location = (
            self._translator.text("回收站产物", "trashed artifacts")
            if self._showing_trash
            else self._translator.text("已校验产物", "verified artifacts")
        )
        self._summary.setText(
            self._translator.text(
                "显示最近 {count} 个{location}  ·  {size}",
                "Showing {count} recent {location}  ·  {size}",
                count=self._translator.integer(len(self._artifacts)),
                location=location,
                size=_size(size, self._translator),
            )
        )
        self._splitter.setVisible(bool(self._artifacts))
        self._empty.setVisible(not self._artifacts)
        self._load_more.setVisible(self._next_cursor is not None)
        model = QStandardItemModel(0, 6, self)
        model.setHorizontalHeaderLabels(
            [
                self._translator.text("类型", "Type"),
                self._translator.text("文件", "File"),
                self._translator.text("大小", "Size"),
                "SHA-256",
                self._translator.text("来源 Task", "Source task"),
                self._translator.text("创建时间", "Created"),
            ]
        )
        for artifact in self._artifacts:
            values = (
                artifact.kind,
                artifact.relative_path,
                _size(artifact.size_bytes, self._translator),
                artifact.sha256[:16] + "…",
                artifact.task_id or "—",
                artifact.created_at.astimezone().strftime("%m-%d %H:%M:%S"),
            )
            items = [QStandardItem(value) for value in values]
            items[0].setData(artifact.id, Qt.ItemDataRole.UserRole)
            for item in items:
                item.setEditable(False)
            model.appendRow(items)
        self._table.setModel(model)
        selection = self._table.selectionModel()
        if selection is not None:
            selection.selectionChanged.connect(self._selection_changed)
        self._update_actions()
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        if self._artifacts:
            self._table.selectRow(0)
            self._show_detail(0)

    def _build_detail(self) -> QFrame:
        detail = QFrame()
        detail.setObjectName("ModelDetail")
        layout = QVBoxLayout(detail)
        layout.setContentsMargins(18, 17, 18, 18)
        layout.setSpacing(10)
        eyebrow = QLabel("ARTIFACT PREVIEW")
        eyebrow.setObjectName("HeroEyebrow")
        self._detail_title = QLabel(self._translator.text("选择一个产物", "Select an artifact"))
        self._detail_title.setObjectName("CardTitle")
        self._preview = QLabel(
            self._translator.text("选中产物后显示预览", "Select an artifact to preview it")
        )
        self._preview.setObjectName("ArtifactPreview")
        self._preview.setAccessibleName(self._translator.text("产物预览", "Artifact preview"))
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumHeight(210)
        self._preview.setWordWrap(True)
        self._metadata = QPlainTextEdit()
        self._metadata.setObjectName("SchemaViewer")
        self._metadata.setAccessibleName(
            self._translator.text("产物元数据与来源血缘", "Artifact metadata and provenance")
        )
        self._metadata.setFont(fixed_width_font())
        self._metadata.setReadOnly(True)
        actions = QHBoxLayout()
        self._open_file = Button(self._translator.text("打开文件", "Open file"), variant="ghost")
        self._open_file.clicked.connect(self._open_selected_file)
        self._copy_path = Button(self._translator.text("复制路径", "Copy path"), variant="ghost")
        self._copy_path.clicked.connect(self._copy_selected_path)
        actions.addWidget(self._open_file)
        actions.addWidget(self._copy_path)
        actions.addStretch(1)
        layout.addWidget(eyebrow)
        layout.addWidget(self._detail_title)
        layout.addWidget(self._preview)
        layout.addWidget(self._metadata, 1)
        layout.addLayout(actions)
        return detail

    def _artifact_query(self) -> ArtifactQuery:
        kind = self._kind_filter.currentData()
        days = self._period_filter.currentData()
        created_after = datetime.now(UTC) - timedelta(days=days) if isinstance(days, int) else None
        return ArtifactQuery(
            trashed=self._showing_trash,
            kinds=frozenset({kind}) if isinstance(kind, str) else frozenset(),
            created_after=created_after,
        )

    def _selection_changed(self) -> None:
        self._update_actions()
        selection = self._table.selectionModel()
        if selection is not None and selection.selectedRows():
            self._show_detail(selection.selectedRows()[0].row())

    def _show_detail(self, row: int) -> None:
        model = self._table.model()
        if model is None or not 0 <= row < model.rowCount():
            return
        artifact_id = model.index(row, 0).data(Qt.ItemDataRole.UserRole)
        artifact = next((item for item in self._artifacts if item.id == artifact_id), None)
        if artifact is None:
            return
        source = self._artifact_path(artifact)
        exists = source.is_file() and not self._showing_trash
        self._detail_title.setText(artifact.relative_path)
        self._preview.clear()
        pixmap = self._thumbnails.pixmap_for(artifact, source) if exists else None
        if pixmap is not None:
            self._preview.setPixmap(pixmap)
        elif not exists:
            self._preview.setText(
                self._translator.text(
                    "文件已在外部移动，或当前位于回收站",
                    "The file was moved externally or is currently in the trash",
                )
            )
        else:
            self._preview.setText(
                self._translator.text(
                    "{kind}\n{mime}\n当前类型不提供内联预览",
                    "{kind}\n{mime}\nInline preview is unavailable for this type",
                    kind=artifact.kind,
                    mime=artifact.mime_type,
                )
            )
        self._metadata.setPlainText(
            json.dumps(
                {
                    "id": artifact.id,
                    "task_id": artifact.task_id,
                    "kind": artifact.kind,
                    "mime_type": artifact.mime_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                    "relative_path": artifact.relative_path,
                    "file_exists": exists,
                    "created_at": artifact.created_at.isoformat(),
                    "metadata": _plain(artifact.metadata),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        self._open_file.setEnabled(exists)
        self._copy_path.setEnabled(exists)

    def _artifact_path(self, artifact: Artifact) -> Path:
        return (self._root_path / artifact.relative_path).resolve()

    def _open_selected_file(self) -> None:
        artifact = self._selected_artifact()
        if artifact is not None:
            path = self._artifact_path(artifact)
            if path.is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _copy_selected_path(self) -> None:
        artifact = self._selected_artifact()
        if artifact is not None:
            path = self._artifact_path(artifact)
            if path.is_file():
                QApplication.clipboard().setText(str(path))

    def _open_folder(self) -> None:
        self._root_path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._root_path)))

    def _toggle_trash(self) -> None:
        self._showing_trash = not self._showing_trash
        self._toggle.setText(
            self._translator.text("返回产物库", "Back to library")
            if self._showing_trash
            else self._translator.text("查看回收站", "View trash")
        )
        self._lifecycle.setText(
            self._translator.text("恢复产物", "Restore artifact")
            if self._showing_trash
            else self._translator.text("移入回收站", "Move to trash")
        )
        self._purge.setVisible(self._showing_trash)
        self.request_refresh()

    def _selected_artifact(self) -> Artifact | None:
        selection = self._table.selectionModel()
        if selection is None or not selection.selectedRows():
            return None
        row = selection.selectedRows()[0].row()
        model = self._table.model()
        artifact_id = model.index(row, 0).data(Qt.ItemDataRole.UserRole)
        return next((item for item in self._artifacts if item.id == artifact_id), None)

    def _update_actions(self) -> None:
        selected = self._selected_artifact() is not None
        self._lifecycle.setEnabled(selected)
        self._purge.setEnabled(selected and self._showing_trash)

    def _request_lifecycle_action(self) -> None:
        artifact = self._selected_artifact()
        if artifact is None:
            return
        self._start(self._apply_lifecycle(artifact))

    async def _apply_lifecycle(self, artifact: Artifact) -> None:
        try:
            preview = await self._service.preview_artifact_trash(artifact.id)
        except Exception as exc:
            self._handle_error(
                self._translator.text("无法读取产物影响", "Unable to inspect artifact impact"),
                exc,
            )
            return
        if self._showing_trash:
            action = self._translator.text("恢复", "Restore")
            detail = self._translator.text(
                "文件将返回原目录，Task 和工作流血缘保持不变。",
                "The file returns to its original folder while task and workflow provenance remain unchanged.",
            )
        else:
            action = self._translator.text("移入回收站", "Move to trash")
            references = []
            if preview.task_reference:
                references.append(self._translator.text("1 个 Task 来源", "1 source task"))
            if preview.workflow_reference_count:
                references.append(
                    self._translator.text(
                        "{count} 个工作流端口",
                        "{count} workflow ports",
                        count=self._translator.integer(preview.workflow_reference_count),
                    )
                )
            reference_text = (
                self._translator.text("、", ", ").join(references)
                if references
                else self._translator.text("无持久引用", "no persistent references")
            )
            detail = self._translator.text(
                "引用：{references}。可在回收站恢复，血缘记录不会删除。",
                "References: {references}. The artifact can be restored from trash and provenance records are retained.",
                references=reference_text,
            )
        answer = QMessageBox.question(
            self,
            self._translator.text("确认{action}", "Confirm {action}", action=action),
            f"{artifact.relative_path}\n{_size(artifact.size_bytes, self._translator)}\n\n{detail}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        try:
            if self._showing_trash:
                await self._service.restore_artifact(artifact.id)
            else:
                await self._service.trash_artifact(artifact.id)
            await self._refresh()
        except Exception as exc:
            self._handle_error(
                self._translator.text("{action}失败", "{action} failed", action=action),
                exc,
            )

    def _request_purge(self) -> None:
        artifact = self._selected_artifact()
        if artifact is None:
            return
        self._start(self._purge_artifact(artifact))

    async def _purge_artifact(self, artifact: Artifact) -> None:
        try:
            preview = await self._service.preview_artifact_trash(artifact.id)
        except Exception as exc:
            self._handle_error(
                self._translator.text("无法读取产物影响", "Unable to inspect artifact impact"),
                exc,
            )
            return
        if not preview.can_purge:
            QMessageBox.warning(
                self,
                self._translator.text("不能永久删除", "Cannot Delete Permanently"),
                self._translator.text(
                    "产物仍被 {count} 个工作流端口引用，请先解除引用。",
                    "The artifact is still referenced by {count} workflow ports. Remove those references first.",
                    count=self._translator.integer(preview.workflow_reference_count),
                ),
            )
            return
        answer = QMessageBox.warning(
            self,
            self._translator.text("永久删除产物", "Delete Artifact Permanently"),
            self._translator.text(
                "{path}\n{size}\n\n该操作不可撤销，但 Task 记录仍保留。确定继续？",
                "{path}\n{size}\n\nThis cannot be undone, although the task record is retained. Continue?",
                path=artifact.relative_path,
                size=_size(artifact.size_bytes, self._translator),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        try:
            await self._service.purge_artifact(artifact.id, confirm_sha256=artifact.sha256)
            self._thumbnails.invalidate(artifact)
            await self._refresh()
        except Exception as exc:
            self._handle_error(
                self._translator.text("永久删除失败", "Permanent deletion failed"),
                exc,
            )

    def _handle_error(self, title: str, exc: Exception) -> None:
        self._logger.exception("artifact_lifecycle_failed", exc_info=exc)
        QMessageBox.warning(self, title, str(exc))

    def _start(self, operation: Coroutine[Any, Any, object]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            operation.close()
            return
        task = loop.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _size(value: int, translator: Translator | None = None) -> str:
    translator = translator or Translator()
    if value < 1024:
        return f"{translator.integer(value)} B"
    if value < 1024 * 1024:
        return f"{translator.decimal(value / 1024)} KB"
    return f"{translator.decimal(value / (1024 * 1024))} MB"


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain(child) for child in value]
    return value
