"""Provider plugin and configured-instance management page."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine, Mapping
from typing import Any, cast

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from astraweft.application.providers import (
    CreateProvider,
    PluginManagementEntry,
    ProviderService,
    UpdateProvider,
)
from astraweft.domain.provider import Provider, ProviderHealth
from astraweft.ports.provider_plugins import PluginLoadState, PluginRecord
from astraweft.ports.secrets import SecretValue
from astraweft.presentation.i18n import Translator
from astraweft.presentation.widgets.controls import Badge, BadgeTone, Button, TextInput
from astraweft.presentation.widgets.feedback import EmptyState, Toast, ToastTone
from astraweft.presentation.widgets.schema_form import SchemaForm, SchemaFormError


class ProviderDialog(QDialog):
    """Descriptor-driven create/edit dialog with secret-safe credential fields."""

    def __init__(
        self,
        records: tuple[PluginRecord, ...],
        *,
        provider: Provider | None = None,
        parent: QWidget | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator or Translator()
        self.setObjectName("ProviderDialog")
        self.setWindowTitle(
            self._translator.text("编辑 Provider", "Edit Provider")
            if provider
            else self._translator.text("添加 Provider", "Add Provider")
        )
        self.setModal(True)
        self.resize(650, 680)
        self._provider = provider
        self._settings_form: SchemaForm | None = None
        self._credential_form: SchemaForm | None = None
        self._records = tuple(
            record
            for record in records
            if record.state is PluginLoadState.READY and record.descriptor is not None
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(16)
        title = QLabel(self._translator.text("配置 Provider 连接", "Configure Provider Connection"))
        title.setObjectName("DialogTitle")
        body = QLabel(
            self._translator.text(
                "界面由插件 Schema 自动生成；凭据不会进入数据库，系统密钥环不可用时仅保留本次会话。",
                "This form is generated from the plugin schema. Credentials never enter the database and remain session-only when the system credential store is unavailable.",
            )
        )
        body.setObjectName("BodyText")
        body.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(body)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(1, 1, 8, 1)
        self._content_layout.setSpacing(14)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        self._plugin_combo = QComboBox()
        self._plugin_combo.setObjectName("SelectInput")
        self._plugin_combo.setAccessibleName(
            self._translator.text("Provider 插件", "Provider plugin")
        )
        for record in self._records:
            descriptor = record.descriptor
            if descriptor is not None:
                self._plugin_combo.addItem(descriptor.name, userData=record)
        self._content_layout.addWidget(
            _field(self._translator.text("Provider 插件", "Provider plugin"), self._plugin_combo)
        )

        self._name = TextInput(
            self._translator.text("Provider 名称", "Provider name"),
            placeholder=self._translator.text(
                "例如：本地开发 Provider", "For example: Local Development Provider"
            ),
        )
        self._endpoint = TextInput(
            self._translator.text("服务地址", "Service endpoint"),
            placeholder=self._translator.text(
                "使用插件默认地址", "Use the plugin default endpoint"
            ),
        )
        self._content_layout.addWidget(_field(self._translator.text("名称", "Name"), self._name))
        self._content_layout.addWidget(
            _field(
                self._translator.text("服务地址 (可选)", "Service endpoint (optional)"),
                self._endpoint,
            )
        )
        self._description = QLabel()
        self._description.setObjectName("FormHint")
        self._description.setWordWrap(True)
        self._content_layout.addWidget(self._description)

        self._forms_host = QWidget()
        self._forms_layout = QVBoxLayout(self._forms_host)
        self._forms_layout.setContentsMargins(0, 0, 0, 0)
        self._forms_layout.setSpacing(16)
        self._content_layout.addWidget(self._forms_host)
        self._content_layout.addStretch(1)

        self._error = QLabel()
        self._error.setObjectName("FormError")
        self._error.setWordWrap(True)
        self._error.hide()
        root.addWidget(self._error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        if cancel is not None:
            cancel.setText(self._translator.text("取消", "Cancel"))
        if save is not None:
            save.setText(self._translator.text("保存", "Save"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._plugin_combo.currentIndexChanged.connect(self._rebuild_forms)
        if provider is not None:
            for index in range(self._plugin_combo.count()):
                record = self._plugin_combo.itemData(index)
                if (
                    isinstance(record, PluginRecord)
                    and record.descriptor is not None
                    and record.descriptor.plugin_id == provider.plugin_id
                ):
                    self._plugin_combo.setCurrentIndex(index)
                    break
            self._plugin_combo.setEnabled(False)
            self._name.setText(provider.name)
            self._endpoint.setText(provider.endpoint or "")
        self._rebuild_forms()

    def accept(self) -> None:
        try:
            _ = self.settings()
            _ = self.credentials()
            if not self._name.text().strip():
                raise SchemaFormError(self._translator.text("名称不能为空", "Name is required"))
            descriptor = self._selected_record().descriptor
            if (
                descriptor is not None
                and descriptor.endpoint_required
                and not self._endpoint.text().strip()
            ):
                raise SchemaFormError(
                    self._translator.text(
                        "请填写第三方 API 的 HTTPS 服务地址",
                        "Enter the third-party API HTTPS endpoint",
                    )
                )
        except SchemaFormError as exc:
            self._error.setText(str(exc))
            self._error.show()
            return
        self._error.hide()
        super().accept()

    def create_command(self) -> CreateProvider:
        record = self._selected_record()
        descriptor = record.descriptor
        if descriptor is None:
            raise SchemaFormError(
                self._translator.text("没有可用插件", "No compatible plugin is available")
            )
        credentials = self.credentials()
        if credentials is None:
            raise SchemaFormError(
                self._translator.text("请填写凭据", "Enter the required credentials")
            )
        return CreateProvider(
            plugin_id=descriptor.plugin_id,
            name=self._name.text(),
            settings=self.settings(),
            credentials=credentials,
            endpoint=self._endpoint.text() or None,
        )

    def update_command(self) -> UpdateProvider:
        if self._provider is None:
            raise RuntimeError("dialog is not editing a Provider")
        return UpdateProvider(
            provider_id=self._provider.id,
            name=self._name.text(),
            settings=self.settings(),
            endpoint=self._endpoint.text() or None,
            enabled=self._provider.enabled,
            credentials=self.credentials(),
        )

    def settings(self) -> Mapping[str, object]:
        if self._settings_form is None:
            raise SchemaFormError(
                self._translator.text(
                    "插件设置表单不可用", "The plugin settings form is unavailable"
                )
            )
        return self._settings_form.values()

    def credentials(self) -> dict[str, SecretValue] | None:
        if self._credential_form is None:
            raise SchemaFormError(
                self._translator.text(
                    "插件凭据表单不可用", "The plugin credential form is unavailable"
                )
            )
        return self._credential_form.secret_values(required=self._provider is None)

    def _selected_record(self) -> PluginRecord:
        record = self._plugin_combo.currentData()
        if not isinstance(record, PluginRecord):
            raise SchemaFormError(
                self._translator.text(
                    "没有可用的 Provider 插件", "No compatible Provider plugin is available"
                )
            )
        return record

    def _rebuild_forms(self) -> None:
        while self._forms_layout.count():
            item = self._forms_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        try:
            record = self._selected_record()
        except SchemaFormError:
            self._description.setText(
                self._translator.text(
                    "当前没有通过兼容性检查的插件。",
                    "No plugin currently passes compatibility checks.",
                )
            )
            self._settings_form = None
            self._credential_form = None
            return
        descriptor = record.descriptor
        if descriptor is None:
            return
        self._description.setText(descriptor.description)
        if self._provider is None:
            self._name.setText(descriptor.name)
            self._endpoint.setText(descriptor.default_endpoint or "")
        fixed_endpoint = descriptor.default_endpoint is not None
        self._endpoint.setReadOnly(fixed_endpoint)
        if fixed_endpoint:
            self._endpoint.setToolTip(
                self._translator.text(
                    "该服务地址由 Provider 插件固定，避免密钥发送到其他主机。",
                    "The Provider plugin fixes this endpoint so credentials cannot be sent to another host.",
                )
            )
            self._description.setText(
                self._translator.text(
                    "{description}\n服务地址由插件固定：{endpoint}",
                    "{description}\nEndpoint fixed by plugin: {endpoint}",
                    description=descriptor.description,
                    endpoint=descriptor.default_endpoint,
                )
            )
        else:
            self._endpoint.setPlaceholderText(
                self._translator.text(
                    "例如：https://api.example.com/v1",
                    "For example: https://api.example.com/v1",
                )
                if descriptor.endpoint_required
                else self._translator.text("使用插件默认地址", "Use the plugin default endpoint")
            )
            self._endpoint.setToolTip(
                self._translator.text(
                    "仅该精确 HTTPS 域名会获得网络权限。",
                    "Only this exact HTTPS host receives network permission.",
                )
                if descriptor.endpoint_required
                else ""
            )
        settings_section = _section(self._translator.text("连接设置", "Connection settings"))
        self._settings_form = SchemaForm(
            descriptor.settings_schema,
            descriptor.settings_ui_schema,
            initial=self._provider.config if self._provider is not None else None,
            translator=self._translator,
        )
        settings_layout = settings_section.layout()
        if settings_layout is None:
            raise RuntimeError("settings section has no layout")
        settings_layout.addWidget(self._settings_form)
        self._forms_layout.addWidget(settings_section)

        credential_title = (
            self._translator.text(
                "更新凭据 (留空则保持不变)",
                "Update credentials (leave blank to keep unchanged)",
            )
            if self._provider
            else self._translator.text("凭据", "Credentials")
        )
        credentials_section = _section(credential_title)
        self._credential_form = SchemaForm(
            descriptor.credential_schema,
            secret_mode=True,
            translator=self._translator,
        )
        credential_layout = credentials_section.layout()
        if credential_layout is None:
            raise RuntimeError("credential section has no layout")
        credential_layout.addWidget(self._credential_form)
        self._forms_layout.addWidget(credentials_section)


class PluginManagerDialog(QDialog):
    """Inspect local plugin compatibility and persist enablement choices."""

    def __init__(
        self,
        service: ProviderService,
        parent: QWidget | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator or Translator()
        self.setObjectName("PluginManagerDialog")
        self.setWindowTitle(self._translator.text("Provider 插件管理", "Provider Plugin Manager"))
        self.setModal(True)
        self.resize(760, 650)
        self._service = service
        self._tasks: set[asyncio.Task[Any]] = set()
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel(self._translator.text("插件管理", "Plugin Manager"))
        title.setObjectName("DialogTitle")
        self._summary = QLabel(
            self._translator.text("正在检查本机插件…", "Checking local plugins…")
        )
        self._summary.setObjectName("BodyText")
        titles.addWidget(title)
        titles.addWidget(self._summary)
        header.addLayout(titles)
        header.addStretch(1)
        rescan = Button(self._translator.text("重新扫描", "Rescan"), variant="ghost")
        rescan.clicked.connect(lambda: self._start(self._rescan()))
        header.addWidget(rescan)
        root.addLayout(header)
        notice = QLabel(
            self._translator.text(
                "启停选择保存在本地设置。停用不删除 Provider、模型、Task 或密钥；已运行实例的新调用将不可用。",
                "Enablement is saved in local settings. Disabling does not remove providers, models, tasks, or credentials, but prevents new calls from configured instances.",
            )
        )
        notice.setObjectName("BodyText")
        notice.setWordWrap(True)
        root.addWidget(notice)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self._list = QVBoxLayout(content)
        self._list.setContentsMargins(0, 0, 8, 0)
        self._list.setSpacing(12)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close is not None:
            close.setText(self._translator.text("关闭", "Close"))
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        QTimer.singleShot(0, lambda: self._start(self.refresh()))

    async def refresh(self) -> None:
        entries = await self._service.plugin_management()
        _clear_layout(self._list)
        ready = sum(entry.record.state is PluginLoadState.READY for entry in entries)
        self._summary.setText(
            self._translator.text(
                "{total} 个已发现 · {ready} 个可用 · 兼容性由静态 manifest 验证",
                "{total} discovered · {ready} available · compatibility verified from static manifests",
                total=self._translator.integer(len(entries)),
                ready=self._translator.integer(ready),
            )
        )
        for entry in entries:
            self._list.addWidget(self._entry_card(entry))
        self._list.addStretch(1)

    def _entry_card(self, entry: PluginManagementEntry) -> QFrame:
        record = entry.record
        manifest = record.manifest
        card = QFrame()
        card.setObjectName("ProviderCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        header = QHBoxLayout()
        name = QLabel(manifest.name if manifest is not None else record.entry_point_name)
        name.setObjectName("CardTitle")
        header.addWidget(name)
        header.addStretch(1)
        tone, state_text = _plugin_state(record.state, self._translator)
        header.addWidget(Badge(state_text, tone=tone))
        layout.addLayout(header)
        if manifest is not None:
            identity = QLabel(
                f"{manifest.plugin_id}  ·  v{manifest.version}  ·  {record.distribution_name}"
            )
            identity.setObjectName("BodyText")
            compatibility = QLabel(
                self._translator.text(
                    "Plugin API {api}  ·  Python {python}  ·  {providers} 个 Provider / {models} 个模型",
                    "Plugin API {api}  ·  Python {python}  ·  {providers} providers / {models} models",
                    api=manifest.plugin_api,
                    python=manifest.python,
                    providers=self._translator.integer(entry.provider_count),
                    models=self._translator.integer(entry.model_count),
                )
            )
            compatibility.setObjectName("MutedText")
            package_hash = QLabel(
                "Package SHA-256  "
                + (
                    record.package_hash[:20] + "…"
                    if record.package_hash
                    else self._translator.text("未可用", "Unavailable")
                )
            )
            package_hash.setObjectName("MutedText")
            layout.addWidget(identity)
            layout.addWidget(compatibility)
            layout.addWidget(package_hash)
        if record.diagnostic:
            diagnostic = QLabel(record.diagnostic)
            diagnostic.setObjectName("MutedText")
            diagnostic.setWordWrap(True)
            layout.addWidget(diagnostic)
        actions = QHBoxLayout()
        actions.addStretch(1)
        if manifest is not None and record.state in {
            PluginLoadState.READY,
            PluginLoadState.DISABLED,
        }:
            enabled = record.state is PluginLoadState.READY
            toggle = Button(
                self._translator.text("停用插件", "Disable plugin")
                if enabled
                else self._translator.text("启用插件", "Enable plugin"),
                variant="ghost",
            )
            toggle.clicked.connect(
                lambda _checked=False, item=entry, target=not enabled: self._confirm_toggle(
                    item, target
                )
            )
            actions.addWidget(toggle)
        layout.addLayout(actions)
        return card

    def _confirm_toggle(self, entry: PluginManagementEntry, enabled: bool) -> None:
        manifest = entry.record.manifest
        if manifest is None:
            return
        if not enabled:
            answer = QMessageBox.question(
                self,
                self._translator.text("确认停用插件", "Disable Plugin"),
                self._translator.text(
                    "{name}\n\n影响：{providers} 个 Provider，其中 {enabled} 个已启用；{models} 个模型。\n历史数据和密钥保留，重新启用后可恢复调用。",
                    "{name}\n\nImpact: {providers} providers, including {enabled} enabled; {models} models.\nHistorical data and credentials are retained, and calls resume after re-enabling.",
                    name=manifest.name,
                    providers=self._translator.integer(entry.provider_count),
                    enabled=self._translator.integer(entry.enabled_provider_count),
                    models=self._translator.integer(entry.model_count),
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return
        self._start(self._toggle(manifest.plugin_id, enabled))

    async def _toggle(self, plugin_id: str, enabled: bool) -> None:
        try:
            await self._service.set_plugin_enabled(plugin_id, enabled=enabled)
            await self.refresh()
        except Exception as exc:
            QMessageBox.warning(
                self,
                self._translator.text("插件状态更新失败", "Unable to update plugin state"),
                str(exc),
            )

    async def _rescan(self) -> None:
        try:
            await self._service.refresh_plugin_catalog()
            await self.refresh()
        except Exception as exc:
            QMessageBox.warning(
                self,
                self._translator.text("插件扫描失败", "Plugin scan failed"),
                str(exc),
            )

    def _start(self, operation: Coroutine[Any, Any, object]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            operation.close()
            return
        task = loop.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


class ProviderPage(QWidget):
    """Real Provider management surface backed by ProviderService."""

    catalog_changed = Signal()

    def __init__(self, service: ProviderService, translator: Translator | None = None) -> None:
        super().__init__()
        self.setObjectName("ProviderPage")
        self._service = service
        self._translator = translator or Translator()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._logger = logging.getLogger("astraweft.presentation.providers")
        self._toast: Toast | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 27, 30, 24)
        root.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel(self._translator.text("Provider 连接", "Provider Connections"))
        title.setObjectName("ContentTitle")
        subtitle = QLabel(
            self._translator.text(
                "管理插件、凭据、连接状态与远程模型目录",
                "Manage plugins, credentials, connection health, and remote model catalogs",
            )
        )
        subtitle.setObjectName("BodyText")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        refresh = Button(self._translator.text("刷新", "Refresh"), variant="ghost")
        refresh.clicked.connect(lambda: self._start(self.refresh()))
        plugins = Button(self._translator.text("插件管理", "Plugin manager"), variant="ghost")
        plugins.clicked.connect(self._open_plugins)
        self._add = Button(self._translator.text("+ 添加 Provider", "+ Add Provider"))
        self._add.clicked.connect(self._open_add)
        header.addWidget(refresh)
        header.addWidget(plugins)
        header.addWidget(self._add)
        root.addLayout(header)

        records = self._service.plugin_records()
        ready = sum(record.state is PluginLoadState.READY for record in records)
        isolated = len(records) - ready
        plugin_bar = QFrame()
        plugin_bar.setObjectName("PluginSummary")
        plugin_layout = QHBoxLayout(plugin_bar)
        plugin_layout.setContentsMargins(14, 10, 14, 10)
        plugin_layout.addWidget(
            Badge(
                self._translator.text(
                    "{count} 个插件可用",
                    "{count} plugins available",
                    count=self._translator.integer(ready),
                ),
                tone="success" if ready else "warning",
            )
        )
        if isolated:
            plugin_layout.addWidget(
                Badge(
                    self._translator.text(
                        "{count} 个插件已隔离",
                        "{count} plugins isolated",
                        count=self._translator.integer(isolated),
                    ),
                    tone="warning",
                )
            )
        plugin_layout.addStretch(1)
        plugin_label = QLabel(
            self._translator.text(
                "插件通过 entry point 发现；一个插件故障不会影响应用启动。",
                "Plugins are discovered through entry points; one plugin failure cannot prevent app startup.",
            )
        )
        plugin_label.setObjectName("MutedText")
        plugin_layout.addWidget(plugin_label)
        root.addWidget(plugin_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._content = QWidget()
        self._list = QVBoxLayout(self._content)
        self._list.setContentsMargins(0, 0, 8, 0)
        self._list.setSpacing(12)
        self._list.addStretch(1)
        scroll.setWidget(self._content)
        root.addWidget(scroll, 1)
        QTimer.singleShot(0, lambda: self._start(self.refresh()))

    async def refresh(self) -> None:
        try:
            providers = await self._service.list_providers()
        except Exception:
            self._logger.exception("provider_list_failed")
            self._notify(
                self._translator.text("无法读取 Provider 列表", "Unable to load Provider list"),
                "danger",
            )
            return
        _clear_layout(self._list)
        if not providers:
            empty = EmptyState(
                "⬡",
                self._translator.text("还没有 Provider", "No providers yet"),
                self._translator.text(
                    "添加一个插件连接后即可测试状态并同步模型。",
                    "Add a plugin connection to test its health and sync models.",
                ),
                action_text=self._translator.text("添加 Provider", "Add Provider"),
            )
            empty.action_requested.connect(self._open_add)
            self._list.addWidget(empty, 1)
            return
        records = {
            record.descriptor.plugin_id: record
            for record in self._service.plugin_records()
            if record.descriptor is not None
        }
        for provider in providers:
            card = _ProviderCard(provider, records.get(provider.plugin_id), self._translator)
            card.edit_requested.connect(lambda pid=provider.id: self._open_edit(pid))
            card.test_requested.connect(
                lambda pid=provider.id: self._start(self._test_connection(pid))
            )
            card.sync_requested.connect(lambda pid=provider.id: self._start(self._sync(pid)))
            card.toggle_requested.connect(
                lambda enabled, pid=provider.id: self._start(self._toggle(pid, enabled))
            )
            card.delete_requested.connect(lambda pid=provider.id: self._confirm_delete(pid))
            self._list.addWidget(card)
        self._list.addStretch(1)

    def _open_add(self) -> None:
        dialog = ProviderDialog(
            self._service.plugin_records(), parent=self, translator=self._translator
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            command = dialog.create_command()
        except SchemaFormError as exc:
            self._notify(str(exc), "danger")
            return
        self._start(self._create(command))

    def _open_plugins(self) -> None:
        dialog = PluginManagerDialog(self._service, self, self._translator)
        dialog.exec()
        self._start(self.refresh())

    def _open_edit(self, provider_id: str) -> None:
        self._start(self._edit(provider_id))

    async def _create(self, command: CreateProvider) -> None:
        try:
            await self._service.create(command)
            await self.refresh()
            self._notify(
                self._translator.text(
                    "Provider 已保存，凭据未写入数据库",
                    "Provider saved; credentials were not written to the database",
                ),
                "success",
            )
        except Exception as exc:
            self._handle_operation_error(
                self._translator.text("保存 Provider 失败", "Unable to save Provider"),
                exc,
            )

    async def _edit(self, provider_id: str) -> None:
        providers = await self._service.list_providers()
        provider = next((item for item in providers if item.id == provider_id), None)
        if provider is None:
            self._notify(
                self._translator.text("Provider 已不存在", "Provider no longer exists"),
                "warning",
            )
            return
        dialog = ProviderDialog(
            self._service.plugin_records(),
            provider=provider,
            parent=self,
            translator=self._translator,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            await self._service.update(dialog.update_command())
            await self.refresh()
            self._notify(
                self._translator.text("Provider 设置已更新", "Provider settings updated"),
                "success",
            )
        except Exception as exc:
            self._handle_operation_error(
                self._translator.text("更新 Provider 失败", "Unable to update Provider"),
                exc,
            )

    async def _test_connection(self, provider_id: str) -> None:
        try:
            result = await self._service.test_connection(provider_id)
            await self.refresh()
            latency = f" · {result.latency_ms} ms" if result.latency_ms is not None else ""
            self._notify(
                self._translator.text(
                    "连接正常{latency}",
                    "Connection healthy{latency}",
                    latency=latency,
                ),
                "success",
            )
        except Exception as exc:
            await self.refresh()
            self._handle_operation_error(
                self._translator.text("连接测试失败", "Connection test failed"),
                exc,
            )

    async def _sync(self, provider_id: str) -> None:
        try:
            models = await self._service.sync_models(provider_id)
            self.catalog_changed.emit()
            self._notify(
                self._translator.text(
                    "已同步 {count} 个可用模型",
                    "Synced {count} available models",
                    count=self._translator.integer(len(models)),
                ),
                "success",
            )
        except Exception as exc:
            self._handle_operation_error(
                self._translator.text("模型同步失败", "Model sync failed"), exc
            )

    async def _toggle(self, provider_id: str, enabled: bool) -> None:
        try:
            await self._service.set_enabled(provider_id, enabled)
            await self.refresh()
            self._notify(
                self._translator.text("Provider 已启用", "Provider enabled")
                if enabled
                else self._translator.text("Provider 已停用", "Provider disabled"),
                "info",
            )
        except Exception as exc:
            self._handle_operation_error(
                self._translator.text("更新启用状态失败", "Unable to update enablement"),
                exc,
            )

    def _confirm_delete(self, provider_id: str) -> None:
        answer = QMessageBox.question(
            self,
            self._translator.text("删除 Provider", "Delete Provider"),
            self._translator.text(
                "确认删除这个 Provider？其关联的安全凭据也会移除。",
                "Delete this Provider? Its associated secure credentials will also be removed.",
            ),
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer is QMessageBox.StandardButton.Yes:
            self._start(self._delete(provider_id))

    async def _delete(self, provider_id: str) -> None:
        try:
            await self._service.delete(provider_id)
            await self.refresh()
            self.catalog_changed.emit()
            self._notify(self._translator.text("Provider 已删除", "Provider deleted"), "success")
        except Exception as exc:
            self._handle_operation_error(
                self._translator.text("删除 Provider 失败", "Unable to delete Provider"),
                exc,
            )

    def _handle_operation_error(self, prefix: str, error: Exception) -> None:
        self._logger.warning(
            "provider_ui_operation_failed",
            exc_info=(type(error), error, error.__traceback__),
        )
        message = str(error).strip() or type(error).__name__
        self._notify(f"{prefix}：{message}", "danger")

    def _start(self, operation: Coroutine[Any, Any, object]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            operation.close()
            return
        task = loop.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _notify(self, text: str, tone: ToastTone) -> None:
        if self._toast is not None:
            self._toast.deleteLater()
        self._toast = Toast(text, tone=tone, translator=self._translator)
        self._toast.setParent(self)
        self._position_toast()
        self._toast.show()
        self._toast.raise_()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._position_toast()

    def _position_toast(self) -> None:
        if self._toast is None:
            return
        self._toast.adjustSize()
        self._toast.move(max(16, self.width() - self._toast.width() - 24), 22)


class _ProviderCard(QFrame):
    edit_requested = Signal()
    test_requested = Signal()
    sync_requested = Signal()
    toggle_requested = Signal(bool)
    delete_requested = Signal()

    def __init__(
        self,
        provider: Provider,
        record: PluginRecord | None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__()
        self._translator = translator or Translator()
        self.setObjectName("ProviderCard")
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        header = QHBoxLayout()
        mark = QLabel((provider.name[:2] or "P").upper())
        mark.setObjectName("ProviderMark")
        mark.setFixedSize(42, 42)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        names = QVBoxLayout()
        name = QLabel(provider.name)
        name.setObjectName("CardTitle")
        plugin_name = record.descriptor.name if record and record.descriptor else provider.plugin_id
        plugin = QLabel(f"{plugin_name}  ·  {provider.plugin_version}")
        plugin.setObjectName("MutedText")
        names.addWidget(name)
        names.addWidget(plugin)
        header.addWidget(mark)
        header.addLayout(names)
        header.addStretch(1)
        tone, label = _health_badge(provider.health_status, self._translator)
        header.addWidget(Badge(label, tone=tone))
        header.addWidget(
            Badge(
                self._translator.text("已启用", "Enabled")
                if provider.enabled
                else self._translator.text("已停用", "Disabled"),
                tone="info" if provider.enabled else "neutral",
            )
        )
        root.addLayout(header)

        details = QHBoxLayout()
        endpoint = QLabel(
            self._translator.text(
                "地址  {endpoint}",
                "Endpoint  {endpoint}",
                endpoint=provider.endpoint
                or self._translator.text("插件默认 / 本地", "Plugin default / local"),
            )
        )
        endpoint.setObjectName("BodyText")
        details.addWidget(endpoint)
        if record and record.descriptor:
            operations = QLabel(
                self._translator.text("能力  ", "Capabilities  ")
                + "  ·  ".join(sorted(record.descriptor.operations))
            )
            operations.setObjectName("BodyText")
            details.addWidget(operations)
        details.addStretch(1)
        root.addLayout(details)

        actions = QHBoxLayout()
        edit = Button(self._translator.text("编辑", "Edit"), variant="ghost")
        test = Button(self._translator.text("测试连接", "Test connection"), variant="ghost")
        sync = Button(self._translator.text("同步模型", "Sync models"))
        toggle = Button(
            self._translator.text("停用", "Disable")
            if provider.enabled
            else self._translator.text("启用", "Enable"),
            variant="ghost",
        )
        delete = Button(self._translator.text("删除", "Delete"), variant="danger")
        edit.clicked.connect(self.edit_requested)
        test.clicked.connect(self.test_requested)
        sync.clicked.connect(self.sync_requested)
        toggle.clicked.connect(lambda: self.toggle_requested.emit(not provider.enabled))
        delete.clicked.connect(self.delete_requested)
        actions.addWidget(edit)
        actions.addWidget(test)
        actions.addWidget(sync)
        actions.addStretch(1)
        actions.addWidget(toggle)
        actions.addWidget(delete)
        root.addLayout(actions)


def _health_badge(
    status: ProviderHealth, translator: Translator | None = None
) -> tuple[BadgeTone, str]:
    translator = translator or Translator()
    return cast(
        tuple[BadgeTone, str],
        {
            ProviderHealth.UNKNOWN: ("neutral", translator.text("未测试", "Not tested")),
            ProviderHealth.HEALTHY: (
                "success",
                translator.text("连接正常", "Healthy"),
            ),
            ProviderHealth.DEGRADED: (
                "warning",
                translator.text("连接降级", "Degraded"),
            ),
            ProviderHealth.UNAVAILABLE: (
                "danger",
                translator.text("连接不可用", "Unavailable"),
            ),
        }[status],
    )


def _plugin_state(
    state: PluginLoadState, translator: Translator | None = None
) -> tuple[BadgeTone, str]:
    translator = translator or Translator()
    return cast(
        tuple[BadgeTone, str],
        {
            PluginLoadState.READY: ("success", translator.text("可用", "Available")),
            PluginLoadState.DISABLED: (
                "neutral",
                translator.text("已停用", "Disabled"),
            ),
            PluginLoadState.INCOMPATIBLE: (
                "warning",
                translator.text("不兼容", "Incompatible"),
            ),
            PluginLoadState.LOAD_FAILED: (
                "danger",
                translator.text("加载失败", "Load failed"),
            ),
            PluginLoadState.COLLISION: (
                "danger",
                translator.text("ID 冲突", "ID collision"),
            ),
        }[state],
    )


def _field(label: str, widget: QWidget) -> QWidget:
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    title = QLabel(label)
    title.setObjectName("FormLabel")
    layout.addWidget(title)
    layout.addWidget(widget)
    return container


def _section(title: str) -> QFrame:
    section = QFrame()
    section.setObjectName("FormSection")
    layout = QVBoxLayout(section)
    layout.setContentsMargins(15, 14, 15, 15)
    layout.setSpacing(12)
    label = QLabel(title)
    label.setObjectName("SectionTitle")
    layout.addWidget(label)
    return section


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
