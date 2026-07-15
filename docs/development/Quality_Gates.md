# AstraWeft 质量门禁

## 本地必过命令

```bash
uv sync --locked --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run pytest --cov=astraweft --cov-report=term-missing
uv run pip-audit --timeout 60 --progress-spinner off
uv build
uv run twine check dist/*
uv run check-wheel-contents dist/*.whl
uv run python scripts/verify_release_manifest.py --dist-dir dist/desktop \
  --archive-dir build/phase8/release-artifacts
uv run python scripts/run_local_release_gate.py
```

## CI 门禁

- Ubuntu：Lint、格式、严格类型、架构依赖、完整测试与覆盖率、依赖漏洞审计。
- Packaging：sdist/wheel 构建、metadata、wheel 内容与隔离安装 smoke。
- Cross-platform：macOS、Windows、Linux 的入口和测试 smoke。
- Python compatibility：Python 3.12 与 3.13。

CI 全绿只证明当前检查覆盖的范围。数据库恢复、重复计费、密钥泄露、UI 可访问性和安装包行为必须在对应阶段用专门测试证明，不能从基础 smoke 推断。

`run_local_release_gate.py` 只证明执行它的原生平台。macOS 结果不得替代 Windows/Linux 构建、
签名、安装、升级、卸载和凭据存储验证。

打包冷启动不仅检查窗口与数据库，还必须看到 `loopback_gateway_ready`，并记录
`secure_storage_persistent`。未签名开发包允许安全降级为进程内会话存储；正式签名候选包必须另行
证明系统密钥环可持久读写，不能用会话降级结果替代。

release manifest 必须独立验证普通文件的 SHA-256/尺寸/执行位、符号链接目标、完整成员集合和聚合
摘要。上传候选前必须生成平台原生归档并完成一次解包后复核；只生成 manifest 或只校验压缩包外层
SHA-256 都不足以证明内容完整。

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
