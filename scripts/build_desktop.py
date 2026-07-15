"""Build a local AstraWeft desktop bundle and its integrity manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = PROJECT_ROOT / "dist" / "desktop"
DEFAULT_WORK = PROJECT_ROOT / "build" / "pyinstaller"
MANIFEST_NAME = "release-manifest.json"


def sha256_file(path: Path) -> str:
    """Return a lowercase SHA-256 digest for one file."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_files(root: Path) -> list[dict[str, Any]]:
    """Describe every release file except the self-referential manifest."""
    result: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in {MANIFEST_NAME, ".DS_Store"}:
            continue
        stat = path.stat()
        result.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "size": stat.st_size,
                "executable": bool(stat.st_mode & 0o111),
            }
        )
    return result


def build_manifest(dist_root: Path, command: list[str]) -> dict[str, Any]:
    """Create auditable local build provenance without depending on Git state."""
    try:
        app_version = version("astraweft")
    except PackageNotFoundError:
        app_version = "0.1.0.dev0"
    files = artifact_files(dist_root)
    aggregate = hashlib.sha256()
    for item in files:
        aggregate.update(f"{item['sha256']}  {item['path']}\n".encode())
    return {
        "schema_version": 1,
        "product": "AstraWeft",
        "version": app_version,
        "license": "Apache-2.0",
        "created_at": datetime.now(UTC).isoformat(),
        "build": {
            "format": "pyinstaller-onedir",
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.system().lower(),
            "platform_release": platform.release(),
            "architecture": platform.machine().lower(),
            "command": command,
            "spec_sha256": sha256_file(PROJECT_ROOT / "packaging" / "AstraWeft.spec"),
        },
        "payload_sha256": aggregate.hexdigest(),
        "file_count": len(files),
        "files": files,
    }


def copy_release_extras(dist_root: Path) -> None:
    """Place user-installable integration assets beside the desktop bundle."""
    source = PROJECT_ROOT / "integrations" / "comfyui_custom_nodes" / "AstraWeftGateway"
    target = dist_root / "extras" / "AstraWeftGateway"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    legal = dist_root / "legal"
    legal.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROJECT_ROOT / "LICENSE", legal / "LICENSE")
    shutil.copy2(PROJECT_ROOT / "NOTICE", legal / "NOTICE")


def build(dist_root: Path, work_root: Path) -> Path:
    """Run PyInstaller, add release extras, and write the integrity manifest."""
    dist_root = dist_root.resolve()
    work_root = work_root.resolve()
    dist_root.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist_root),
        "--workpath",
        str(work_root),
        str(PROJECT_ROOT / "packaging" / "AstraWeft.spec"),
    ]
    environment = os.environ.copy()
    environment.setdefault("PYTHONHASHSEED", "0")
    subprocess.run(command, cwd=PROJECT_ROOT, env=environment, check=True)  # noqa: S603
    if platform.system() == "Darwin" and (dist_root / "AstraWeft.app").is_dir():
        duplicate_collection = dist_root / "AstraWeft"
        if duplicate_collection.is_dir():
            shutil.rmtree(duplicate_collection)
    copy_release_extras(dist_root)
    manifest = build_manifest(dist_root, command)
    manifest_path = dist_root / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    args = parser.parse_args()
    manifest = build(args.dist_dir, args.work_dir)
    sys.stdout.write(f"{manifest}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
