"""Submit a Developer ID signed AstraWeft app, staple it, and re-hash final bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = PROJECT_ROOT / "dist" / "desktop"
DEFAULT_PHASE_ROOT = PROJECT_ROOT / "build" / "phase8"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "no process output").strip()
        raise RuntimeError(f"macOS finalization failed ({result.returncode}): {details}")
    return result


def _json_stdout(result: subprocess.CompletedProcess[str], label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} returned malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{label} did not return a JSON object")
    return parsed


def notary_submit_command(
    archive: Path,
    keychain_profile: str,
    keychain: Path | None,
) -> list[str]:
    command = [
        "/usr/bin/xcrun",
        "notarytool",
        "submit",
        str(archive),
        "--keychain-profile",
        keychain_profile,
        "--wait",
        "--timeout",
        "30m",
        "--output-format",
        "json",
        "--no-progress",
    ]
    if keychain is not None:
        command.extend(("--keychain", str(keychain)))
    return command


def finalize(
    dist_root: Path,
    phase_root: Path,
    *,
    keychain_profile: str,
    keychain: Path | None,
    expected_team_id: str | None,
) -> dict[str, Any]:
    """Notarize the signed app and produce the only promotable final archive."""
    if platform.system() != "Darwin":
        raise RuntimeError("macOS notarization must run on macOS")
    dist_root = dist_root.resolve()
    phase_root = phase_root.resolve()
    app = dist_root / "AstraWeft.app"
    if not app.is_dir():
        raise FileNotFoundError(f"macOS application bundle not found: {app}")
    phase_root.mkdir(parents=True, exist_ok=True)
    signed_evidence_path = phase_root / "macos-signed-verification.json"
    signed_command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "verify_platform_release.py"),
        "--dist-dir",
        str(dist_root),
        "--platform",
        "macos",
        "--skip-notarization",
        "--output",
        str(signed_evidence_path),
    ]
    if expected_team_id is not None:
        signed_command.extend(("--expected-team-id", expected_team_id))
    signed = _json_stdout(_run(signed_command), "Developer ID verification")

    submission_dir = phase_root / "notary-submission"
    submission_dir.mkdir(parents=True, exist_ok=True)
    submission_archive = submission_dir / "AstraWeft-notary-submission.zip"
    submission_archive.unlink(missing_ok=True)
    _run(
        [
            "/usr/bin/ditto",
            "-c",
            "-k",
            "--keepParent",
            "--sequesterRsrc",
            str(app),
            str(submission_archive),
        ]
    )
    submission_sha256 = sha256_file(submission_archive)
    notary = _json_stdout(
        _run(
            notary_submit_command(submission_archive, keychain_profile, keychain),
            timeout=1_900,
        ),
        "notarytool",
    )
    if notary.get("status") != "Accepted" or not notary.get("id"):
        raise RuntimeError(
            f"Apple notarization was not accepted: {notary.get('status') or 'unknown status'}"
        )

    _run(["/usr/bin/xcrun", "stapler", "staple", "-v", str(app)])
    final_evidence_path = phase_root / "macos-final-verification.json"
    final_command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "verify_platform_release.py"),
        "--dist-dir",
        str(dist_root),
        "--platform",
        "macos",
        "--output",
        str(final_evidence_path),
    ]
    if expected_team_id is not None:
        final_command.extend(("--expected-team-id", expected_team_id))
    final_verification = _json_stdout(_run(final_command), "notarized release verification")

    release_metadata = {
        "platform": "macos",
        "finalized_at": datetime.now(UTC).isoformat(),
        "codesign": {
            "authority": signed["authorities"][0],
            "team_identifier": signed["team_identifier"],
            "hardened_runtime": signed["hardened_runtime"],
            "timestamp": signed["timestamp"],
        },
        "notarization": {
            "submission_id": notary["id"],
            "status": notary["status"],
            "submission_sha256": submission_sha256,
            "stapled": final_verification["notarized"],
            "gatekeeper": final_verification["gatekeeper"],
        },
    }
    metadata_path = phase_root / "macos-release-metadata.json"
    metadata_path.write_text(json.dumps(release_metadata, indent=2) + "\n", encoding="utf-8")
    _run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "build_desktop.py"),
            "--dist-dir",
            str(dist_root),
            "--manifest-only",
            "--release-metadata",
            str(metadata_path),
        ]
    )
    archive_result = _json_stdout(
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
            ],
            timeout=600,
        ),
        "release manifest verification",
    )
    return {
        "status": "passed",
        "platform": "macos",
        "application": str(app),
        "notary_submission_id": notary["id"],
        "notary_submission_archive": str(submission_archive),
        "notary_submission_sha256": submission_sha256,
        "team_identifier": signed["team_identifier"],
        "hardened_runtime": signed["hardened_runtime"],
        "stapled": final_verification["notarized"],
        "gatekeeper": final_verification["gatekeeper"],
        "final_archive": archive_result["archive"],
        "evidence": {
            "signed_verification": str(signed_evidence_path),
            "final_verification": str(final_evidence_path),
            "release_metadata": str(metadata_path),
            "manifest_verification": str(phase_root / "manifest-verification.json"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--phase-dir", type=Path, default=DEFAULT_PHASE_ROOT)
    parser.add_argument("--keychain-profile", required=True)
    parser.add_argument("--keychain", type=Path)
    parser.add_argument("--expected-team-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = finalize(
        args.dist_dir,
        args.phase_dir,
        keychain_profile=args.keychain_profile,
        keychain=args.keychain,
        expected_team_id=args.expected_team_id,
    )
    rendered = json.dumps(report, indent=2) + "\n"
    output = args.output or args.phase_dir / "macos-notarization.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
