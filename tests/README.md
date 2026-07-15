# Test organization

- `unit/` — framework-independent business rules and focused process behavior.
- `contract/` — stable interfaces shared by Provider and external adapters.
- `integration/` — database, files, HTTP, Keyring fakes, process entry points, and recovery boundaries.
- `gui/` — PySide6 components, ViewModels, keyboard behavior, and application lifecycle.
- `fixtures/` — synthetic, non-secret test data only.
- `smoke_test.py` — isolated wheel and source-distribution import/entry-point verification.

Tests must not access a developer's real credential store, paid Provider account, production files, or
network unless explicitly marked and manually enabled. Live Provider smoke tests use separate bounded
credentials and are never part of an ordinary pull request.
