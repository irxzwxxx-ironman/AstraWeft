# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
intends to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Phase 0 repository, packaging, architecture, testing, and open-source governance baseline.
- AstraWeft product naming and Apache-2.0 licensing decisions.
- Phase 1 local application lifecycle with PySide6 and qasync.
- Platform-aware configuration and data directories with atomic validated settings.
- SQLite async runtime and bundled Alembic migration baseline.
- OS keyring credential adapter with a session-only fallback.
- Recursive secret redaction and rotating JSONL diagnostics.
- Modern Dark Cyber AI application shell, design tokens, reusable cards, honest zero-data states,
  and GUI navigation tests.
- Independent Provider SDK and Mock Provider packages with isolated discovery, Keychain-only
  credentials, Provider/Model management, model synchronization, and Schema-driven forms.
- Durable Task state machine, attempts, optimistic persistence, stable idempotency keys, bounded
  worker/polling runtime, conservative restart recovery, retry, cancel, and timeout semantics.
- Playground, Task Center, redacted Request Logs, verified local Artifacts, and data-backed
  Dashboard metrics.
- SQLite migration `20260715_0003`, atomic artifact storage, 1000-task/100,000-log GUI scale gate,
  and isolated Core/SDK/Mock wheel lifecycle smoke.
- Core-owned manifest-restricted HTTP transport with safe Request Log call metadata.
- Independent OpenAI Responses text Provider with model discovery, `store=false`, usage mapping,
  and conservative no-idempotency recovery.
- Independent Runway `gen4.5` asynchronous video Provider with submit, poll, cancel, failure policy,
  and restart recovery.
- Streamed, size-bounded, SHA-256-verified remote Artifact downloads with atomic publication and
  signed-URL redaction.
- Immutable Workflow versions, validated DAG editing, durable NodeRun scheduling/recovery,
  Transform/Provider execution, Artifact lineage, and visual edit/run observation modes.
- ComfyUI instance and API-template management, capability probing, durable execution
  reconciliation, WebSocket progress hints, authoritative queue/history polling, selected-output
  materialization, cancellation, and Workflow `COMFYUI` nodes.
- Authenticated `127.0.0.1` loopback Task gateway plus keyring-backed AstraWeft Provider Image and
  Video Custom Nodes for ComfyUI, with Host/Origin/body/rate/path safety guards.
- SQLite migrations `20260715_0004` and `20260715_0005`, including immutable workflow ledgers,
  ComfyUI execution snapshots, and separate planned/actual execution identities.
- Phase 7 staged local maintenance: SQLite online backups, restart-only atomic restore,
  verified data-root migration, Artifact trash/restore/retention, Request Log retention, and
  content-free redacted diagnostic archives.
- Persistent Provider plugin enable/disable preferences with impact preview, compatibility
  diagnostics, package hashes, isolated rescan, and non-destructive re-enable behavior.
- Read-only aggregate Query Service, keyset pagination for Tasks/Request Logs/Artifacts,
  `20260715_0006` query indexes, a 100k Task / 1m Request Log opt-in scale gate, and a
  keyboard-first global command palette.
- Data-backed Dashboard queue/artifact previews, Provider/model/currency Cost Analysis with
  explicit unknown-cost accounting, local-time day boundaries, and task terminal notifications.
- Persisted Chinese/English interface preferences, locale-aware number/currency formatting,
  reduced-motion and notification controls, plus an automated whole-shell keyboard/accessibility
  audit.
- Artifact type/time filters, metadata and lineage details, missing-file states, lazy
  content-addressed image thumbnails, and SQLite migration `20260715_0007` filter indexes.
- Cross-platform PyInstaller one-folder specification with a macOS `.app`, bundled Provider
  metadata/migrations, legal files, ComfyUI Custom Node extras, and per-file SHA-256 provenance.
- Packaged cold-start and `0006 → 0007 → 0006 → 0007` upgrade/rollback smoke gates with preserved
  data and verified pre-migration backups.
- Isolated five-wheel release validation, CycloneDX runtime SBOM, third-party license inventory,
  OSV vulnerability evidence, user/troubleshooting documentation, Provider development guidance,
  and a Beta migration/rollback policy.
- Optimistic executemany persistence for Workflow NodeRun waves, preserving atomic stale-writer
  rejection while restoring comfortable headroom for the 1,000-node coverage-instrumented gate.
- Runtime-verified credential storage that safely degrades from an unavailable OS keyring to
  process memory without plaintext persistence, while keeping the effective mode visible in the UI.
- Packaged startup evidence that requires the authenticated loopback gateway to bind successfully
  and records its non-sensitive secure-storage mode.
- Release manifest schema v2 with independent file, executable-mode, symlink-target, membership,
  and aggregate-digest verification, plus native archives that must survive an extraction round trip.
- Pinned GitHub Actions and durable candidate/evidence uploads for pull-request package smokes and
  manually or tag-triggered full native release gates on macOS, Windows, and Linux.
- Screenshot-driven whole-product UI audit with Provider-aware dashboard actions, responsive
  scrolling settings cards, honest disabled actions, dark native controls, and WCAG-AA contrast
  headroom for small secondary text across the three dark surfaces.
- Complete English localization for all 11 production pages, the Workflow editor, Provider and
  ComfyUI dialogs, shared feedback states, queue/tray copy, and plugin-owned Schema forms, including
  the non-validating `x-astraweft-i18n` Provider extension and automated fixed-Chinese regression
  coverage.
- Developer ID build inputs and a secret-safe macOS notarization finalizer that requires hardened
  runtime, secure timestamps, stapling and Gatekeeper acceptance before re-hashing the exact final
  payload into the promotable manifest and archive.
