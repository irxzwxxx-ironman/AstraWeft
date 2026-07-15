## Outcome

<!-- Lead with the user or engineering outcome. -->

## Scope

<!-- What changed, and what intentionally did not change? -->

## Architecture and compatibility

- [ ] Dependency direction remains valid (`lint-imports` passes).
- [ ] Public interfaces, database schemas, workflow formats, and plugin compatibility are unchanged,
      or the required ADR/migration is included.
- [ ] No Provider-specific branch was added to Core or GUI.

## Risk and rollback

<!-- Include duplicate-billing, secret, migration, async, cross-platform, and data-loss risks. -->

## Verification

- [ ] Ruff lint and format checks pass.
- [ ] mypy strict passes.
- [ ] Relevant unit, contract, integration, and GUI tests pass.
- [ ] New or changed behavior has regression coverage.
- [ ] Logs, fixtures, screenshots, and diagnostics contain no secrets or private data.
- [ ] User-facing changes include screenshots and loading/empty/error/keyboard states.

<!-- Paste concise commands/results or link CI evidence. -->
