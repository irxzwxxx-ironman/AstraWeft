"""Exercise a packaged AstraWeft desktop application in an isolated directory."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = PROJECT_ROOT / "dist" / "desktop"


def packaged_executable(dist_root: Path) -> Path:
    """Resolve the platform-specific executable produced by the shared spec."""
    candidates = (
        dist_root / "AstraWeft.app" / "Contents" / "MacOS" / "AstraWeft",
        dist_root / "AstraWeft" / "AstraWeft.exe",
        dist_root / "AstraWeft" / "AstraWeft",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    rendered = "\n".join(f"- {candidate}" for candidate in candidates)
    raise FileNotFoundError(f"packaged executable was not found:\n{rendered}")


def database_revision(database_path: Path) -> str | None:
    """Read the migration revision without importing application code."""
    if not database_path.is_file():
        return None
    with sqlite3.connect(database_path) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
    return None if row is None else str(row[0])


def _run_checked(
    command: list[str], environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=45,
            env=environment,
        )
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or "no process output").strip()
        raise RuntimeError(f"packaged command failed ({error.returncode}): {details}") from error
    except subprocess.TimeoutExpired as error:
        stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else error.stderr
        stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else error.stdout
        details = (stderr or stdout or "no process output").strip()
        raise RuntimeError(f"packaged command timed out: {details}") from error


def run_smoke(dist_root: Path, expected_revision: str | None = None) -> dict[str, Any]:
    """Check version output, first launch, shutdown, and database initialization."""
    executable = packaged_executable(dist_root.resolve())
    environment = os.environ.copy()
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    version_result = _run_checked([str(executable), "--version"], environment)
    with tempfile.TemporaryDirectory(prefix="astraweft-package-smoke-") as temporary:
        data_root = Path(temporary)
        try:
            launch_result = _run_checked(
                [
                    str(executable),
                    "--data-dir",
                    str(data_root),
                    "--quit-after-ms",
                    "1200",
                ],
                environment,
            )
        except RuntimeError as error:
            log_path = data_root / "logs" / "astraweft.jsonl"
            log = log_path.read_text(encoding="utf-8")[-20_000:] if log_path.is_file() else ""
            suffix = f"\napplication log:\n{log}" if log else ""
            raise RuntimeError(f"{error}{suffix}") from error
        database = data_root / "data" / "astraweft.db"
        revision = database_revision(database)
        if revision is None:
            raise RuntimeError(f"packaged launch did not initialize {database}")
        if expected_revision is not None and revision != expected_revision:
            raise RuntimeError(f"expected revision {expected_revision}, got {revision}")
        return {
            "status": "passed",
            "platform": platform.system().lower(),
            "architecture": platform.machine().lower(),
            "executable": str(executable),
            "version_output": version_result.stdout.strip() or version_result.stderr.strip(),
            "database_revision": revision,
            "launch_stdout": launch_result.stdout.strip(),
            "launch_stderr": launch_result.stderr.strip(),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--expected-revision")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_smoke(args.dist_dir, args.expected_revision)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
