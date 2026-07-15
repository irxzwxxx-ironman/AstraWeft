"""Phase 1 dashboard with honest local-service and zero-data states."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from astraweft.application.providers import ProviderService
from astraweft.application.query import QueryService
from astraweft.application.status import ApplicationStatus
from astraweft.application.tasks import TaskService
from astraweft.domain.task import Artifact, Task, TaskStatus
from astraweft.ports.query import TaskQuery
from astraweft.presentation.design_system.tokens import Colors
from astraweft.presentation.i18n import Translator
from astraweft.presentation.widgets.cards import HealthRow, MetricCard, SectionCard
from astraweft.presentation.widgets.controls import Button
from astraweft.presentation.widgets.feedback import EmptyState

_ACTIVE_STATUSES = frozenset(status for status in TaskStatus if not status.terminal)


class DashboardPage(QScrollArea):
    """Local-first overview that does not fabricate usage data."""

    open_page_requested = Signal(str)

    def __init__(
        self,
        status: ApplicationStatus,
        providers: ProviderService | None = None,
        tasks: TaskService | None = None,
        queries: QueryService | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__()
        self._providers_service = providers
        self._tasks_service = tasks
        self._query_service = queries
        self._translator = translator or Translator()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._logger = logging.getLogger("astraweft.presentation.dashboard")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        canvas = QWidget()
        canvas.setObjectName("DashboardCanvas")
        root = QVBoxLayout(canvas)
        root.setContentsMargins(26, 24, 26, 28)
        root.setSpacing(18)

        root.addWidget(self._hero())

        metrics = QHBoxLayout()
        metrics.setSpacing(13)
        self._calls = MetricCard(
            self._translator.text("今日调用", "Calls today"),
            "…",
            self._translator.text("正在读取调用记录", "Loading request logs"),
            Colors.PRIMARY,
        )
        self._success = MetricCard(
            self._translator.text("成功率", "Success rate"),
            "…",
            self._translator.text("正在读取任务状态", "Loading task status"),
            Colors.CYAN,
        )
        self._cost = MetricCard(
            self._translator.text("已知成本", "Known cost"),
            "…",
            self._translator.text(
                "未知成本会单独标记",
                "Unknown costs remain separate",
            ),
            Colors.SUCCESS,
        )
        self._running = MetricCard(
            self._translator.text("运行中", "Running"),
            "…",
            self._translator.text("正在读取任务队列", "Loading task queue"),
            Colors.WARNING,
        )
        for card in (self._calls, self._success, self._cost, self._running):
            metrics.addWidget(card, 1)
        root.addLayout(metrics)

        middle = QHBoxLayout()
        middle.setSpacing(13)
        middle.addWidget(self._health_card(status), 4)
        middle.addWidget(self._provider_card(), 3)
        root.addLayout(middle)

        lower = QHBoxLayout()
        lower.setSpacing(13)
        queue = SectionCard(self._translator.text("任务队列", "Task queue"), "LIVE")
        queue.setMinimumHeight(210)
        self._queue_content = QWidget()
        self._queue_layout = QVBoxLayout(self._queue_content)
        self._queue_layout.setContentsMargins(0, 0, 0, 0)
        self._queue_layout.setSpacing(8)
        queue.add_widget(self._queue_content, 1)
        artifacts = SectionCard(
            self._translator.text("最近产物", "Recent artifacts"),
            "LOCAL",
        )
        artifacts.setMinimumHeight(210)
        self._artifact_content = QWidget()
        self._artifact_layout = QVBoxLayout(self._artifact_content)
        self._artifact_layout.setContentsMargins(0, 0, 0, 0)
        self._artifact_layout.setSpacing(8)
        artifacts.add_widget(self._artifact_content, 1)
        lower.addWidget(queue, 1)
        lower.addWidget(artifacts, 1)
        root.addLayout(lower)
        root.addStretch(1)

        self.setWidget(canvas)
        self._render_queue(())
        self._render_artifacts(())
        if self._query_service is not None or (
            self._providers_service is not None and self._tasks_service is not None
        ):
            QTimer.singleShot(0, self.request_refresh)
        else:
            self._calls.set_value("0")
            self._calls.set_foot(
                self._translator.text("等待第一次模型调用", "Waiting for the first model call")
            )
            self._success.set_value("—")
            self._success.set_foot(
                self._translator.text("暂无可统计任务", "No completed tasks yet")
            )
            self._cost.set_value("—")
            self._running.set_value("0")
            self._running.set_foot(
                self._translator.text("任务队列当前为空", "The task queue is empty")
            )

    def _hero(self) -> QFrame:
        hero = QFrame()
        hero.setObjectName("HeroBanner")
        hero.setMinimumHeight(150)
        shadow = QGraphicsDropShadowEffect(hero)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 10)
        shadow.setColor(Qt.GlobalColor.black)
        hero.setGraphicsEffect(shadow)

        layout = QHBoxLayout(hero)
        layout.setContentsMargins(24, 21, 22, 21)
        layout.setSpacing(18)
        copy = QVBoxLayout()
        copy.setSpacing(6)
        eyebrow = QLabel("LOCAL AI WORKSPACE  /  READY")
        eyebrow.setObjectName("HeroEyebrow")
        title = QLabel(
            self._translator.text("创作工作区已就绪", "Your creative workspace is ready")
        )
        title.setObjectName("HeroTitle")
        body = QLabel(
            self._translator.text(
                "核心服务已在本机启动。连接一个 Provider，开始编排模型、任务与工作流。",
                "Core services are running locally. Connect a Provider to orchestrate models, tasks, and workflows.",
            )
        )
        body.setObjectName("HeroBody")
        body.setWordWrap(True)
        copy.addWidget(eyebrow)
        copy.addWidget(title)
        copy.addWidget(body)
        copy.addStretch(1)
        layout.addLayout(copy, 1)

        action = QPushButton(self._translator.text("连接 Provider  →", "Connect Provider  →"))
        action.setObjectName("PrimaryButton")
        action.setCursor(Qt.CursorShape.PointingHandCursor)
        action.clicked.connect(lambda: self.open_page_requested.emit("providers"))
        action.setFixedWidth(148)
        layout.addWidget(action, 0, Qt.AlignmentFlag.AlignVCenter)
        return hero

    def _health_card(self, status: ApplicationStatus) -> SectionCard:
        card = SectionCard(self._translator.text("本地服务", "Local services"), "SYSTEM HEALTH")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card.add_widget(
            HealthRow(
                self._translator.text("SQLite 数据库", "SQLite database"),
                self._translator.text(
                    "在线" if status.database_online else "异常",
                    "Online" if status.database_online else "Unavailable",
                ),
                Colors.SUCCESS if status.database_online else Colors.DANGER,
            )
        )
        card.add_widget(
            HealthRow(
                self._translator.text("凭据存储", "Credential storage"),
                self._translator.text(
                    "系统密钥环" if status.credential_store_persistent else "仅本次会话",
                    "System keychain"
                    if status.credential_store_persistent
                    else "This session only",
                ),
                Colors.SUCCESS if status.credential_store_persistent else Colors.WARNING,
            )
        )
        card.add_widget(
            HealthRow(
                "Provider Registry",
                self._translator.text("等待配置", "Awaiting configuration"),
                Colors.TEXT_DIM,
            )
        )
        card.add_widget(
            HealthRow(
                "ComfyUI",
                self._translator.text("未连接", "Not connected"),
                Colors.TEXT_DIM,
            )
        )
        return card

    def _provider_card(self) -> SectionCard:
        card = SectionCard(self._translator.text("Provider 状态", "Provider status"), "LOCAL")
        self._provider_summary = QLabel(
            self._translator.text("正在读取 Provider…", "Loading Providers…")
        )
        self._provider_summary.setObjectName("BodyText")
        self._provider_summary.setWordWrap(True)
        card.add_widget(self._provider_summary, 1)
        return card

    def request_refresh(self) -> None:
        self._start(self._refresh())

    async def _refresh(self) -> None:
        if self._query_service is not None:
            try:
                summary, task_page, artifact_page = await asyncio.gather(
                    self._query_service.get_dashboard_summary(),
                    self._query_service.search_tasks(
                        TaskQuery(statuses=_ACTIVE_STATUSES),
                        limit=4,
                    ),
                    self._query_service.search_artifacts(limit=4),
                )
            except Exception:
                self._logger.exception("dashboard_refresh_failed")
                return
            self._apply_summary(
                calls=summary.call_count,
                terminal=summary.terminal_task_count,
                successes=summary.successful_task_count,
                running=summary.running_task_count,
                known=summary.known_costs,
                unknown=summary.unknown_cost_count,
                provider_count=summary.provider_count,
                enabled=summary.enabled_provider_count,
                healthy=summary.healthy_provider_count,
                artifact_count=summary.artifact_count,
                artifact_size=summary.artifact_size_bytes,
            )
            self._render_queue(task_page.items)
            self._render_artifacts(artifact_page.items)
            return
        if self._providers_service is None or self._tasks_service is None:
            return
        try:
            providers = await self._providers_service.list_providers()
            tasks = await self._tasks_service.list_tasks(limit=1000)
            logs = await self._tasks_service.list_request_logs(limit=1000)
        except Exception:
            self._logger.exception("dashboard_refresh_failed")
            return
        local_today = datetime.now().astimezone().date()
        today_logs = tuple(log for log in logs if log.created_at.astimezone().date() == local_today)
        terminal = tuple(task for task in tasks if task.status.terminal)
        successes = sum(task.status is TaskStatus.SUCCESS for task in terminal)
        running = sum(not task.status.terminal for task in tasks)
        known = tuple(
            log for log in today_logs if log.amount_micros is not None and log.currency is not None
        )
        unknown = len(today_logs) - len(known)
        costs = tuple(
            (currency, sum(log.amount_micros or 0 for log in known if log.currency == currency))
            for currency in sorted({str(log.currency) for log in known})
        )
        self._apply_summary(
            calls=len(today_logs),
            terminal=len(terminal),
            successes=successes,
            running=running,
            known=costs,
            unknown=unknown,
            provider_count=len(providers),
            enabled=sum(provider.enabled for provider in providers),
            healthy=sum(provider.health_status.value == "HEALTHY" for provider in providers),
            artifact_count=0,
            artifact_size=0,
        )

    def _apply_summary(
        self,
        *,
        calls: int,
        terminal: int,
        successes: int,
        running: int,
        known: tuple[tuple[str, int], ...],
        unknown: int,
        provider_count: int,
        enabled: int,
        healthy: int,
        artifact_count: int,
        artifact_size: int,
    ) -> None:
        self._calls.set_value(str(calls))
        self._calls.set_foot(
            self._translator.text("今天的 Provider 操作", "Provider operations today")
        )
        self._success.set_value(
            "—" if not terminal else f"{self._translator.decimal(successes / terminal * 100)}%"
        )
        self._success.set_foot(
            self._translator.text(
                "今日 {count} 个终态任务",
                "{count} terminal tasks today",
                count=self._translator.integer(terminal),
            )
        )
        if len(known) == 1:
            currency, total = known[0]
            self._cost.set_value(self._translator.money(currency, total))
        elif known:
            self._cost.set_value(self._translator.text("多币种", "Multiple currencies"))
        else:
            self._cost.set_value(self._translator.text("未知", "Unknown") if unknown else "—")
        self._cost.set_foot(
            self._translator.text(
                "{count} 条成本未知",
                "{count} calls have unknown cost",
                count=self._translator.integer(unknown),
            )
            if unknown
            else self._translator.text("仅汇总已知成本", "Known costs only")
        )
        self._running.set_value(str(running))
        self._running.set_foot(
            self._translator.text(
                "任务运行时正在调度" if running else "任务队列当前为空",
                "Tasks are being scheduled" if running else "The task queue is empty",
            )
        )
        self._provider_summary.setText(
            self._translator.text(
                "{providers} 个已配置  ·  {enabled} 个已启用  ·  {healthy} 个最近检查健康\n"
                "本地产物 {artifacts} 个 / {size}；凭据仅保存在系统密钥环。",
                "{providers} configured  ·  {enabled} enabled  ·  {healthy} recently healthy\n"
                "{artifacts} local artifacts / {size}; credentials stay in the system keychain.",
                providers=self._translator.integer(provider_count),
                enabled=self._translator.integer(enabled),
                healthy=self._translator.integer(healthy),
                artifacts=self._translator.integer(artifact_count),
                size=_size(artifact_size),
            )
        )

    def _render_queue(self, tasks: tuple[Task, ...]) -> None:
        _clear_layout(self._queue_layout)
        if not tasks:
            self._queue_layout.addWidget(
                EmptyState(
                    "◷",
                    self._translator.text("队列空闲", "Queue idle"),
                    self._translator.text(
                        "没有排队、执行、重试或恢复中的任务。",
                        "No tasks are queued, running, retrying, or recovering.",
                    ),
                ),
                1,
            )
            return
        for task in tasks:
            progress = "—" if task.progress is None else f"{task.progress}%"
            self._queue_layout.addWidget(
                _overview_row(
                    task.operation,
                    self._translator.text(
                        "{status}  ·  进度 {progress}",
                        "{status}  ·  Progress {progress}",
                        status=_task_status(task.status, self._translator),
                        progress=progress,
                    ),
                    self._translator.text("任务 {id}", "Task {id}", id=task.id),
                )
            )
        action = Button(
            self._translator.text("查看全部任务", "View all tasks"),
            variant="ghost",
        )
        action.clicked.connect(lambda: self.open_page_requested.emit("tasks"))
        self._queue_layout.addWidget(action)

    def _render_artifacts(self, artifacts: tuple[Artifact, ...]) -> None:
        _clear_layout(self._artifact_layout)
        if not artifacts:
            self._artifact_layout.addWidget(
                EmptyState(
                    "▦",
                    self._translator.text("还没有产物", "No artifacts yet"),
                    self._translator.text(
                        "生成的图片、视频、音频和文本会保存在本机。",
                        "Generated images, video, audio, and text stay on this device.",
                    ),
                ),
                1,
            )
            return
        for artifact in artifacts:
            self._artifact_layout.addWidget(
                _overview_row(
                    artifact.relative_path,
                    f"{artifact.kind}  ·  {_size(artifact.size_bytes)}",
                    self._translator.text("产物 {id}", "Artifact {id}", id=artifact.id),
                )
            )
        action = Button(
            self._translator.text("查看全部产物", "View all artifacts"),
            variant="ghost",
        )
        action.clicked.connect(lambda: self.open_page_requested.emit("artifacts"))
        self._artifact_layout.addWidget(action)

    def _start(self, operation: Coroutine[Any, Any, object]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            operation.close()
            return
        task = loop.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _overview_row(title: str, detail: str, accessible_name: str) -> QFrame:
    row = QFrame()
    row.setObjectName("HealthRow")
    row.setAccessibleName(accessible_name)
    layout = QVBoxLayout(row)
    layout.setContentsMargins(12, 7, 12, 7)
    layout.setSpacing(2)
    title_label = QLabel(title)
    title_label.setObjectName("HealthName")
    title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    detail_label = QLabel(detail)
    detail_label.setObjectName("MutedText")
    layout.addWidget(title_label)
    layout.addWidget(detail_label)
    return row


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def _task_status(status: TaskStatus, translator: Translator) -> str:
    chinese, english = {
        TaskStatus.CREATED: ("已创建", "Created"),
        TaskStatus.QUEUED: ("排队中", "Queued"),
        TaskStatus.SUBMITTING: ("提交中", "Submitting"),
        TaskStatus.RUNNING: ("运行中", "Running"),
        TaskStatus.POLLING: ("等待 Provider", "Waiting for Provider"),
        TaskStatus.RETRY_WAIT: ("等待重试", "Waiting to retry"),
        TaskStatus.CANCELING: ("取消中", "Canceling"),
        TaskStatus.RECOVERING: ("恢复中", "Recovering"),
    }.get(status, (status.value, status.value))
    return translator.text(chinese, english)


def _size(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"
