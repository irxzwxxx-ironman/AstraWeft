"""Cross-platform application path resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import PlatformDirs


@dataclass(frozen=True, slots=True)
class AppPaths:
    """All writable paths used by an AstraWeft process."""

    config_dir: Path
    data_dir: Path
    cache_dir: Path
    log_dir: Path
    artifact_dir: Path
    backup_dir: Path
    trash_dir: Path
    diagnostic_dir: Path
    settings_path: Path
    database_path: Path
    restore_marker_path: Path
    pending_restore_path: Path

    def ensure(self) -> None:
        """Create writable directories without touching configuration files."""
        for directory in (
            self.config_dir,
            self.data_dir,
            self.cache_dir,
            self.log_dir,
            self.artifact_dir,
            self.backup_dir,
            self.trash_dir,
            self.diagnostic_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def resolve_app_paths(override_root: Path | None = None) -> AppPaths:
    """Resolve platform-native paths or an isolated development root."""
    if override_root is not None:
        root = override_root.expanduser().resolve()
        config_dir = root / "config"
        data_dir = root / "data"
        cache_dir = root / "cache"
        log_dir = root / "logs"
    else:
        dirs = PlatformDirs(appname="AstraWeft", appauthor="AstraWeft", roaming=False)
        config_dir = Path(dirs.user_config_path)
        data_dir = Path(dirs.user_data_path)
        cache_dir = Path(dirs.user_cache_path)
        log_dir = Path(dirs.user_log_path)

    return AppPaths(
        config_dir=config_dir,
        data_dir=data_dir,
        cache_dir=cache_dir,
        log_dir=log_dir,
        artifact_dir=data_dir / "artifacts",
        backup_dir=data_dir / "backups",
        trash_dir=data_dir / "trash",
        diagnostic_dir=data_dir / "diagnostics",
        settings_path=config_dir / "settings.json",
        database_path=data_dir / "astraweft.db",
        restore_marker_path=data_dir / "restore.pending.json",
        pending_restore_path=data_dir / "restore.pending.db",
    )
