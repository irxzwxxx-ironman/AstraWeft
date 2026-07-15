"""AstraWeft desktop application shell."""

from __future__ import annotations

import sys
from itertools import pairwise
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from astraweft.application.comfyui import ComfyUIService
from astraweft.application.events import EventBus
from astraweft.application.maintenance import MaintenanceService
from astraweft.application.providers import ProviderService
from astraweft.application.query import QueryService
from astraweft.application.settings import AppSettings, SettingsService
from astraweft.application.status import ApplicationStatus
from astraweft.application.tasks import TaskService
from astraweft.application.workflows import WorkflowExecutionService, WorkflowService
from astraweft.presentation.design_system.tokens import Colors
from astraweft.presentation.i18n import Translator
from astraweft.presentation.notifications import DesktopNotificationController
from astraweft.presentation.pages import (
    ArtifactsPage,
    ComfyUIPage,
    CostAnalysisPage,
    DashboardPage,
    FoundationPage,
    ModelsPage,
    PlaygroundPage,
    ProviderPage,
    RequestLogsPage,
    SettingsPage,
    TaskCenterPage,
    WorkflowPage,
)
from astraweft.presentation.thumbnails import ThumbnailCache
from astraweft.presentation.widgets.controls import Button
from astraweft.presentation.widgets.feedback import EmptyState
from astraweft.presentation.widgets.navigation import NavButton, StatusPill
from astraweft.presentation.widgets.overlays import Drawer

_PAGE_META = {
    "dashboard": ("概览", "Overview", "工作区 / 概览", "Workspace / Overview"),
    "playground": ("Playground", "Playground", "创作 / Playground", "Create / Playground"),
    "workflows": ("工作流", "Workflows", "创作 / 工作流", "Create / Workflows"),
    "tasks": ("任务中心", "Task Center", "运行 / 任务中心", "Run / Task Center"),
    "artifacts": ("产物库", "Artifacts", "运行 / 产物库", "Run / Artifacts"),
    "providers": ("Provider", "Providers", "资源 / Provider", "Resources / Providers"),
    "comfyui": ("ComfyUI", "ComfyUI", "资源 / ComfyUI", "Resources / ComfyUI"),
    "models": ("模型", "Models", "资源 / 模型", "Resources / Models"),
    "logs": ("调用日志", "Request Logs", "运行 / 调用日志", "Run / Request Logs"),
    "costs": ("成本分析", "Cost Analysis", "运行 / 成本分析", "Run / Cost Analysis"),
    "settings": ("设置", "Settings", "系统 / 设置", "System / Settings"),
}

_COMMANDS = (
    ("打开概览", "Open Overview", "dashboard", "home summary metrics 概览"),
    ("打开 Playground", "Open Playground", "playground", "run model generate 运行"),
    ("打开工作流", "Open Workflows", "workflows", "workflow dag canvas 工作流"),
    ("打开任务中心", "Open Task Center", "tasks", "task queue jobs 任务"),
    ("打开产物库", "Open Artifacts", "artifacts", "artifact image video audio trash 产物"),
    ("打开 Provider", "Open Providers", "providers", "provider plugin connection"),
    ("打开模型目录", "Open Models", "models", "model catalog 模型"),
    ("打开 ComfyUI", "Open ComfyUI", "comfyui", "comfy execution template"),
    ("打开调用日志", "Open Request Logs", "logs", "request log cost trace 日志"),
    ("打开成本分析", "Open Cost Analysis", "costs", "cost pricing usage provider model 成本"),
    (
        "打开设置与数据维护",
        "Open Settings and Data",
        "settings",
        "settings backup diagnostics 设置",
    ),
)


