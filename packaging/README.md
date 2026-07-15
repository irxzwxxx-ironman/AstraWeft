# AstraWeft desktop packaging

The local release uses a shared PyInstaller one-folder specification. A one-folder payload is
intentional: it keeps dependency inspection, code signing, update staging, and support diagnostics
straightforward on macOS, Windows, and Linux.

## Local macOS developer build

```bash
.venv/bin/uv sync --all-packages --all-groups
.venv/bin/python scripts/build_desktop.py
.venv/bin/python scripts/verify_release_manifest.py --dist-dir dist/desktop \
  --output build/phase8/manifest-verification.json \
  --archive-dir build/phase8/release-artifacts
.venv/bin/python scripts/smoke_desktop.py --expected-revision 20260715_0007 \
  --output build/phase8/package-smoke.json
.venv/bin/python scripts/smoke_upgrade.py --output build/phase8/upgrade-smoke.json
open dist/desktop/AstraWeft.app
```

The output contains:

- `AstraWeft.app` on macOS, or the `AstraWeft/` folder on Windows and Linux;
- `extras/AstraWeftGateway/`, the installable ComfyUI custom node;
- `legal/`, with Apache-2.0 license and notice files;
- `release-manifest.json` schema v2, with build provenance, file hashes/modes and symlink targets;
- `build/phase8/release-artifacts/`, containing the platform archive and its SHA-256 checksum.

The verifier is intentionally separate from the generator. It rejects missing or unexpected payload
members, changed content or executable modes, broken/escaping symlinks, non-canonical paths, and
aggregate-digest mismatches. The archive is extracted to a temporary directory and the extracted
payload must pass the same verification before the archive is accepted.

## Complete local release gate

The composite gate rebuilds all five wheels in isolation, validates their metadata and contents,
installs a runtime-only environment, rebuilds the desktop package, exercises cold start and database
upgrade/rollback, then regenerates SBOM, license, provenance, and vulnerability evidence:

```bash
.venv/bin/uv sync --locked --all-groups
.venv/bin/python scripts/run_local_release_gate.py
```

The machine-readable result is written to `build/phase8/local-release-gate.json`. This gate is
platform-native: running it on macOS does not claim that Windows or Linux packages pass.

The packaged cold-start check uses an isolated loopback port and requires the authenticated gateway
to reach `ready`. It also records whether secure storage is persistent. An unsigned macOS developer
bundle is expected to fall back to process memory if Keychain rejects the executable; this is safe
for local development but credentials and the gateway token do not survive restart.

The local app is deliberately unsigned. Public macOS builds still require Developer ID signing,
hardened runtime, notarization, stapling, and a clean-machine Gatekeeper verification. Windows
public builds similarly require Authenticode signing and clean-machine SmartScreen validation.

For an authorized Developer ID release environment, provide the public certificate identity at
build time so PyInstaller signs collected binaries before the manifest is generated:

```bash
ASTRAWEFT_CODESIGN_IDENTITY="Developer ID Application: Publisher (TEAMID)" \
  .venv/bin/python scripts/run_local_release_gate.py
.venv/bin/python scripts/notarize_macos.py \
  --keychain-profile astraweft-notary \
  --expected-team-id TEAMID
```

`notarize_macos.py` accepts only a Keychain profile, never an inline Apple password. It rejects
ad-hoc signatures, requires Developer ID, hardened runtime and a secure timestamp, submits a
`ditto` ZIP, staples and validates the ticket, runs Gatekeeper assessment, then re-hashes the
stapled bytes and replaces the native archive. Only that final archive is promotable.

## Native candidate CI

`.github/workflows/release-candidate.yml` runs the complete gate independently on macOS, Windows,
and Linux when manually dispatched or when a `v*-beta.*` / `v*-rc.*` tag is pushed. Each successful
job uploads the verified native archive, checksum, five wheels, cold-start and upgrade reports,
SBOM, license inventory, and vulnerability evidence. These are unsigned candidate artifacts; they
must not be promoted as public Beta installers until the platform signing and clean-machine gates
also pass.

Verify a downloaded checksum from inside its artifact directory so the portable basename in the
checksum file resolves correctly:

```bash
cd build/phase8/release-artifacts
shasum -a 256 -c *.sha256
```
