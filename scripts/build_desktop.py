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


def _resolved_inside(path: Path, root: Path) -> None:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise RuntimeError(f"release symlink escapes or is broken: {path}") from exc


def artifact_entries(root: Path) -> list[dict[str, Any]]:
    """Describe every release file and symlink except the manifest itself."""
    result: list[dict[str, Any]] = []
    paths = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    for path in paths:
        relative = path.relative_to(root)
        if relative == Path(MANIFEST_NAME) or path.name == ".DS_Store":
            continue
        if path.is_symlink():
            _resolved_inside(path, root)
            result.append(
                {
                    "path": relative.as_posix(),
                    "type": "symlink",
                    "target": path.readlink().as_posix(),
                }
            )
            continue
        if not path.is_file():
            continue
        stat = path.stat()
        result.append(
            {
                "path": relative.as_posix(),
                "type": "file",
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
    entries = artifact_entries(dist_root)
    aggregate = hashlib.sha256()
    for entry in entries:
        aggregate.update(
            json.dumps(
                entry,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
    return {
        "schema_version": 2,
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
        "entry_count": len(entries),
        "file_count": sum(entry["type"] == "file" for entry in entries),
        "symlink_count": sum(entry["type"] == "symlink" for entry in entries),
        "entries": entries,
    }


def write_manifest(
    dist_root: Path,
    command: list[str],
    *,
    release_metadata: dict[str, Any] | None = None,
) -> Path:
    """Write a fresh manifest for the exact current payload bytes."""
    manifest = build_manifest(dist_root, command)
    if release_metadata is not None:
        manifest["release"] = release_metadata
    manifest_path = dist_root / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def refresh_manifest(dist_root: Path, release_metadata_path: Path | None = None) -> Path:
    """Re-hash an externally signed/stapled payload while preserving build provenance."""
    manifest_path = dist_root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"release manifest not found: {manifest_path}")
    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    build = existing.get("build")
    command = build.get("command") if isinstance(build, dict) else None
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise RuntimeError("existing release manifest has invalid build provenance")
    release_metadata: dict[str, Any] | None = None
    if release_metadata_path is not None:
        parsed = json.loads(release_metadata_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise RuntimeError("release metadata must be a JSON object")
        release_metadata = parsed
    return write_manifest(dist_root, command, release_metadata=release_metadata)


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
    return write_manifest(dist_root, command)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="re-hash an existing payload after platform signing or stapling",
    )
    parser.add_argument(
        "--release-metadata",
        type=Path,
        help="JSON object to embed when refreshing a finalized payload",
    )
    args = parser.parse_args()
    if args.release_metadata is not None and not args.manifest_only:
        parser.error("--release-metadata requires --manifest-only")
    manifest = (
        refresh_manifest(args.dist_dir.resolve(), args.release_metadata)
        if args.manifest_only
        else build(args.dist_dir, args.work_dir)
    )
    sys.stdout.write(f"{manifest}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
