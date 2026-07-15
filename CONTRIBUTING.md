# Contributing to AstraWeft

Thank you for helping build a dependable local-first AI desktop tool. AstraWeft values maintainable
changes over rapid feature accumulation.

## Before you start

- Read the [architecture v2](docs/Local_AI_Workflow_Manager_Architecture_v2.md),
  [architecture review](docs/Architecture_Review_and_Gap_Analysis.md), and
  [implementation roadmap](docs/Product_Implementation_Roadmap.md).
- Check the relevant [architecture decision records](docs/adr/README.md).
- Discuss broad architectural changes before writing a large patch.
- Never include real API keys, user payloads, temporary signed URLs, or production data in a change.

## Development setup

Install Python 3.12+, Git, and uv 0.11.x, then run:

```bash
uv sync --locked --dev
uv run pre-commit install --install-hooks
```

Verify the checkout:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run pytest --cov --cov-report=term-missing
uv build
uv run twine check dist/*
```

## Architecture rules

- Domain code is framework-independent and cannot import Qt, SQLAlchemy, HTTP clients, or adapters.
- Application code orchestrates commands and queries, but cannot import presentation or concrete
  infrastructure modules.
- Ports define stable interfaces; infrastructure and plugins implement them.
- Presentation code uses application DTOs and use cases, never ORM sessions or Provider SDK internals.
- Provider plugins depend only on the public Provider SDK. Core and GUI cannot branch on plugin IDs.
- Network requests and long-running disk work cannot block the Qt main thread.
- A remote submit must be persisted and idempotent before the system can claim crash safety.

The executable architecture contracts are in `pyproject.toml` and are checked by `lint-imports`.

## Change workflow

1. Keep each change focused. Separate mechanical refactors from behavior changes.
2. Add or update tests at the layer where the behavior belongs.
3. Add an Alembic migration for every database schema change; never rewrite a released migration.
4. Update documentation, examples, error messages, and changelog entries where applicable.
5. Run the complete local quality suite before opening a pull request.
6. Include risk, rollback, testing evidence, and screenshots for user-facing changes.

## Testing expectations

- Domain and application rules require focused unit tests and meaningful branch coverage.
- Provider implementations must pass the shared contract suite.
- Infrastructure changes require temporary-database, mock-server, or temporary-filesystem tests.
- GUI changes require ViewModel tests and keyboard/loading/empty/error-state evidence.
- Bug fixes begin with a failing regression test whenever practical.
- A green narrow test is not evidence for an untested recovery, migration, security, or platform claim.

## Pull request review

Reviewers check dependency direction, state transitions, crash recovery, duplicate-billing risk, secret
handling, asynchronous boundaries, cross-platform behavior, public API compatibility, and user-facing
error quality. High-risk changes may require a fault-injection or migration rehearsal before merge.

## Public interfaces and ADRs

Changes to the Provider Plugin API, workflow format, loopback API, database compatibility policy,
application identity, or core architecture require an ADR. Do not silently encode a new architectural
exception in implementation code.

## Licensing

By submitting a contribution, you agree that it is licensed under the Apache License 2.0 and that you
have the right to contribute it. Third-party code and assets must include provenance and a compatible
license; copied snippets without clear licensing will not be accepted.
