# Phase 0 工程与开源基线验收报告

- 项目：AstraWeft（星纬）
- 日期：2026-07-15
- 阶段：Phase 0
- 结论：本地门禁通过；远程三平台 CI 待公开/私有 GitHub 仓库建立后执行

> 后续决策：用户于 2026-07-15 明确要求暂不处理 Git、优先本地运行。因此远端门禁保持待验，但不再阻止后续阶段的本地实现与本地验收。

## 1. 阶段目标

Phase 0 只建立长期工程基础，不实现 Provider、Task、Workflow、ComfyUI、数据库或完整 GUI 功能。

验收范围：

- 正式名称、许可证、平台与架构决策。
- Git 仓库和开源治理文件。
- Python 3.12+ `src` package 与最小进程入口。
- 可锁定依赖、静态检查、严格类型、架构约束和测试。
- sdist/wheel 构建与隔离安装 smoke。
- macOS/Windows/Linux CI 定义。

## 2. 已完成交付

### 2.1 名称与文档迁移

- 本地目录从 `LingWeave/` 迁移为 `AstraWeft/`。
- 正式显示名为 `AstraWeft（星纬）`。
- Python distribution/import package 为 `astraweft`。
- Provider entry point 规范统一为 `astraweft.providers`。
- 旧名称只保留在 ADR-000 的决策背景和引用中。

### 2.2 开源治理

- Apache License 2.0 和 NOTICE。
- README、CONTRIBUTING、SECURITY、CODE_OF_CONDUCT、CHANGELOG。
- Pull Request 模板、Bug/Feature Issue 表单、Dependabot 配置。
- Git 主分支初始化为 `main`，IDE、环境、构建、运行数据和密钥文件已忽略。

### 2.3 工程结构

- Python 3.12+，`src/astraweft` layout。
- `application/domain/ports/infrastructure/presentation/bootstrap` 分层包。
- 最小 `python -m astraweft` 与 `astraweft --version` 入口。
- `packages/` 为未来 Provider SDK 保留。
- `plugins/` 明确 Mock-first 的真实实现顺序。
- `tests/` 按 unit/contract/integration/gui/fixtures 分类。

### 2.4 依赖与构建

- uv 0.11.x 版本约束与 `uv.lock`。
- Hatchling 构建后端。
- Ruff、mypy strict、pytest/coverage、import-linter、pip-audit。
- sdist 与 universal wheel 构建。

### 2.5 决策记录

Accepted ADR：

- ADR-000：AstraWeft 名称与技术标识。
- ADR-001：分层模块化单体。
- ADR-002：PySide6 + qasync。
- ADR-003：SQLite + SQLAlchemy 2 + Alembic。
- ADR-004：OS Keychain 凭据策略。
- ADR-005：Provider Plugin API 与可信插件模型。
- ADR-006：JSON Schema 2020-12 + UI Schema。
- ADR-007：Apache-2.0 与平台支持政策。

## 3. 自动验证证据

本地环境：macOS arm64，Python 3.12.13。

| 检查 | 结果 | 证据摘要 |
|---|---|---|
| uv lock | PASS | 79 packages resolved，lock 状态有效 |
| Ruff lint | PASS | All checks passed |
| Ruff format | PASS | 12 Python files formatted |
| mypy strict | PASS | 12 source files，0 issues |
| import-linter | PASS | 3 contracts kept，0 broken |
| pytest | PASS | 5 passed |
| coverage | PASS | 100%，门槛 90% |
| pre-commit pre-push | PASS | 5 hooks passed |
| package build | PASS | sdist + wheel |
| Twine metadata | PASS | 两个 distribution 均通过 |
| wheel contents | PASS | wheel OK，含 LICENSE/NOTICE |
| isolated wheel smoke | PASS | 1 passed |
| isolated sdist smoke | PASS | 1 passed |
| pip-audit | PASS | No known vulnerabilities；本地项目自身按预期跳过 |
| secret-like pattern scan | PASS | 未发现 AWS/OpenAI/private-key 样式秘密 |
| Markdown local links/fences | PASS | 无断链，无奇数 code fence |

## 4. 代码审查结论

### 4.1 通过项

- 分层依赖已由可执行 contract 固定，未发现跨层导入。
- Phase 0 入口没有 GUI、网络、数据库或 Provider 假实现。
- 锁文件、Python 基线和 CI 使用一致。
- 构建包只包含预期源码、metadata、LICENSE 和 NOTICE。
- `.idea`、`.venv`、coverage、dist、本地数据库、日志、产物和常见密钥文件不进入 Git。
- 文档和技术命名空间已迁移到 AstraWeft。

### 4.2 发现并修复

- 本机系统 Python 为 3.9：改用项目隔离的 Python 3.12.13，不修改系统 Python。
- uv 配置最初在两个位置重复：删除 `pyproject.toml` 重复项，仅保留 `uv.toml`。
- 初始入口覆盖率为 94%：增加真实 module-entrypoint 单测，提升为 100%。
- 初次 pip-audit 因代理握手超时失败：使用 60 秒超时重试并通过；CI 同步调整超时。
- 初始公开名 LingWeave 已有冲突：用户确认迁移为 AstraWeft，并固化 ADR-000。

## 5. 尚未证明的门禁

### 5.1 GitHub 三平台执行

CI 已定义以下任务，但当前没有 GitHub remote，无法把配置存在等同于执行通过：

- macOS、Windows、Ubuntu smoke。
- Python 3.12、3.13 compatibility。
- Ubuntu quality、dependency audit 与 package build。

当前仓库也没有配置 Git author name/email，且本机没有 GitHub CLI。因此未虚构身份、未创建本地初始提交，也未进行任何外部推送。

在 GitHub 仓库创建、提交并实际全绿前，Phase 0 只能获得“本地通过/远程待验”的结论。根据后续用户决策，本地 Phase 1 可以继续，但不得把本地结果表述为远端或三平台已通过。

### 5.2 正式发布基础设施

以下不属于 Phase 0 完成范围：

- PyInstaller 桌面包、macOS 公证、Windows 代码签名。
- SBOM/provenance 正式发布工作流。
- PyPI 发布与自动更新。
- GUI 视觉验收。

它们分别在 Phase 1、Phase 7、Phase 8 建立对应门禁。

## 6. 下一步

1. 本地开发按阶段门禁继续推进。
2. 用户恢复 Git 工作后，创建或指定 AstraWeft GitHub 仓库。
3. 提交基线、推送 `main` 并运行全部 CI jobs。
4. 根据 macOS、Windows、Ubuntu 实际结果处理平台差异。
5. CI 全绿后再把远端状态更新为 `PASS`。