class CommandPalette(QDialog):
    """Keyboard-first command launcher with deterministic local filtering."""

    command_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None, translator: Translator | None = None) -> None:
        super().__init__(parent)
        self._translator = translator or Translator()
        self.setObjectName("CommandPalette")
        self.setWindowTitle(self._translator.text("命令面板", "Command Palette"))
        self.setModal(True)
        self.setMinimumWidth(580)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        self._query = QLineEdit()
        self._query.setObjectName("CommandSearch")
        self._query.setAccessibleName(self._translator.text("搜索命令", "Search commands"))
        self._query.setPlaceholderText(
            self._translator.text(
                "输入页面、任务或操作名称…",
                "Type a page, task, or action…",
            )
        )
        self._query.textChanged.connect(self._filter)
        self._query.returnPressed.connect(self._activate_current)
        layout.addWidget(self._query)
        self._list = QListWidget()
        self._list.setObjectName("CommandList")
        self._list.setAccessibleName(self._translator.text("命令结果", "Command results"))
        self._list.itemActivated.connect(self._activate_item)
        layout.addWidget(self._list)
        self._filter("")

    def open_with_query(self, query: str = "") -> None:
        self._query.setText(query)
        self._query.selectAll()
        self.open()
        self._query.setFocus()

    def _filter(self, query: str) -> None:
        normalized = query.strip().casefold()
        self._list.clear()
        for chinese, english, command, keywords in _COMMANDS:
            label = self._translator.text(chinese, english)
            haystack = f"{label} {keywords}".casefold()
            if normalized and normalized not in haystack:
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, command)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _activate_current(self) -> None:
        item = self._list.currentItem()
        if item is not None:
            self._activate_item(item)

    def _activate_item(self, item: QListWidgetItem) -> None:
        command = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(command, str):
            self.command_selected.emit(command)
            self.accept()


