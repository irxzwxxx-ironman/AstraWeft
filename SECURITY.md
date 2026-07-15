# Security Policy

AstraWeft manages API credentials, local files, paid provider calls, and workflow data. Security and
privacy reports are treated as product correctness issues, not optional hardening.

## Supported versions

The project is pre-alpha. Until the first public release, only the latest commit on the protected
default branch receives security fixes. A release support table will be published before beta.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or include live credentials, signed URLs,
private prompts, or user artifacts in a report.

Use GitHub Private Vulnerability Reporting for the repository. If private reporting is temporarily
unavailable, contact a listed repository maintainer privately and disclose only enough information to
establish a secure reporting channel.

Please include, when safe:

- Affected version, operating system, and installation method.
- Impact and the boundary that was crossed.
- Minimal reproduction steps using synthetic data.
- Whether credentials, files, money, or remote execution are involved.
- Any suggested mitigation or evidence that the issue is being exploited.

Maintainers should acknowledge a valid private report within five business days, provide a tracking
status, and coordinate disclosure after a fix or mitigation is available. Complex cross-vendor issues
may require additional time.

## Security invariants

- API secrets are stored in an operating-system credential store, not plaintext SQLite or settings.
- Secrets, authorization headers, signed URLs, and sensitive payloads never enter ordinary logs.
- Unknown remote task state does not trigger an automatic resubmission when duplicate billing is
  possible.
- The ComfyUI gateway binds to loopback by default and requires authentication.
- Imported workflows and downloaded artifacts are validated before use.
- Plugins are trusted local code in v1; the UI must not describe them as sandboxed.
- Telemetry is off by default and requires explicit opt-in if introduced later.

See the architecture and Provider plugin specification for the complete threat boundaries.
