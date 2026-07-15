"""GUI tests for preview-first local data maintenance."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QMessageBox
from pytestqt.qtbot import QtBot

from astraweft.application.maintenance import MaintenanceService
from astraweft.bootstrap.container import build_app_context
from astraweft.infrastructure.config.settings_store import SettingsStore
from astraweft.infrastructure.secrets.store import SessionSecretStore
from astraweft.ports.maintenance import (
    DataMigrationPreview,
    RestorePreview,
)
from astraweft.presentation.pages.settings import (
    SettingsPage,
    _open_folder,
    _restore_summary,
    _size,
)


@pytest.mark.gui
@pytest.mark.asyncio
async def test_settings_page_runs_real_backup_diagnostics_and_restore_preview(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    page = SettingsPage(
        context.maintenance_service,
        context.paths.data_dir,
        context.settings_service,
        context.settings,
    )
    qtbot.addWidget(page)
    page.show()
    information: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, _title, message: information.append(str(message)),
    )
    try:
        await page._refresh_health()
        assert "版本 20260715_0007" in page._health.text()
        assert page._health_badge.text() == "完整性正常"

        notifications: list[bool] = []
        page.notification_preference_changed.connect(notifications.append)
        page._language.setCurrentIndex(page._language.findData("en_US"))
        page._system_notifications.setChecked(False)
        page._reduce_motion.setChecked(True)
        await page._save_user_preferences()
        persisted = SettingsStore(context.paths.settings_path).load_persisted()
        assert persisted.language == "en_US"
        assert persisted.system_notifications is False
        assert persisted.reduce_motion is True
        assert notifications == [False]
        assert "重启" in page._preference_status.text()

        await page._create_backup()
        assert "完整性已验证" in page._backup_status.text()
        backup = max(context.paths.backup_dir.glob("*.db"))

        context.log_path.write_text(
            '{"authorization":"Bearer hidden-value","safe":"visible"}\n',
            encoding="utf-8",
        )
        await page._export_diagnostics()
        assert "个脱敏文件" in page._diagnostic_status.text()
        assert tuple(context.paths.diagnostic_dir.glob("*.zip"))

        await page._preview_restore(backup)
        assert context.paths.restore_marker_path.exists()
        assert "重启" in page._backup_status.text()
        assert information

        migration_target = tmp_path / "migrated-data"
        await page._preview_migration(migration_target)
        assert migration_target.joinpath("migration.complete.json").exists()
        assert "已校验" in page._migration_status.text()

        monkeypatch.setattr(
            QDesktopServices,
            "openUrl",
            lambda _url: True,
        )
        new_folder = context.paths.backup_dir / "new"
        _open_folder(new_folder)
        assert new_folder.is_dir()
        assert _size(12) == "12 B"
        assert _size(2048) == "2.0 KB"
        assert _size(2 * 1024 * 1024) == "2.0 MB"

        preview = await context.maintenance_service.inspect_restore(backup)
        assert "完整性：通过" in _restore_summary(preview)
    finally:
        pending = tuple(page._tasks)
        if pending:
            await asyncio.gather(*pending)
        await context.close()


class _FailureService:
    def __init__(self, preview: RestorePreview) -> None:
        self.preview = preview
        self.health_fails = True
        self.inspect_fails = True
        self.stage_fails = True
        self.migration_preview = DataMigrationPreview(
            source_data_path=preview.health.database_path.parent,
            target_root=preview.health.database_path.parent / "target",
            required_bytes=100,
            available_bytes=50,
            file_count=2,
            conflicts=(),
        )

    async def check_database(self) -> object:
        if self.health_fails:
            raise OSError("health unavailable")
        return self.preview.health

    async def create_backup(self) -> object:
        raise OSError("backup unavailable")

    async def inspect_restore(self, _path: Path) -> RestorePreview:
        if self.inspect_fails:
            raise OSError("not a backup")
        return self.preview

    async def stage_restore(self, _path: Path) -> object:
        if self.stage_fails:
            raise OSError("disk full")
        return object()

    async def export_diagnostics(self) -> object:
        raise OSError("export unavailable")

    async def inspect_data_migration(self, _target: Path) -> DataMigrationPreview:
        return self.migration_preview

    async def stage_data_migration(self, _target: Path) -> object:
        raise OSError("copy interrupted")


@pytest.mark.gui
@pytest.mark.asyncio
async def test_settings_page_failure_and_cancel_paths_preserve_current_data(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await build_app_context(tmp_path, secret_store_override=SessionSecretStore())
    health = await context.maintenance_service.check_database()
    await context.close()
    invalid = RestorePreview(
        source_path=tmp_path / "candidate.db",
        size_bytes=1024,
        sha256="a" * 64,
        health=health,
        compatible=False,
        warnings=("备份由更新版本的 AstraWeft 创建",),
    )
    fake = _FailureService(invalid)
    page = SettingsPage(cast(MaintenanceService, fake), tmp_path)
    qtbot.addWidget(page)
    warnings: list[str] = []
    critical: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(str(message)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda _parent, _title, message: critical.append(str(message)),
    )

    await page._refresh_health()
    await page._create_backup()
    await page._export_diagnostics()
    assert page._health_badge.text() == "需要处理"
    assert "失败" in page._backup_status.text()
    assert "失败" in page._diagnostic_status.text()

    await page._preview_restore(tmp_path / "bad.db")
    assert warnings and "无法验证" in warnings[-1]

    fake.inspect_fails = False
    await page._preview_restore(invalid.source_path)
    assert "注意" in warnings[-1]

    valid = RestorePreview(
        source_path=invalid.source_path,
        size_bytes=health.size_bytes,
        sha256="b" * 64,
        health=health,
        compatible=True,
        warnings=(),
    )
    fake.preview = valid
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.No,
    )
    await page._preview_restore(valid.source_path)
    assert not critical

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    await page._preview_restore(valid.source_path)
    assert critical and "当前数据未更改" in critical[-1]

    chosen: list[object] = []
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args: (str(valid.source_path), "SQLite"),
    )

    def capture_operation(operation: object) -> None:
        chosen.append(operation)
        operation.close()  # type: ignore[attr-defined]

    monkeypatch.setattr(page, "_start", capture_operation)
    page._choose_restore()
    assert chosen
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: ("", ""))
    page._choose_restore()

    fake.health_fails = False
    await page._refresh_health()
    assert page._health_badge.text() == "完整性正常"

    await page._preview_migration(fake.migration_preview.target_root)
    assert "可用空间不足" in warnings[-1]
    fake.migration_preview = DataMigrationPreview(
        source_data_path=health.database_path.parent,
        target_root=tmp_path / "stage-fails",
        required_bytes=10,
        available_bytes=100,
        file_count=1,
        conflicts=(),
    )
    await page._preview_migration(fake.migration_preview.target_root)
    assert "迁移未发布" in page._migration_status.text()

    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *_args: str(tmp_path),
    )
    page._choose_migration_target()
    assert len(chosen) == 2
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: "")
    page._choose_migration_target()