class MainWindow(QMainWindow):
    """Modern shell that owns navigation but no business persistence."""

    def __init__(
        self,
        status: ApplicationStatus,
        provider_service: ProviderService | None = None,
        task_service: TaskService | None = None,
        workflow_service: WorkflowService | None = None,
        workflow_execution: WorkflowExecutionService | None = None,
        comfyui_service: ComfyUIService | None = None,
        maintenance_service: MaintenanceService | None = None,
        query_service: QueryService | None = None,
        events: EventBus | None = None,
        settings_service: SettingsService | None = None,
        *,
        system_notifications: bool = True,
        language: str = "zh_CN",
        app_settings: AppSettings | None = None,
    ) -> None:
        super().__init__()
        self._status = status
        self._provider_service = provider_service
        self._task_service = task_service
        self._workflow_service = workflow_service
        self._workflow_execution = workflow_execution
        self._comfyui_service = comfyui_service
        self._maintenance_service = maintenance_service
        self._query_service = query_service
        self._settings_service = settings_service
        self._app_settings = app_settings or AppSettings(
            language="en_US" if language == "en_US" else "zh_CN",
            system_notifications=system_notifications,
        )
        self._translator = Translator(language)
        self._notifications: DesktopNotificationController | None = None
        self._buttons: dict[str, NavButton] = {}
        self._page_indexes: dict[str, int] = {}
        self.setWindowTitle("AstraWeft · Local AI Workspace")
        self.setMinimumSize(1180, 720)

        root = QWidget()
        root.setObjectName("AppRoot")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_sidebar())
        root_layout.addWidget(self._build_workspace(), 1)
        self.setCentralWidget(root)
        self.resize(1440, 900)

        search_shortcut = "Meta+K" if sys.platform == "darwin" else "Ctrl+K"
        shortcut = QShortcut(QKeySequence(search_shortcut), self)
        self._command_palette = CommandPalette(self, self._translator)
        self._command_palette.command_selected.connect(self._show_page)
        shortcut.activated.connect(self._command_palette.open_with_query)
        settings_shortcut = QShortcut(
            QKeySequence("Meta+," if sys.platform == "darwin" else "Ctrl+,"), self
        )
        settings_shortcut.activated.connect(lambda: self._show_page("settings"))
        self._configure_focus_order()
        self._show_page("dashboard")
        if events is not None:
            self._notifications = DesktopNotificationController(
                events,
                self,
                enabled=system_notifications,
                translator=self._translator,
            )

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(224)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(15, 18, 15, 16)
        layout.setSpacing(5)

        brand = QHBoxLayout()
        brand.setContentsMargins(4, 0, 2, 17)
        brand.setSpacing(10)
        mark = QLabel("A")
        mark.setObjectName("LogoMark")
        mark.setFixedSize(40, 40)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        names = QVBoxLayout()
        names.setSpacing(0)
        name = QLabel("ASTRAWEFT")
        name.setObjectName("BrandName")
        meta = QLabel("LOCAL AI STUDIO")
        meta.setObjectName("BrandMeta")
        names.addWidget(name)
        names.addWidget(meta)
        brand.addWidget(mark)
        brand.addLayout(names)
        brand.addStretch(1)
        layout.addLayout(brand)

        self._add_section(layout, self._translator.text("工作区", "Workspace"))
        for page_id, glyph, chinese, english in (
            ("dashboard", "⌂", "概览", "Overview"),
            ("playground", "✦", "Playground", "Playground"),
            ("workflows", "⌘", "工作流", "Workflows"),
        ):
            self._add_nav(layout, page_id, glyph, self._translator.text(chinese, english))

        self._add_section(layout, self._translator.text("运行", "Run"), top_margin=11)
        for page_id, glyph, chinese, english in (
            ("tasks", "◷", "任务中心", "Task Center"),
            ("artifacts", "▦", "产物库", "Artifacts"),
            ("logs", "≡", "调用日志", "Request Logs"),
            ("costs", "¢", "成本分析", "Cost Analysis"),
        ):
            self._add_nav(layout, page_id, glyph, self._translator.text(chinese, english))

        self._add_section(layout, self._translator.text("资源", "Resources"), top_margin=11)
        for page_id, glyph, chinese, english in (
            ("providers", "⬡", "Provider", "Providers"),
            ("models", "◉", "模型", "Models"),
            ("comfyui", "◈", "ComfyUI", "ComfyUI"),
        ):
            self._add_nav(layout, page_id, glyph, self._translator.text(chinese, english))

        layout.addStretch(1)
        status_text = self._translator.text(
            "本地核心 · 在线" if self._status.database_online else "本地核心 · 降级",
            "LOCAL CORE · ONLINE" if self._status.database_online else "LOCAL CORE · DEGRADED",
        )
        layout.addWidget(
            StatusPill(
                status_text, Colors.SUCCESS if self._status.database_online else Colors.DANGER
            )
        )
        self._add_nav(
            layout,
            "settings",
            "⚙",
            self._translator.text("设置", "Settings"),
        )
        return sidebar

    @staticmethod
    def _add_section(layout: QVBoxLayout, text: str, top_margin: int = 0) -> None:
        if top_margin:
            layout.addSpacing(top_margin)
        label = QLabel(text.upper())
        label.setObjectName("NavSection")
        label.setContentsMargins(10, 4, 0, 5)
        layout.addWidget(label)

    def _add_nav(self, layout: QVBoxLayout, page_id: str, glyph: str, label: str) -> None:
        button = NavButton(glyph, label, page_id)
        button.clicked.connect(
            lambda _checked=False, destination=page_id: self._show_page(destination)
        )
        self._buttons[page_id] = button
        layout.addWidget(button)

    def _build_workspace(self) -> QWidget:
        workspace = QWidget()
        self._workspace = workspace
        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_topbar())

        self._stack = QStackedWidget()
        if self._provider_service is not None:
            provider_page: QWidget = ProviderPage(self._provider_service)
            models_page: QWidget = ModelsPage(self._provider_service)
            if isinstance(provider_page, ProviderPage) and isinstance(models_page, ModelsPage):
                provider_page.catalog_changed.connect(models_page.request_refresh)
        else:
            provider_page = FoundationPage(
                "⬡",
                "连接第一个 Provider",
                "Provider 插件将独立发现、测试连接并同步模型；API Key 只进入系统密钥环。",
                "Provider 服务尚未注入",
            )
            models_page = FoundationPage(
                "◉",
                "模型目录",
                "模型能力、参数 JSON Schema、输出类型和版本化价格将从 Provider 动态同步。",
                "等待模型同步",
            )
        if self._provider_service is not None and self._task_service is not None:
            playground_page: QWidget = PlaygroundPage(self._provider_service, self._task_service)
            task_page: QWidget = TaskCenterPage(self._task_service, self._query_service)
            artifact_page: QWidget = ArtifactsPage(
                self._task_service,
                Path(self._status.data_directory) / "artifacts",
                self._query_service,
                ThumbnailCache(
                    Path(self._status.cache_directory or self._status.data_directory) / "thumbnails"
                ),
            )
            logs_page: QWidget = RequestLogsPage(self._task_service, self._query_service)
            if isinstance(provider_page, ProviderPage) and isinstance(
                playground_page, PlaygroundPage
            ):
                provider_page.catalog_changed.connect(playground_page.request_refresh)
            if (
                isinstance(playground_page, PlaygroundPage)
                and isinstance(task_page, TaskCenterPage)
                and isinstance(artifact_page, ArtifactsPage)
                and isinstance(logs_page, RequestLogsPage)
            ):
                playground_page.task_changed.connect(task_page.request_refresh)
                playground_page.task_changed.connect(artifact_page.request_refresh)
                playground_page.task_changed.connect(logs_page.request_refresh)
        else:
            playground_page = FoundationPage(
                "✦",
                "Playground 即将就绪",
                "Provider 与模型目录建立后，这里会按 Schema 自动生成参数表单，并展示请求、响应、成本与产物。",
                "等待 Provider 模块",
            )
            task_page = FoundationPage(
                "◷",
                "任务中心",
                "同步和异步 Provider 调用会统一进入可重试、可取消、可恢复的任务状态机。",
                "等待 Task Runtime",
            )
            artifact_page = FoundationPage(
                "▦",
                "本地产物库",
                "图片、视频、音频与 JSON 产物将按哈希校验、来源血缘和回收站策略管理。",
                "等待首个生成任务",
            )
            logs_page = FoundationPage(
                "≡",
                "调用日志",
                "每次外部请求会关联 Task、Attempt 和 trace ID；正文默认脱敏，未知成本不会显示为零。",
                "等待第一次外部调用",
            )
        dashboard_page = DashboardPage(
            self._status,
            self._provider_service,
            self._task_service,
            self._query_service,
            self._translator,
        )
        if self._query_service is not None:
            costs_page: QWidget = CostAnalysisPage(self._query_service, self._translator)
        else:
            costs_page = FoundationPage(
                "¢",
                "成本分析",
                "Provider 已知价格会按模型与币种汇总，未知成本不会被计为零。",
                "等待只读查询服务",
            )
        if isinstance(playground_page, PlaygroundPage):
            playground_page.task_changed.connect(dashboard_page.request_refresh)
        if self._comfyui_service is not None:
            comfyui_page: QWidget = ComfyUIPage(self._comfyui_service)
            if isinstance(comfyui_page, ComfyUIPage):
                comfyui_page.health_changed.connect(self._comfyui_status.set_text)
        else:
            comfyui_page = FoundationPage(
                "◈",
                "连接 ComfyUI",
                "配置本机执行实例、导入 API Format 模板并把图像或视频节点加入工作流。",
                "等待 ComfyUI Adapter",
            )
        if (
            self._workflow_service is not None
            and self._workflow_execution is not None
            and self._provider_service is not None
        ):
            workflow_page: QWidget = WorkflowPage(
                self._workflow_service,
                self._workflow_execution,
                self._provider_service,
                self._comfyui_service,
            )
            if isinstance(workflow_page, WorkflowPage):
                workflow_page.workflow_changed.connect(dashboard_page.request_refresh)
        else:
            workflow_page = FoundationPage(
                "⌘",
                "工作流编排",
                "不可变版本、类型安全端口和可恢复节点运行会在核心任务运行时完成后启用。",
                "等待 Workflow Engine",
            )
        settings_page: QWidget
        if self._maintenance_service is not None:
            settings_page = SettingsPage(
                self._maintenance_service,
                Path(self._status.data_directory),
                self._settings_service,
                self._app_settings,
                self._translator,
            )
            settings_page.notification_preference_changed.connect(self._set_system_notifications)
        else:
            settings_page = FoundationPage(
                "⚙",
                "本地设置",
                "备份、恢复、数据目录与脱敏诊断将在这里管理。",
                "维护服务尚未注入",
            )
        pages: list[tuple[str, QWidget]] = [
            ("dashboard", dashboard_page),
            ("playground", playground_page),
            ("workflows", workflow_page),
            ("tasks", task_page),
            ("artifacts", artifact_page),
            ("providers", provider_page),
            ("models", models_page),
            ("comfyui", comfyui_page),
            ("logs", logs_page),
            ("costs", costs_page),
            ("settings", settings_page),
        ]
        for page_id, page in pages:
            self._page_indexes[page_id] = self._stack.addWidget(page)
        dashboard = pages[0][1]
        if isinstance(dashboard, DashboardPage):
            dashboard.open_page_requested.connect(self._show_page)
        layout.addWidget(self._stack, 1)
        layout.addWidget(self._build_statusbar())
        workspace_layout.addWidget(content, 1)

        self._queue_drawer = Drawer("任务速览", workspace)
        self._queue_drawer.set_content(
            EmptyState(
                "◷",
                "队列当前为空",
                "Provider 任务开始后，这里会保留进度、状态与快速操作。",
            )
        )
        return workspace

    def _build_topbar(self) -> QFrame:
        topbar = QFrame()
        topbar.setObjectName("Topbar")
        topbar.setFixedHeight(66)
        layout = QHBoxLayout(topbar)
        layout.setContentsMargins(24, 0, 22, 0)
        layout.setSpacing(12)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        self._title = QLabel(self._translator.text("概览", "Overview"))
        self._title.setObjectName("PageTitle")
        self._breadcrumb = QLabel(self._translator.text("工作区 / 概览", "Workspace / Overview"))
        self._breadcrumb.setObjectName("Breadcrumb")
        titles.addWidget(self._title)
        titles.addWidget(self._breadcrumb)
        layout.addLayout(titles)
        layout.addStretch(1)

        self._search = QLineEdit()
        self._search.setObjectName("GlobalSearch")
        self._search.setPlaceholderText(
            self._translator.text(
                "⌕  搜索任务、模型或工作流    ⌘K",
                "⌕  Search tasks, models, or workflows    ⌘K",
            )
        )
        self._search.setClearButtonEnabled(True)
        self._search.setAccessibleName(self._translator.text("全局搜索", "Global search"))
        self._search.setToolTip(
            self._translator.text(
                "按 Enter 在命令面板中搜索页面和操作",
                "Press Enter to search pages and actions in the command palette",
            )
        )
        self._search.returnPressed.connect(
            lambda: self._command_palette.open_with_query(self._search.text())
        )
        layout.addWidget(self._search)
        self._comfyui_status = StatusPill(
            self._translator.text("COMFYUI 未配置", "COMFYUI NOT CONFIGURED"),
            Colors.TEXT_DIM,
        )
        layout.addWidget(self._comfyui_status)

        self._queue_button = Button(
            self._translator.text("队列  0", "QUEUE  0"),
            variant="ghost",
        )
        self._queue_button.clicked.connect(self._toggle_queue_drawer)
        layout.addWidget(self._queue_button)
        avatar = QLabel("AW")
        avatar.setObjectName("Avatar")
        avatar.setFixedSize(36, 36)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(avatar)
        return topbar

    def _build_statusbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("StatusBar")
        bar.setFixedHeight(28)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(8)

        dot = QLabel("●")
        dot.setStyleSheet(
            f"color: {Colors.SUCCESS if self._status.database_online else Colors.DANGER}; font-size: 8px;"
        )
        database = QLabel(
            self._translator.text(
                "本地数据库正常" if self._status.database_online else "本地数据库异常",
                "Local database online"
                if self._status.database_online
                else "Local database unavailable",
            )
        )
        database.setObjectName("StatusText")
        separator = QLabel("│")
        separator.setObjectName("StatusText")
        data_path = QLabel(
            self._translator.text(
                "数据目录  {path}",
                "Data directory  {path}",
                path=self._status.data_directory,
            )
        )
        data_path.setObjectName("StatusText")
        data_path.setToolTip(self._status.data_directory)
        data_path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(dot)
        layout.addWidget(database)
        layout.addWidget(separator)
        layout.addWidget(data_path, 1)
        version = QLabel(f"v{self._status.version}")
        version.setObjectName("StatusText")
        layout.addWidget(version)
        return bar

    def _show_page(self, page_id: str) -> None:
        if page_id not in self._page_indexes:
            return
        for button_id, button in self._buttons.items():
            button.setChecked(button_id == page_id)
        self._stack.setCurrentIndex(self._page_indexes[page_id])
        chinese_title, english_title, chinese_crumb, english_crumb = _PAGE_META[page_id]
        self._title.setText(self._translator.text(chinese_title, english_title))
        self._breadcrumb.setText(self._translator.text(chinese_crumb, english_crumb))

    def _toggle_queue_drawer(self) -> None:
        self._position_queue_drawer()
        self._queue_drawer.toggle()

    def _set_system_notifications(self, enabled: bool) -> None:
        if self._notifications is not None:
            self._notifications.set_enabled(enabled)

    def _close_transient_surfaces(self) -> None:
        self._queue_drawer.close_drawer()

    def _configure_focus_order(self) -> None:
        controls: list[QWidget] = [*self._buttons.values(), self._search, self._queue_button]
        for current, following in pairwise(controls):
            QWidget.setTabOrder(current, following)

    def activate_from_external_instance(self) -> None:
        """Restore and focus this window when a second process asks to open."""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._position_queue_drawer()

    def _position_queue_drawer(self) -> None:
        width = self._queue_drawer.width()
        self._queue_drawer.setGeometry(
            max(0, self._workspace.width() - width),
            0,
            width,
            self._workspace.height(),
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self._queue_drawer.isVisible():
            self._close_transient_surfaces()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._notifications is not None:
            self._notifications.close()
        super().closeEvent(event)
