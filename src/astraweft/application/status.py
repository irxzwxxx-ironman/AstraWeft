"""Presentation-safe process status DTOs."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApplicationStatus:
    """Non-sensitive state shown by the Phase 1 application shell."""

    database_online: bool
    credential_store_persistent: bool
    data_directory: str
    log_path: str
    version: str
    cache_directory: str | None = None
