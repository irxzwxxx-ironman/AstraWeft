"""Independently verify and archive an AstraWeft desktop release payload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

MANIFEST_NAME = "release-manifest.json"
_SHA256 = re.compile(r"[0-9a-f]{64}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"release manifest is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("release manifest root must be an object")
    return value


def _safe_target(root: Path, relative: str) -> Path:
    candidate = root / relative
    try:
        candidate.parent.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise RuntimeError(f"manifest path escapes the release root: {relative}") from exc
    if Path(relative).is_absolute() or "\\" in relative or ".." in Path(relative).parts:
        raise RuntimeError(f"manifest path is not canonical: {relative}")
    if not relative or Path(relative).as_posix() != relative:
        raise RuntimeError(f"manifest path is not canonical: {relative}")
    return candidate


def _validate_entry(root: Path, value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("manifest entries must be objects")
    relative = value.get("path")
    kind = value.get("type")
    if not isinstance(relative, str):
        raise RuntimeError("manifest entry path must be text")
    path = _safe_target(root, relative)
    if kind == "file":
        if set(value) != {"path", "type", "sha256", "size", "executable"}:
            raise RuntimeError(f"file manifest entry has unexpected fields: {relative}")
        digest = value.get("sha256")
        size = value.get("size")
        executable = value.get("executable")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise RuntimeError(f"file manifest digest is invalid: {relative}")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise RuntimeError(f"file manifest size is invalid: {relative}")
        if not isinstance(executable, bool):
            raise RuntimeError(f"file manifest executable flag is invalid: {relative}")
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"manifest file is missing or has the wrong type: {relative}")
        metadata = path.stat()
        if metadata.st_size != size:
            raise RuntimeError(f"release file size mismatch: {relative}")
        if bool(metadata.st_mode & 0o111) != executable:
            raise RuntimeError(f"release executable mode mismatch: {relative}")
        if sha256_file(path) != digest:
            raise RuntimeError(f"release file digest mismatch: {relative}")
        return dict(value)
    if kind == "symlink":
        if set(value) != {"path", "type", "target"}:
            raise RuntimeError(f"symlink manifest entry has unexpected fields: {relative}")
        target = value.get("target")
        if not isinstance(target, str) or not target:
            raise RuntimeError(f"symlink target is invalid: {relative}")
        if Path(target).is_absolute() or "\\" in target:
            raise RuntimeError(f"symlink target is not relocatable: {relative}")
        if not path.is_symlink() or path.readlink().as_posix() != target:
            raise RuntimeError(f"release symlink target mismatch: {relative}")
        try:
            path.resolve(strict=True).relative_to(root.resolve(strict=True))
        except (FileNotFoundError, ValueError) as exc:
            raise RuntimeError(f"release symlink escapes or is broken: {relative}") from exc
        return dict(value)
    raise RuntimeError(f"manifest entry type is invalid: {relative}")


def _actual_entry_paths(root: Path) -> set[str]:
    result: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative == Path(MANIFEST_NAME) or path.name == ".DS_Store":
            continue
        if path.is_symlink() or path.is_file():
            result.add(relative.as_posix())
    return result


def verify_release_manifest(dist_root: Path) -> dict[str, Any]:
    root = dist_root.resolve(strict=True)
    manifest_path = root / MANIFEST_NAME
    manifest = _load_manifest(manifest_path)
    if manifest.get("schema_version") != 2 or manifest.get("product") != "AstraWeft":
        raise RuntimeError("release manifest identity or schema is unsupported")
    values = manifest.get("entries")
    if not isinstance(values, list):
        raise RuntimeError("release manifest entries must be an array")
    entries = [_validate_entry(root, value) for value in values]
    paths = [str(entry["path"]) for entry in entries]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise RuntimeError("release manifest paths must be unique and sorted")
    actual_paths = _actual_entry_paths(root)
    if set(paths) != actual_paths:
        missing = sorted(set(paths) - actual_paths)
        unexpected = sorted(actual_paths - set(paths))
        raise RuntimeError(
            f"release payload membership mismatch; missing={missing}, unexpected={unexpected}"
        )
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
    payload_digest = aggregate.hexdigest()
    files = sum(entry["type"] == "file" for entry in entries)
    symlinks = sum(entry["type"] == "symlink" for entry in entries)
    expected_counts = tuple(
        manifest.get(name) for name in ("entry_count", "file_count", "symlink_count")
    )
    if any(isinstance(value, bool) or not isinstance(value, int) for value in expected_counts):
        raise RuntimeError("release manifest counts must be integers")
    if expected_counts != (len(entries), files, symlinks):
        raise RuntimeError("release manifest counts do not match its entries")
    if manifest.get("payload_sha256") != payload_digest:
        raise RuntimeError("release payload aggregate digest mismatch")
    build = manifest.get("build")
    if not isinstance(build, dict):
        raise RuntimeError("release manifest build metadata must be an object")
    identity = (manifest.get("version"), build.get("platform"), build.get("architecture"))
    if any(not isinstance(value, str) or not value.strip() for value in identity):
        raise RuntimeError("release manifest version and platform identity must be text")
    return {
        "status": "passed",
        "schema_version": 2,
        "entry_count": len(entries),
        "file_count": files,
        "symlink_count": symlinks,
        "payload_sha256": payload_digest,
        "manifest_sha256": sha256_file(manifest_path),
        "version": manifest.get("version"),
        "platform": build.get("platform"),
        "architecture": build.get("architecture"),
    }


def _archive_stem(report: dict[str, Any]) -> str:
    raw = "-".join(
        str(report.get(key) or "unknown") for key in ("version", "platform", "architecture")
    )
    return "AstraWeft-" + re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if Path(info.name).name == ".DS_Store":
        return None
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def create_release_archive(
    dist_root: Path,
    output_dir: Path,
    report: dict[str, Any],
) -> dict[str, Any]:
    try:
        output_dir.resolve().relative_to(dist_root.resolve(strict=True))
    except ValueError:
        pass
    else:
        raise ValueError("release archive output must be outside the payload root")
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = _archive_stem(report)
    if os.name == "nt":
        archive = output_dir / f"{stem}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(dist_root.rglob("*")):
                if path.name == ".DS_Store":
                    continue
                relative = Path("AstraWeft") / path.relative_to(dist_root)
                if path.is_symlink():
                    info = zipfile.ZipInfo(relative.as_posix())
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    bundle.writestr(info, path.readlink().as_posix())
                elif path.is_file():
                    bundle.write(path, relative.as_posix())
    else:
        archive = output_dir / f"{stem}.tar.gz"
        with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT, dereference=False) as bundle:
            bundle.add(dist_root, arcname="AstraWeft", recursive=True, filter=_tar_filter)
    digest = sha256_file(archive)
    with tempfile.TemporaryDirectory(prefix="astraweft-archive-roundtrip-") as temporary:
        extracted = Path(temporary)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    destination = extracted / member.filename
                    try:
                        destination.resolve().relative_to(extracted.resolve())
                    except ValueError as exc:
                        raise RuntimeError("release archive contains an unsafe path") from exc
                    mode = member.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        raise RuntimeError("Windows release ZIP cannot contain symbolic links")
                    if member.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, destination.open("wb") as target:
                        shutil.copyfileobj(source, target)
                    if mode:
                        destination.chmod(stat.S_IMODE(mode))
        else:
            with tarfile.open(archive, "r:gz") as bundle:
                bundle.extractall(extracted, filter="data")
        roundtrip = verify_release_manifest(extracted / "AstraWeft")
        if roundtrip["payload_sha256"] != report["payload_sha256"]:
            raise RuntimeError("release archive round-trip changed the payload")
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return {
        "path": str(archive),
        "name": archive.name,
        "size": archive.stat().st_size,
        "sha256": digest,
        "checksum": str(checksum),
        "roundtrip_verified": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--archive-dir", type=Path)
    args = parser.parse_args()
    report = verify_release_manifest(args.dist_dir)
    if args.archive_dir is not None:
        report["archive"] = create_release_archive(args.dist_dir, args.archive_dir, report)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
