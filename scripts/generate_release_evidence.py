"""Generate a CycloneDX SBOM, license inventory, and vulnerability audit."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "build" / "phase8" / "release-evidence"
FIRST_PARTY = {
    "astraweft",
    "astraweft-custom-rest-provider",
    "astraweft-mock-provider",
    "astraweft-openai-provider",
    "astraweft-provider-sdk",
    "astraweft-runway-provider",
}
METADATA_PROGRAM = r"""
import json
from importlib.metadata import distributions

records = []
for distribution in distributions():
    metadata = distribution.metadata
    name = metadata.get("Name") or "UNKNOWN"
    expression = metadata.get("License-Expression")
    legacy = metadata.get("License")
    classifiers = [
        value.removeprefix("License :: ")
        for value in metadata.get_all("Classifier", [])
        if value.startswith("License :: ")
    ]
    if expression:
        license_value = expression
    elif legacy and len(legacy.strip()) <= 200 and "\n" not in legacy.strip():
        license_value = legacy.strip()
    elif classifiers:
        license_value = "; ".join(classifiers)
    else:
        license_value = "UNKNOWN"
    project_urls = {}
    for value in metadata.get_all("Project-URL", []):
        if "," in value:
            label, url = value.split(",", 1)
            project_urls[label.strip()] = url.strip()
    records.append({
        "name": name,
        "version": distribution.version,
        "license": license_value,
        "homepage": metadata.get("Home-page") or project_urls.get("Homepage") or project_urls.get("Source"),
    })
print(json.dumps(sorted(records, key=lambda item: item["name"].lower())))
"""


def _run(
    command: list[str], *, allowed_codes: tuple[int, ...] = (0,)
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode not in allowed_codes:
        details = (result.stderr or result.stdout or "no process output").strip()
        raise RuntimeError(f"release evidence command failed ({result.returncode}): {details}")
    return result


def _site_packages(python: Path) -> Path:
    result = _run(
        [
            str(python),
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ]
    )
    return Path(result.stdout.strip())


def _license_inventory(python: Path) -> list[dict[str, Any]]:
    result = _run([str(python), "-c", METADATA_PROGRAM])
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, list):
        raise RuntimeError("target interpreter returned an invalid license inventory")
    return parsed


def _write_license_markdown(records: list[dict[str, Any]], path: Path) -> None:
    third_party = [record for record in records if record["name"].lower() not in FIRST_PARTY]
    unknown = [record["name"] for record in third_party if record["license"] == "UNKNOWN"]
    lines = [
        "# AstraWeft third-party license inventory",
        "",
        f"Packages: {len(third_party)} · Unknown license metadata: {len(unknown)}",
        "",
        "| Package | Version | License metadata |",
        "|---|---:|---|",
    ]
    for record in third_party:
        license_value = str(record["license"]).replace("|", "\\|")
        lines.append(f"| {record['name']} | {record['version']} | {license_value} |")
    if unknown:
        lines += ["", "## Requires manual review", "", *[f"- {name}" for name in unknown]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate(python: Path, output_dir: Path) -> dict[str, Any]:
    python = python.expanduser()
    if not python.is_absolute():
        python = Path(os.path.abspath(PROJECT_ROOT / python))
    output_dir.mkdir(parents=True, exist_ok=True)
    cyclonedx = Path(sys.executable).with_name("cyclonedx-py")
    pip_audit = Path(sys.executable).with_name("pip-audit")
    sbom = output_dir / "astraweft.cdx.json"
    _run(
        [
            str(cyclonedx),
            "environment",
            str(python),
            "--pyproject",
            str(PROJECT_ROOT / "pyproject.toml"),
            "--mc-type",
            "application",
            "--sv",
            "1.6",
            "--output-reproducible",
            "--of",
            "JSON",
            "--output-file",
            str(sbom),
        ]
    )
    sbom_payload = json.loads(sbom.read_text(encoding="utf-8"))
    if sbom_payload.get("bomFormat") != "CycloneDX":
        raise RuntimeError("generated SBOM is not CycloneDX JSON")

    records = _license_inventory(python)
    inventory_json = output_dir / "third-party-licenses.json"
    inventory_json.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    _write_license_markdown(records, output_dir / "third-party-licenses.md")

    audit = output_dir / "pip-audit.json"
    audit.unlink(missing_ok=True)
    audit_result = _run(
        [
            str(pip_audit),
            "--path",
            str(_site_packages(python)),
            "--vulnerability-service",
            "osv",
            "--timeout",
            "60",
            "--format",
            "json",
            "--output",
            str(audit),
            "--progress-spinner",
            "off",
        ],
        allowed_codes=(0, 1),
    )
    if not audit.is_file():
        details = (audit_result.stderr or audit_result.stdout or "no process output").strip()
        raise RuntimeError(f"vulnerability audit did not produce evidence: {details}")
    audit_payload = json.loads(audit.read_text(encoding="utf-8"))
    vulnerabilities = sum(
        len(item.get("vulns", [])) for item in audit_payload.get("dependencies", [])
    )
    report = {
        "status": "passed" if audit_result.returncode == 0 else "failed",
        "python": str(python),
        "components": len(sbom_payload.get("components", [])),
        "installed_distributions": len(records),
        "third_party_distributions": sum(
            record["name"].lower() not in FIRST_PARTY for record in records
        ),
        "unknown_license_metadata": [
            record["name"]
            for record in records
            if record["name"].lower() not in FIRST_PARTY and record["license"] == "UNKNOWN"
        ],
        "known_vulnerabilities": vulnerabilities,
        "artifacts": {
            "sbom": sbom.name,
            "licenses_json": inventory_json.name,
            "licenses_markdown": "third-party-licenses.md",
            "audit": audit.name,
        },
    }
    (output_dir / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = generate(args.python, args.output_dir)
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
