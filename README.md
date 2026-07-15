# AstraWeft（星纬）

Local-first desktop AI provider, task, and workflow manager.

> Project status: pre-alpha. Phase 0–7 have passed their macOS local gates. Phase 8 packaging,
> clean-machine installation, and cross-platform release work remain before public beta.

## Vision

AstraWeft brings cloud AI providers, long-running generation tasks, reproducible workflows,
ComfyUI, artifacts, logs, and cost visibility into one local desktop workspace.

The product is designed around six commitments:

- Local-first data and credential control.
- A modern Dark Cyber AI desktop experience built with PySide6.
- Provider and model capabilities exposed through stable plugin contracts.
- Recoverable asynchronous tasks that avoid accidental duplicate billing.
- Immutable, reproducible workflow versions and artifact lineage.
- Cross-platform, maintainable, open-source distribution.

## Current phase

Phase 0 through Phase 7 have passed their macOS local gates; Phase 8 is next.
Remote Git hosting and cross-OS CI
execution remain explicitly deferred. AstraWeft runs Mock, OpenAI, Runway and ComfyUI work through
durable local ledgers, keeps credentials in the OS keyring, and exposes Provider tasks to ComfyUI
through a token-protected `127.0.0.1` gateway. Product hardening is tracked in Phase 7 of the
[product roadmap](docs/Product_Implementation_Roadmap.md).

## Documentation

- [Architecture v2](docs/Local_AI_Workflow_Manager_Architecture_v2.md)
- [Architecture review and gap analysis](docs/Architecture_Review_and_Gap_Analysis.md)
- [Product implementation roadmap](docs/Product_Implementation_Roadmap.md)
- [Detailed technical design](docs/Local_AI_Workflow_Manager_Detailed_Technical_Design.md)
- [Database ER design](docs/Local_AI_Workflow_Manager_Database_ER_Design.md)
- [GUI prototype design](docs/Local_AI_Workflow_Manager_GUI_Prototype_Design.md)
- [Provider plugin specification](docs/Local_AI_Workflow_Manager_Provider_Plugin_Interface_Spec.md)
- [Architecture decision records](docs/adr/README.md)
- [Phase 1 local gate report](docs/phase-reports/Phase_1_Local_Runnable_Foundation.md)
- [Phase 2 local gate report](docs/phase-reports/Phase_2_Provider_Model_Loop.md)
- [Phase 3 local gate report](docs/phase-reports/Phase_3_Task_Runtime_Playground_Logs_Artifacts.md)
- [Phase 4 local gate report](docs/phase-reports/Phase_4_Real_Provider_Integration.md)
- [Phase 5 local gate report](docs/phase-reports/Phase_5_Workflow_Engine.md)
- [ComfyUI integration design](docs/comfyui/ComfyUI_Integration_Implementation_Design.md)
- [Phase 6 local gate report](docs/phase-reports/Phase_6_ComfyUI_Integration.md)
- [Phase 7 local data maintenance design](docs/operations/Local_Data_Maintenance_Implementation_Design.md)
- [Phase 7 product hardening gate report](docs/phase-reports/Phase_7_Product_Hardening.md)

## Development

Requirements:

- Python 3.12 or 3.13
- uv 0.11.x
- Git

```bash
uv sync --locked --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run pytest --cov --cov-report=term-missing
```

Start the local desktop application:

```bash
uv run astraweft
```

Use an isolated data root while developing or testing:

```bash
uv run astraweft --data-dir ./build/local-data
```

Print the installed version without opening the GUI:

```bash
uv run astraweft --version
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a change.

## Supported platforms

- macOS: primary development platform.
- Windows: CI is defined; execution is deferred until Git hosting is enabled.
- Linux: architecture-compatible now; full beta support is required before public beta.

## Security and privacy

Do not place API keys, signing credentials, personal data, or production request payloads in issues,
logs, fixtures, or commits. See [SECURITY.md](SECURITY.md) for reporting and support policy.

## License

Licensed under the [Apache License 2.0](LICENSE).
