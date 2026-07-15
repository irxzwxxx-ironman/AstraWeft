# AstraWeft 质量门禁

## 本地必过命令

```bash
uv sync --locked --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run pytest --cov=astraweft --cov-report=term-missing
uv run pip-audit --timeout 60 --progress-spinner off
uv build
uv run twine check dist/*
uv run check-wheel-contents dist/*.whl
```

## CI 门禁

- Ubuntu：Lint、格式、严格类型、架构依赖、完整测试与覆盖率、依赖漏洞审计。
- Packaging：sdist/wheel 构建、metadata、wheel 内容与隔离安装 smoke。
- Cross-platform：macOS、Windows、Linux 的入口和测试 smoke。
- Python compatibility：Python 3.12 与 3.13。

CI 全绿只证明当前检查覆盖的范围。数据库恢复、重复计费、密钥泄露、UI 可访问性和安装包行为必须在对应阶段用专门测试证明，不能从基础 smoke 推断。

## 覆盖率政策

全局最低门槛为 90%。Domain 和 Application 的状态机、策略与验证规则应接近完整分支覆盖。覆盖率不替代边界、性质、故障注入和迁移测试。

## 阶段结束审查

每阶段必须提供：

1. 需求与验收项逐条证据。
2. 代码审查结果和未解决风险。
3. 自动测试与手工验证命令/结果。
4. 数据、API、插件和跨平台兼容影响。
5. 文档、ADR、迁移与回滚状态。
6. 下一阶段允许进入或需要返工的明确结论。
