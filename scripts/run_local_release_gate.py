"""Run the complete local desktop release gate without relying on Git state."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PHASE_ROOT = PROJECT_ROOT / "build" / "phase8"
DEFAULT_DIST = PROJECT_ROOT / "dist" / "desktop"
PROJECTS = (
    PROJECT_ROOT / "packages" / "provider-sdk",
    PROJECT_ROOT / "plugins" / "mock",
    PROJECT_ROOT / "plugins" / "openai",
    PROJECT_ROOT / "plugins" / "runway",
    PROJECT_ROOT,
)
EXPECTED_REVISION = "20260715_0007"


def _tool(name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    candidate = Path(sys.executable).with_name(f"{name}{suffix}")
    if not candidate.is_file():
        raise FileNotFoundError(f"required release tool is not installed: {candidate}")
    return candidate


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run(command: list[str]) -> None:
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)  # noqa: S603


def _fresh_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _build_wheels(wheel_dir: Path) -> list[Path]:
    _fresh_directory(wheel_dir)
    for project in PROJECTS:
        _run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(wheel_dir),
                str(project),
            ]
        )
    wheels = sorted(wheel_dir.glob("*.whl"))
    if len(wheels) != len(PROJECTS):
        raise RuntimeError(f"expected {len(PROJECTS)} wheels, found {len(wheels)}")
    _run([str(_tool("twine")), "check", *[str(path) for path in wheels]])
    _run([str(_tool("check-wheel-contents")), *[str(path) for path in wheels]])
    return wheels


def _install_runtime(runtime_root: Path, wheels: list[Path]) -> Path:
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    _run([sys.executable, "-m", "venv", str(runtime_root)])
    python = _venv_python(runtime_root)
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--timeout",
            "60",
            "--retries",
            "2",
            *[str(path) for path in wheels],
        ]
    )
    _run([str(python), "-m", "pip", "uninstall", "-y", "pip"])
    return python


def run_gate(phase_root: Path, dist_root: Path) -> dict[str, Any]:
    phase_root = phase_root.resolve()
    dist_root = dist_root.resolve()
    started_at = datetime.now(UTC)
    wheels = _build_wheels(phase_root / "wheels")
    runtime_python = _install_runtime(phase_root / "runtime-venv", wheels)

    _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "build_desktop.py"),
            "--dist-dir",
            str(dist_root),
            "--work-dir",
            str(PROJECT_ROOT / "build" / "pyinstaller"),
        ]
    )
    _fresh_directory(phase_root / "release-artifacts")
    _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_release_manifest.py"),
            "--dist-dir",
            str(dist_root),
            "--output",
            str(phase_root / "manifest-verification.json"),
            "--archive-dir",
            str(phase_root / "release-artifacts"),
        ]
    )
    _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "smoke_desktop.py"),
            "--dist-dir",
            str(dist_root),
            "--expected-revision",
            EXPECTED_REVISION,
            "--output",
            str(phase_root / "package-smoke.json"),
        ]
    )
    _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "smoke_upgrade.py"),
            "--dist-dir",
            str(dist_root),
            "--output",
            str(phase_root / "upgrade-smoke.json"),
        ]
    )
    _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "generate_release_evidence.py"),
            "--python",
            str(runtime_python),
            "--output-dir",
            str(phase_root / "release-evidence"),
        ]
    )

    report = {
        "status": "passed",
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "platform": platform.system().lower(),
        "architecture": platform.machine().lower(),
        "python": platform.python_version(),
        "expected_database_revision": EXPECTED_REVISION,
        "wheel_count": len(wheels),
        "desktop_dist": str(dist_root),
        "evidence": {
            "manifest": str(dist_root / "release-manifest.json"),
            "manifest_verification": str(phase_root / "manifest-verification.json"),
            "release_artifacts": str(phase_root / "release-artifacts"),
            "package_smoke": str(phase_root / "package-smoke.json"),
            "upgrade_smoke": str(phase_root / "upgrade-smoke.json"),
            "release_evidence": str(phase_root / "release-evidence" / "summary.json"),
        },
    }
    summary = phase_root / "local-release-gate.json"
    summary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase-dir", type=Path, default=DEFAULT_PHASE_ROOT)
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST)
    args = parser.parse_args()
    report = run_gate(args.phase_dir, args.dist_dir)
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
