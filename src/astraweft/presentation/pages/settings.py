"""Local data safety and diagnostics settings page."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, Literal

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from astraweft.application.maintenance import MaintenanceService
from astraweft.application.settings import AppSettings, SettingsService
from astraweft.ports.maintenance import RestorePreview
from astraweft.presentation.i18n import Translator
from astraweft.presentation.widgets.controls import Badge, BadgeTone, Button, SelectInput


class SettingsPage(QWidget):
    """Expose preview-first, recoverable local maintenance operations."""

    notification_preference_changed = Signal(bool)

    def __init__(
        self,
        service: MaintenanceService,
        data_root: Path,
        settings_service: SettingsService | None = None,
        settings: AppSettings | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("SettingsPage")
        self._service = service
        self._data_root = data_root
        self._settings_service = settings_service
        self._translator = translator or Translator()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._logger = logging.getLogger("astraweft.presentation.settings")
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 27, 30, 24)
        root.setSpacing(18)

        title = QLabel(self._translator.text("设置与本地数据", "Settings and Local Data"))
        title.setObjectName("ContentTitle")
        subtitle = QLabel(
            self._translator.text(
                "备份、恢复和诊断都在本机完成；恢复只会在重启后应用。",
                "Backups, restores, and diagnostics stay local; restores apply only after restart.",
            )
        )
        subtitle.setObjectName("BodyText")
        root.addWidget(title)
        root.addWidget(subtitle)

        if self._settings_service is not None and settings is not None:
            preference_card, preference_layout = _card(
                self._translator.text("界面与通知", "Interface and notifications")
            )
            preference_row = QHBoxLayout()
            self._language = SelectInput(self._translator.text("界面语言", "Interface language"))
            self._language.addItem("中文 (简体)", "zh_CN")
            self._language.addItem("English (US)", "en_US")
            self._language.setCurrentIndex(max(0, self._language.findData(settings.language)))
            self._system_notifications = QCheckBox(
                self._translator.text("任务完成时显示系统通知", "Notify when tasks finish")
            )
            self._system_notifications.setAccessibleName(
                self._translator.text("系统任务通知", "System task notifications")
            )
            self._system_notifications.setChecked(settings.system_notifications)
            self._reduce_motion = QCheckBox(
                self._translator.text("减少界面动态效果", "Reduce interface motion")
            )
            self._reduce_motion.setAccessibleName(
                self._translator.text("减少动态效果", "Reduce motion")
            )
            self._reduce_motion.setChecked(settings.reduce_motion)
            save_preferences = Button(
                self._translator.text("保存偏好", "Save preferences"),
                variant="ghost",
            )
            save_preferences.clicked.connect(lambda: self._start(self._save_user_preferences()))
            preference_row.addWidget(self._language)
            preference_row.addWidget(self._system_notifications)
            preference_row.addWidget(self._reduce_motion)
            preference_row.addWidget(save_preferences)
            preference_row.addStretch(1)
            preference_layout.addLayout(preference_row)
            self._preference_status = QLabel(
                self._translator.text(
                    "语言和动态效果更改在重启后完全生效",
                    "Language and motion changes fully apply after restart",
                )
            )
            self._preference_status.setObjectName("BodyText")
            preference_layout.addWidget(self._preference_status)
            root.addWidget(preference_card)

        database_card, database_layout = _card("数据库健康")
        database_header = QHBoxLayout()
        self._health = QLabel("正在检查数据库…")
        self._health.setObjectName("BodyText")
        self._health_badge = Badge("检查中", tone="neutral")
        database_header.addWidget(self._health)
        database_header.addStretch(1)
        database_header.addWidget(self._health_badge)
        database_layout.addLayout(database_header)
        root.addWidget(database_card)

        backup_card, backup_layout = _card("备份与恢复")
        backup_hint = QLabel(
            "备份使用 SQLite 在线一致性快照。恢复前会预览影响，并在下次启动替换前自动保留当前数据。"
        )
        backup_hint.setObjectName("BodyText")
        backup_hint.setWordWrap(True)
        backup_layout.addWidget(backup_hint)
        backup_actions = QHBoxLayout()
        backup = Button("立即备份")
        backup.clicked.connect(lambda: self._start(self._create_backup()))
        restore = Button("从备份恢复", variant="ghost")
        restore.clicked.connect(self._choose_restore)
        open_backups = Button("打开备份目录", variant="ghost")
        open_backups.clicked.connect(lambda: _open_folder(self._data_root / "backups"))
        backup_actions.addWidget(backup)
        backup_actions.addWidget(restore)
        backup_actions.addWidget(open_backups)
        backup_actions.addStretch(1)
        backup_layout.addLayout(backup_actions)
        self._backup_status = QLabel("默认保留最近 7 份备份")
        self._backup_status.setObjectName("BodyText")
        backup_layout.addWidget(self._backup_status)
        root.addWidget(backup_card)

        migration_card, migration_layout = _card("数据目录迁移")
        migration_hint = QLabel(
            "先在目标位置生成经哈希校验的完整副本，成功后才发布。当前目录始终保留，不会自动删除。"
        )
        migration_hint.setObjectName("BodyText")
        migration_hint.setWordWrap(True)
        migration_layout.addWidget(migration_hint)
        migration_actions = QHBoxLayout()
        migrate = Button("准备新数据目录")
        migrate.clicked.connect(self._choose_migration_target)
        migration_actions.addWidget(migrate)
        migration_actions.addStretch(1)
        migration_layout.addLayout(migration_actions)
        self._migration_status = QLabel("尚未准备迁移")
        self._migration_status.setObjectName("BodyText")
        migration_layout.addWidget(self._migration_status)
        root.addWidget(migration_card)

        diagnostic_card, diagnostic_layout = _card("诊断包")
        diagnostic_hint = QLabel(
            "导出包含系统版本、数据库计数和二次脱敏日志；不包含密钥、请求正文、产物或数据库内容。"
        )
        diagnostic_hint.setObjectName("BodyText")
        diagnostic_hint.setWordWrap(True)
        diagnostic_layout.addWidget(diagnostic_hint)
        diagnostic_actions = QHBoxLayout()
        export = Button("导出脱敏诊断包")
        export.clicked.connect(lambda: self._start(self._export_diagnostics()))
        open_diagnostics = Button("打开诊断目录", variant="ghost")
        open_diagnostics.clicked.connect(lambda: _open_folder(self._data_root / "diagnostics"))
        diagnostic_actions.addWidget(export)
        diagnostic_actions.addWidget(open_diagnostics)
        diagnostic_actions.addStretch(1)
        diagnostic_layout.addLayout(diagnostic_actions)
        self._diagnostic_status = QLabel("尚未导出诊断包")
        self._diagnostic_status.setObjectName("BodyText")
        diagnostic_layout.addWidget(self._diagnostic_status)
        root.addWidget(diagnostic_card)
        root.addStretch(1)
        QTimer.singleShot(0, lambda: self._start(self._refresh_health()))

    async def _save_user_preferences(self) -> None:
        if self._settings_service is None:
            return
        selected = self._language.currentData()
        language: Literal["zh_CN", "en_US"] = "en_US" if selected == "en_US" else "zh_CN"
        try:
            updated = await self._settings_service.update_user_preferences(
                language=language,
                system_notifications=self._system_notifications.isChecked(),
                reduce_motion=self._reduce_motion.isChecked(),
            )
        except Exception:
            self._logger.exception("user_preferences_save_failed")
            self._preference_status.setText(
                self._translator.text("偏好保存失败", "Could not save preferences")
            )
            return
        self.notification_preference_changed.emit(updated.system_notifications)
        self._preference_status.setText(
            self._translator.text(
                "偏好已保存；系统通知已立即更新，其他更改重启后生效",
                "Preferences saved; notifications updated now, other changes apply after restart",
            )
        )

    async def _refresh_health(self) -> None:
        try:
            health = await self._service.check_database()
        except Exception as exc:
            self._logger.exception("database_health_check_failed")
            self._health.setText(f"无法完成检查：{type(exc).__name__}")
            self._set_health_badge("需要处理", "danger")
            return
        total_rows = sum(count for _table, count in health.table_counts)
        self._health.setText(
            f"{len(health.table_counts)} 张表 · {total_rows:,} 行 · "
            f"{_size(health.size_bytes)} · 版本 {health.revision or '未知'}"
        )
        self._set_health_badge(
            "完整性正常" if health.healthy else "需要处理",
            "success" if health.healthy else "danger",
        )

    async def _create_backup(self) -> None:
        self._backup_status.setText("正在创建一致性备份…")
        try:
            result = await self._service.create_backup()
        except Exception as exc:
            self._logger.exception("backup_create_failed")
            self._backup_status.setText(f"备份失败：{type(exc).__name__}")
            return
        self._backup_status.setText(
            f"已创建 {result.path.name} · {_size(result.size_bytes)} · 完整性已验证"
        )

    def _choose_restore(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "选择 AstraWeft 备份",
            str(self._data_root / "backups"),
            "SQLite 备份 (*.db);;所有文件 (*)",
        )
        if path:
            self._start(self._preview_restore(Path(path)))

    async def _preview_restore(self, path: Path) -> None:
        try:
            preview = await self._service.inspect_restore(path)
        except Exception as exc:
            self._logger.exception("restore_preview_failed")
            QMessageBox.warning(self, "备份不可用", f"无法验证该备份。\n\n{exc}")
            return
        if not preview.can_restore:
            QMessageBox.warning(self, "备份不可用", _restore_summary(preview))
            return
        answer = QMessageBox.question(
            self,
            "确认暂存恢复",
            _restore_summary(preview) + "\n\n确认后仅暂存备份。当前数据保持不变，重启时才会替换，"
            "并先自动创建安全备份。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        try:
            await self._service.stage_restore(path)
        except Exception as exc:
            self._logger.exception("restore_stage_failed")
            QMessageBox.critical(self, "暂存失败", f"当前数据未更改。\n\n{exc}")
            return
        self._backup_status.setText("恢复已暂存；完全退出并重启 AstraWeft 后生效")
        QMessageBox.information(
            self,
            "恢复已就绪",
            "当前数据未变更。完全退出 AstraWeft 并重新启动后将安全应用恢复。",
        )

    async def _export_diagnostics(self) -> None:
        self._diagnostic_status.setText("正在导出并二次脱敏…")
        try:
            result = await self._service.export_diagnostics()
        except Exception as exc:
            self._logger.exception("diagnostic_export_failed")
            self._diagnostic_status.setText(f"导出失败：{type(exc).__name__}")
            return
        self._diagnostic_status.setText(
            f"已导出 {result.path.name} · {_size(result.size_bytes)} · "
            f"{len(result.included_files)} 个脱敏文件"
        )

    def _choose_migration_target(self) -> None:
        parent = QFileDialog.getExistingDirectory(
            self,
            "选择新数据目录的上级位置",
            str(self._data_root.parent),
        )
        if not parent:
            return
        target = Path(parent) / "AstraWeft-Data"
        self._start(self._preview_migration(target))

    async def _preview_migration(self, target: Path) -> None:
        try:
            preview = await self._service.inspect_data_migration(target)
        except Exception as exc:
            self._logger.exception("data_migration_preview_failed")
            QMessageBox.warning(self, "无法评估迁移", str(exc))
            return
        conflicts = "\n".join(f"• {item}" for item in preview.conflicts)
        summary = (
            f"目标：{preview.target_root}\n"
            f"内容：{preview.file_count} 个文件，{_size(preview.required_bytes)}\n"
            f"可用空间：{_size(preview.available_bytes)}"
        )
        if not preview.can_stage:
            detail = conflicts or "可用空间不足"
            QMessageBox.warning(self, "目标不可用", f"{summary}\n\n{detail}")
            return
        answer = QMessageBox.question(
            self,
            "确认准备迁移",
            summary + "\n\n将创建完整的可启动副本。当前运行目录和数据不会改变。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        self._migration_status.setText("正在复制并逐文件校验…")
        try:
            result = await self._service.stage_data_migration(target)
        except Exception as exc:
            self._logger.exception("data_migration_stage_failed")
            self._migration_status.setText("迁移未发布；当前目录保持不变")
            QMessageBox.critical(self, "迁移失败", str(exc))
            return
        self._migration_status.setText(
            f"已校验 {result.file_count} 个文件 · {_size(result.total_bytes)} · "
            f"新目录：{result.target_root}"
        )
        QMessageBox.information(
            self,
            "新数据目录已准备",
            "当前目录仍在使用且未被删除。下次启动可选择新目录；确认无误后再手动清理旧目录。",
        )

    def _set_health_badge(self, text: str, tone: BadgeTone) -> None:
        self._health_badge.setText(text)
        self._health_badge.setAccessibleName(text)
        self._health_badge.setProperty("tone", tone)
        self._health_badge.style().unpolish(self._health_badge)
        self._health_badge.style().polish(self._health_badge)

    def _start(self, operation: Coroutine[Any, Any, object]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            operation.close()
            return
        task = loop.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("SurfaceCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    label = QLabel(title)
    label.setObjectName("SectionTitle")
    layout.addWidget(label)
    return card, layout


def _restore_summary(preview: RestorePreview) -> str:
    row_count = sum(count for _table, count in preview.health.table_counts)
    warnings = "\n".join(f"• {item}" for item in preview.warnings)
    text = (
        f"文件：{preview.source_path.name}\n"
        f"大小：{_size(preview.size_bytes)}\n"
        f"数据：{len(preview.health.table_counts)} 张表，{row_count:,} 行\n"
        f"版本：{preview.health.revision or '未知'}\n"
        f"完整性：{'通过' if preview.health.healthy else '未通过'}"
    )
    return text if not warnings else f"{text}\n\n注意：\n{warnings}"


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def _size(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"
