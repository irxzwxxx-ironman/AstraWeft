"""Application-facing contracts for local data maintenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    """Content-free database integrity summary safe for presentation."""

    database_path: Path
    size_bytes: int
    revision: str | None
    integrity: str
    foreign_key_issues: int
    table_counts: tuple[tuple[str, int], ...]

    @property
    def healthy(self) -> bool:
        return self.integrity == "ok" and self.foreign_key_issues == 0


@dataclass(frozen=True, slots=True)
class BackupResult:
    path: Path
    created_at: datetime
    size_bytes: int
    sha256: str
    health: DatabaseHealth


@dataclass(frozen=True, slots=True)
class RestorePreview:
    source_path: Path
    size_bytes: int
    sha256: str
    health: DatabaseHealth
    compatible: bool
    warnings: tuple[str, ...]

    @property
    def can_restore(self) -> bool:
        return self.health.healthy and self.compatible


@dataclass(frozen=True, slots=True)
class PendingRestore:
    source_path: Path
    staged_path: Path
    marker_path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class DiagnosticExport:
    path: Path
    created_at: datetime
    size_bytes: int
    included_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DataMigrationPreview:
    source_data_path: Path
    target_root: Path
    required_bytes: int
    available_bytes: int
    file_count: int
    conflicts: tuple[str, ...]

    @property
    def can_stage(self) -> bool:
        return not self.conflicts and self.available_bytes >= self.required_bytes


@dataclass(frozen=True, slots=True)
class DataMigrationResult:
    target_root: Path
    manifest_path: Path
    total_bytes: int
    file_count: int


class MaintenancePort(Protocol):
    """Synchronous infrastructure boundary; the application offloads its calls."""

    def check_database(self, path: Path | None = None) -> DatabaseHealth: ...

    def create_backup(self, *, reason: str = "manual") -> BackupResult: ...

    def inspect_restore(self, source_path: Path) -> RestorePreview: ...

    def stage_restore(self, source_path: Path) -> PendingRestore: ...

    def export_diagnostics(self) -> DiagnosticExport: ...

    def inspect_data_migration(self, target_root: Path) -> DataMigrationPreview: ...

    def stage_data_migration(self, target_root: Path) -> DataMigrationResult: ...
