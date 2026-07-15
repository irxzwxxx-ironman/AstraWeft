# Public packages

This directory is reserved for independently versioned public packages that must not import
AstraWeft Core internals.

Current package:

- `astraweft-provider-sdk` — stable Provider protocols, DTOs, exceptions, and contract-test kit.

Phase 2 ships the SDK as its own wheel with immutable DTOs, async protocols, standardized safe
errors, manifest parsing, JSON Schema checks, and a reusable Provider contract suite. Import-linter
proves that it does not depend on Core, plugins, Qt, or SQLAlchemy.
