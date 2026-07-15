"""Verify native platform trust properties for a finalized AstraWeft payload."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = PROJECT_ROOT / "dist" / "desktop"


def _run(
    command: list[str],
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "no process output").strip()
        raise RuntimeError(f"platform verification failed ({result.returncode}): {details}")
    return result


def parse_codesign_metadata(output: str) -> dict[str, Any]:
    """Parse the stable key/value subset of ``codesign -dv`` output."""
    values: dict[str, str] = {}
    authorities: list[str] = []
    code_directory = ""
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("CodeDirectory "):
            code_directory = line
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "Authority":
            authorities.append(value)
        else:
            values[key] = value
    return {
        "identifier": values.get("Identifier"),
        "team_identifier": values.get("TeamIdentifier"),
        "authorities": authorities,
        "timestamp": values.get("Timestamp"),
        "hardened_runtime": "runtime" in code_directory.lower(),
        "adhoc": values.get("Signature") == "adhoc" or "(adhoc)" in code_directory.lower(),
    }


def verify_macos_release(
    dist_root: Path,
    *,
    require_notarization: bool,
    expected_team_id: str | None = None,
) -> dict[str, Any]:
    """Require Developer ID, hardened runtime, and optionally notarization."""
    if platform.system() != "Darwin":
        raise RuntimeError("macOS release verification must run on macOS")
    app = dist_root.resolve() / "AstraWeft.app"
    if not app.is_dir():
        raise FileNotFoundError(f"macOS application bundle not found: {app}")

    _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
    displayed = _run(["/usr/bin/codesign", "--display", "--verbose=4", str(app)])
    metadata = parse_codesign_metadata(f"{displayed.stdout}\n{displayed.stderr}")
    authorities = metadata["authorities"]
    if metadata["adhoc"] or not authorities:
        raise RuntimeError("macOS payload is ad-hoc signed, not Developer ID signed")
    if not str(authorities[0]).startswith("Developer ID Application:"):
        raise RuntimeError("macOS payload is not signed by a Developer ID Application certificate")
    if not metadata["hardened_runtime"]:
        raise RuntimeError("macOS payload does not enable hardened runtime")
    if not metadata["timestamp"]:
        raise RuntimeError("macOS payload does not contain a secure signing timestamp")
    team_id = metadata["team_identifier"]
    if not team_id or team_id == "not set":
        raise RuntimeError("macOS payload does not contain a TeamIdentifier")
    if expected_team_id is not None and team_id != expected_team_id:
        raise RuntimeError(f"expected macOS TeamIdentifier {expected_team_id}, got {team_id}")

    report: dict[str, Any] = {
        "status": "passed",
        "platform": "macos",
        "application": str(app),
        **metadata,
        "notarized": False,
        "gatekeeper": False,
    }
    if require_notarization:
        stapler = _run(["/usr/bin/xcrun", "stapler", "validate", "-v", str(app)])
        gatekeeper = _run(
            ["/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=4", str(app)]
        )
        report.update(
            {
                "notarized": True,
                "gatekeeper": True,
                "stapler_output": (stapler.stdout or stapler.stderr).strip(),
                "gatekeeper_output": (gatekeeper.stdout or gatekeeper.stderr).strip(),
            }
        )
    return report


def _powershell_executable() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        raise FileNotFoundError("PowerShell is required for Authenticode verification")
    return executable


def verify_windows_release(
    dist_root: Path,
    *,
    expected_publisher: str | None = None,
) -> dict[str, Any]:
    """Require valid timestamped Authenticode on every packaged PE file."""
    if platform.system() != "Windows":
        raise RuntimeError("Windows release verification must run on Windows")
    payload = dist_root.resolve() / "AstraWeft"
    executable = payload / "AstraWeft.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"Windows application executable not found: {executable}")
    environment = os.environ.copy()
    environment["ASTRAWEFT_VERIFY_ROOT"] = str(payload)
    script = "\n".join(
        (
            "$ErrorActionPreference = 'Stop'",
            "$files = @(Get-ChildItem -LiteralPath $env:ASTRAWEFT_VERIFY_ROOT -Recurse -File | "
            "Where-Object { $_.Extension -in '.exe', '.dll' })",
            "if ($files.Count -eq 0) { throw 'No PE files found' }",
            "$records = @($files | ForEach-Object {",
            "  $signature = Get-AuthenticodeSignature -LiteralPath $_.FullName",
            "  [PSCustomObject]@{",
            "    path = $_.FullName.Substring($env:ASTRAWEFT_VERIFY_ROOT.Length + 1)",
            "    status = [string]$signature.Status",
            "    signer = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { $null }",
            "    thumbprint = if ($signature.SignerCertificate) { $signature.SignerCertificate.Thumbprint } else { $null }",
            "    timestamp_signer = if ($signature.TimeStamperCertificate) { $signature.TimeStamperCertificate.Subject } else { $null }",
            "  }",
            "})",
            "ConvertTo-Json -InputObject $records -Depth 4 -Compress",
        )
    )
    result = _run(
        [_powershell_executable(), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        environment=environment,
    )
    parsed = json.loads(result.stdout)
    records = parsed if isinstance(parsed, list) else [parsed]
    if not records or not all(isinstance(record, dict) for record in records):
        raise RuntimeError("Windows signature evidence is malformed")
    invalid = [
        record.get("path")
        for record in records
        if record.get("status") != "Valid" or not record.get("signer")
    ]
    if invalid:
        raise RuntimeError(f"Windows payload contains invalid or unsigned PE files: {invalid}")
    untimestamped = [record.get("path") for record in records if not record.get("timestamp_signer")]
    if untimestamped:
        raise RuntimeError(f"Windows payload contains untimestamped PE files: {untimestamped}")
    main = next((record for record in records if record.get("path") == "AstraWeft.exe"), None)
    if main is None:
        raise RuntimeError("Windows signature evidence omitted AstraWeft.exe")
    signer = str(main["signer"])
    if expected_publisher is not None and expected_publisher not in signer:
        raise RuntimeError(f"expected Windows publisher {expected_publisher!r}, got {signer!r}")
    return {
        "status": "passed",
        "platform": "windows",
        "application": str(executable),
        "signed_pe_files": len(records),
        "publisher": signer,
        "thumbprint": main.get("thumbprint"),
        "timestamp_signer": main.get("timestamp_signer"),
    }


def verify_linux_release(dist_root: Path) -> dict[str, Any]:
    """Require an executable Linux payload with all dynamic libraries resolved."""
    if platform.system() != "Linux":
        raise RuntimeError("Linux release verification must run on Linux")
    executable = dist_root.resolve() / "AstraWeft" / "AstraWeft"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise FileNotFoundError(f"executable Linux application not found: {executable}")
    ldd = shutil.which("ldd")
    if ldd is None:
        raise FileNotFoundError("ldd is required for Linux dependency verification")
    result = _run([ldd, str(executable)])
    unresolved = [line.strip() for line in result.stdout.splitlines() if "not found" in line]
    if unresolved:
        raise RuntimeError(f"Linux payload has unresolved dynamic libraries: {unresolved}")
    return {
        "status": "passed",
        "platform": "linux",
        "application": str(executable),
        "dynamic_libraries_resolved": True,
    }


def _native_platform() -> str:
    mapping = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}
    try:
        return mapping[platform.system()]
    except KeyError as exc:
        raise RuntimeError(f"unsupported release platform: {platform.system()}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--platform", choices=("macos", "windows", "linux"))
    parser.add_argument("--skip-notarization", action="store_true")
    parser.add_argument("--expected-team-id")
    parser.add_argument("--expected-publisher")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    selected = args.platform or _native_platform()
    if selected == "macos":
        report = verify_macos_release(
            args.dist_dir,
            require_notarization=not args.skip_notarization,
            expected_team_id=args.expected_team_id,
        )
    elif selected == "windows":
        report = verify_windows_release(
            args.dist_dir,
            expected_publisher=args.expected_publisher,
        )
    else:
        report = verify_linux_release(args.dist_dir)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
