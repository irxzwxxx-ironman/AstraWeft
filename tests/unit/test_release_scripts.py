"""Regression tests for platform-neutral release script helpers."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str) -> ModuleType:
    path = PROJECT_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_artifact_manifest_is_stable_and_ignores_self(tmp_path: Path) -> None:
    module = _load_script("build_desktop")
    (tmp_path / "nested").mkdir()
    payload = tmp_path / "nested" / "payload.txt"
    payload.write_text("astraweft", encoding="utf-8")
    sibling = tmp_path / "nested-info.txt"
    sibling.write_text("metadata", encoding="utf-8")
    (tmp_path / "release-manifest.json").write_text("stale", encoding="utf-8")
    (tmp_path / ".DS_Store").write_bytes(b"ignored")

    records = module.artifact_entries(tmp_path)

    assert records == [
        {
            "path": "nested-info.txt",
            "type": "file",
            "sha256": module.sha256_file(sibling),
            "size": len("metadata"),
            "executable": False,
        },
        {
            "path": "nested/payload.txt",
            "type": "file",
            "sha256": module.sha256_file(payload),
            "size": len("astraweft"),
            "executable": False,
        },
    ]


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation needs host privileges")
def test_artifact_manifest_records_symlink_identity(tmp_path: Path) -> None:
    module = _load_script("build_desktop")
    payload = tmp_path / "payload.txt"
    payload.write_text("content", encoding="utf-8")
    link = tmp_path / "payload-link"
    link.symlink_to(payload.name)

    records = module.artifact_entries(tmp_path)

    assert next(record for record in records if record["type"] == "symlink") == {
        "path": "payload-link",
        "type": "symlink",
        "target": "payload.txt",
    }


def test_manifest_refresh_rehashes_final_bytes_and_preserves_build_provenance(
    tmp_path: Path,
) -> None:
    module = _load_script("build_desktop")
    payload = tmp_path / "AstraWeft.bin"
    payload.write_bytes(b"signed-before-staple")
    original = module.build_manifest(tmp_path, ["pyinstaller", "AstraWeft.spec"])
    (tmp_path / "release-manifest.json").write_text(
        json.dumps(original),
        encoding="utf-8",
    )
    payload.write_bytes(b"signed-and-stapled")
    release_metadata = tmp_path.parent / "release-metadata.json"
    release_metadata.write_text(
        json.dumps({"platform": "macos", "notarization": {"status": "Accepted"}}),
        encoding="utf-8",
    )

    manifest_path = module.refresh_manifest(tmp_path, release_metadata)
    refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert refreshed["build"]["command"] == ["pyinstaller", "AstraWeft.spec"]
    assert refreshed["release"]["notarization"]["status"] == "Accepted"
    assert refreshed["entries"][0]["sha256"] == module.sha256_file(payload)
    assert refreshed["payload_sha256"] != original["payload_sha256"]


def test_packaged_executable_prefers_native_bundle(tmp_path: Path) -> None:
    module = _load_script("smoke_desktop")
    executable = tmp_path / "AstraWeft.app" / "Contents" / "MacOS" / "AstraWeft"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"launcher")
    fallback = tmp_path / "AstraWeft" / "AstraWeft"
    fallback.parent.mkdir()
    fallback.write_bytes(b"fallback")

    assert module.packaged_executable(tmp_path) == executable


def test_packaged_database_revision_is_read_without_app_import(tmp_path: Path) -> None:
    module = _load_script("smoke_desktop")
    database = tmp_path / "astraweft.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('20260715_0007')")

    assert module.database_revision(database) == "20260715_0007"
    assert module.database_revision(tmp_path / "missing.db") is None


def test_packaged_gateway_evidence_requires_ready_state(tmp_path: Path) -> None:
    module = _load_script("smoke_desktop")
    log_path = tmp_path / "astraweft.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "message": "loopback_gateway_ready",
                "context": {
                    "secure_storage_persistent": False,
                    "port": 32123,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert module.gateway_startup_evidence(log_path) == {
        "status": "ready",
        "port": 32123,
        "secure_storage_persistent": False,
    }


def test_release_manifest_verifier_and_native_archive(tmp_path: Path) -> None:
    builder = _load_script("build_desktop")
    verifier = _load_script("verify_release_manifest")
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"verified-payload")
    manifest = builder.build_manifest(tmp_path, ["test-build"])
    (tmp_path / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    report = verifier.verify_release_manifest(tmp_path)
    archive = verifier.create_release_archive(tmp_path, tmp_path.parent / "archives", report)

    assert report["status"] == "passed"
    assert report["schema_version"] == 2
    assert report["entry_count"] == 1
    assert Path(archive["path"]).is_file()
    assert verifier.sha256_file(Path(archive["path"])) == archive["sha256"]
    assert archive["roundtrip_verified"] is True
    assert Path(archive["checksum"]).read_text(encoding="utf-8").startswith(str(archive["sha256"]))

    payload.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match=r"size mismatch|digest mismatch"):
        verifier.verify_release_manifest(tmp_path)


def test_license_inventory_marks_unknown_for_manual_review(tmp_path: Path) -> None:
    module = _load_script("generate_release_evidence")
    output = tmp_path / "licenses.md"
    module._write_license_markdown(
        [
            {"name": "astraweft", "version": "0.1", "license": "Apache-2.0"},
            {"name": "known", "version": "1", "license": "MIT"},
            {"name": "review-me", "version": "2", "license": "UNKNOWN"},
        ],
        output,
    )

    rendered = output.read_text(encoding="utf-8")
    assert "Packages: 2 · Unknown license metadata: 1" in rendered
    assert "| known | 1 | MIT |" in rendered
    assert "- review-me" in rendered


def test_release_gate_selects_platform_venv_interpreter(tmp_path: Path) -> None:
    module = _load_script("run_local_release_gate")
    expected = tmp_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    assert module._venv_python(tmp_path) == expected


def test_codesign_metadata_requires_developer_id_runtime_and_timestamp() -> None:
    module = _load_script("verify_platform_release")
    parsed = module.parse_codesign_metadata(
        "\n".join(
            (
                "Identifier=dev.astraweft.app",
                "CodeDirectory v=20500 size=123 flags=0x10000(runtime) hashes=4+7",
                "Authority=Developer ID Application: AstraWeft Project (TEAM123456)",
                "Authority=Developer ID Certification Authority",
                "Timestamp=Jul 16, 2026 at 10:00:00",
                "TeamIdentifier=TEAM123456",
            )
        )
    )

    assert parsed == {
        "identifier": "dev.astraweft.app",
        "team_identifier": "TEAM123456",
        "authorities": [
            "Developer ID Application: AstraWeft Project (TEAM123456)",
            "Developer ID Certification Authority",
        ],
        "timestamp": "Jul 16, 2026 at 10:00:00",
        "hardened_runtime": True,
        "adhoc": False,
    }


def test_notary_submission_uses_keychain_profile_without_inline_credentials(
    tmp_path: Path,
) -> None:
    module = _load_script("notarize_macos")
    archive = tmp_path / "AstraWeft.zip"
    keychain = tmp_path / "release.keychain-db"

    command = module.notary_submit_command(archive, "astraweft-notary", keychain)

    assert command == [
        "/usr/bin/xcrun",
        "notarytool",
        "submit",
        str(archive),
        "--keychain-profile",
        "astraweft-notary",
        "--wait",
        "--timeout",
        "30m",
        "--output-format",
        "json",
        "--no-progress",
        "--keychain",
        str(keychain),
    ]
    assert "--apple-id" not in command
    assert "--password" not in command


def test_pyinstaller_spec_accepts_signing_only_through_explicit_environment() -> None:
    spec = (PROJECT_ROOT / "packaging" / "AstraWeft.spec").read_text(encoding="utf-8")

    assert 'os.environ.get("ASTRAWEFT_CODESIGN_IDENTITY")' in spec
    assert 'os.environ.get("ASTRAWEFT_MACOS_ENTITLEMENTS")' in spec
    assert "codesign_identity=codesign_identity" in spec
    assert "entitlements_file=entitlements_file" in spec
