"""Async use cases for backup, restore preview, and diagnostics."""

from __future__ import annotations

import asyncio
from pathlib import Path

from astraweft.ports.maintenance import (
    BackupResult,
    DatabaseHealth,
    DataMigrationPreview,
    DataMigrationResult,
    DiagnosticExport,
    MaintenancePort,
    PendingRestore,
    RestorePreview,
)


class MaintenanceService:
    """Keep blocking filesystem and SQLite maintenance away from the UI loop."""

    def __init__(self, adapter: MaintenancePort) -> None:
        self._adapter = adapter

    async def check_database(self) -> DatabaseHealth:
        return await asyncio.to_thread(self._adapter.check_database)

    async def create_backup(self, *, reason: str = "manual") -> BackupResult:
        return await asyncio.to_thread(self._adapter.create_backup, reason=reason)

    async def inspect_restore(self, source_path: Path) -> RestorePreview:
        return await asyncio.to_thread(self._adapter.inspect_restore, source_path)

    async def stage_restore(self, source_path: Path) -> PendingRestore:
        return await asyncio.to_thread(self._adapter.stage_restore, source_path)

    async def export_diagnostics(self) -> DiagnosticExport:
        return await asyncio.to_thread(self._adapter.export_diagnostics)

    async def inspect_data_migration(self, target_root: Path) -> DataMigrationPreview:
        return await asyncio.to_thread(self._adapter.inspect_data_migration, target_root)

    async def stage_data_migration(self, target_root: Path) -> DataMigrationResult:
        return await asyncio.to_thread(self._adapter.stage_data_migration, target_root)


__all__ = ["MaintenanceService"]
