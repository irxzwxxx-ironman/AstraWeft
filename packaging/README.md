# AstraWeft desktop packaging

The local release uses a shared PyInstaller one-folder specification. A one-folder payload is
intentional: it keeps dependency inspection, code signing, update staging, and support diagnostics
straightforward on macOS, Windows, and Linux.

## Local macOS developer build

```bash
.venv/bin/uv sync --all-packages --all-groups
.venv/bin/python scripts/build_desktop.py
.venv/bin/python scripts/smoke_desktop.py --expected-revision 20260715_0007 \
  --output build/phase8/package-smoke.json
.venv/bin/python scripts/smoke_upgrade.py --output build/phase8/upgrade-smoke.json
open dist/desktop/AstraWeft.app
```

The output contains:

- `AstraWeft.app` on macOS, or the `AstraWeft/` folder on Windows and Linux;
- `extras/AstraWeftGateway/`, the installable ComfyUI custom node;
- `legal/`, with Apache-2.0 license and notice files;
- `release-manifest.json`, with build provenance and SHA-256 for every payload file.

The local app is deliberately unsigned. Public macOS builds still require Developer ID signing,
hardened runtime, notarization, stapling, and a clean-machine Gatekeeper verification. Windows
public builds similarly require Authenticode signing and clean-machine SmartScreen validation.
