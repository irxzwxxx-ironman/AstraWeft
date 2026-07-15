# Phase 2 Provider SDK、Provider 与 Model 管理闭环验收报告

- 项目：AstraWeft（星纬）
- 日期：2026-07-15
- 状态：LOCAL PASS
- 范围：macOS 本地 Phase 2 完整门禁；Git 与远端 Windows/Linux CI 按用户要求延后

## 1. 阶段交付

### 1.1 独立 Provider SDK 与插件边界

- `astraweft-provider-sdk` 作为独立 wheel：不可变 DTO、异步 Protocol、标准安全错误、manifest 解析、JSON Schema 2020-12 验证和 contract test kit。
- Provider 通过 `astraweft.providers` entry point 注册，静态 `plugin.toml` 在导入插件代码前完成 API、Python、声明一致性和包指纹检查。
- 单插件导入失败、版本不兼容和 plugin ID 冲突均被隔离，不阻止 Core 启动。
- SDK 禁止依赖 Core/插件/Qt/SQLAlchemy；Mock 插件禁止依赖 Core/Qt/SQLAlchemy/keyring。
- `SecretResolver.get` 修订为异步接口，并由 [ADR-008](../adr/ADR-008_Provider_SDK_Packaging_and_Async_Secrets.md) 固化。

### 1.2 Mock Provider

- `astraweft-mock-provider` 作为独立 wheel，无网络、文件系统和子进程权限。
- 覆盖同步完成、异步 accepted、轮询成功、取消、幂等 task ID 和幂等关闭。
- 覆盖认证失败、429、不可用、超时、协议错误和远端任务失败。
- 提供两版模型目录，验证远端模型新增、消失和稳定 remote ID。

### 1.3 Provider / Credential / Model 核心闭环

- Provider、CredentialMetadata、Model Domain 实体与不可变 JSON 值。
- Alembic migration 创建 `provider_credentials`、`providers` 和 `models`；Provider 名称与远端模型 ID 唯一约束生效。
- Create、Edit、Enable、Delete、Test、Sync 与模型用户偏好用例。
- 插件调用期间不持有数据库事务；短事务完成读取和同步结果落库。
- API Key 只进入 SecretStore；SQLite 只保存 `credential_ref`、类型、字段名与脱敏 hint。
- 模型同步保留用户显示名、默认参数和启用状态；远端消失模型保留历史并标记 unavailable/deprecated。
- 数据库提交成功但提交后事件失败时，不回滚已提交事实，也不误删新凭据。

### 1.4 GUI

- Provider 页面：插件可用/隔离摘要、添加、编辑、启停、删除、测试连接、同步模型和非阻塞 Toast。
- Provider 对话框完全由 descriptor 的 settings/credential Schema 生成，不按插件 ID 分支。
- SchemaForm 支持 string、secret、enum、integer、number、boolean、textarea、默认值、顺序提示和字段级验证。
- 模型页：Provider 筛选、可用/用户启用状态、远端下线提示，以及参数 Schema、输出 Schema、能力和价格详情面板。

## 2. 验收证据

主验证环境：macOS arm64，Python 3.12.13，Qt/PySide6 6.10.3。

| 门禁 | 结果 | 证据摘要 |
|---|---|---|
| Ruff lint / format | PASS | 108 个 Python 文件格式一致，0 lint |
| mypy strict | PASS | 105 个源码与测试文件，0 issues |
| import-linter | PASS | 117 个文件、374 条依赖；5 个契约保持，0 broken |
| pytest | PASS | 99 passed |
| coverage | PASS | 90.19%，高于 90% 门槛 |
| Provider contracts | PASS | manifest/descriptor、Schema、health、models、close 与 Mock 扩展路径全绿 |
| 插件故障隔离 | PASS | 导入失败与不兼容 manifest 不影响其他插件发现 |
| 重启恢复 | PASS | Provider/Model 在新 AppContext 中恢复并可重新测试连接 |
| secret canary | PASS | 所有测试/视觉/本地数据目录中的 SQLite 与 JSONL 为 0 matches |
| SDK / Mock / Core 构建 | PASS | 3 个 sdist + 3 个 wheel；check-wheel-contents 与 twine check 全绿 |
| Python 3.12 wheel smoke | PASS | 隔离安装、Mock entry point 发现、建库、GUI 启动和受控退出 |
| Python 3.13 wheel smoke | PASS | Python 3.13.14 隔离安装、插件发现、GUI 启动和受控退出 |
| pip-audit | PASS | 第三方依赖无已知漏洞；3 个本地未发布包按预期跳过 PyPI 查询 |
| 视觉验收 | PASS | 1440×900 Provider 与 Model 主从详情页已检查 |

视觉证据：

- `build/visual-qa/phase2-provider-final-v3.png`
- `build/visual-qa/phase2-models-final.png`

## 3. 代码审查发现并修复

- Application 初版直接调用 Infrastructure 的 ProviderContext 构造器：提升为 `ProviderContextFactory` 端口，恢复依赖方向，import-linter 从 broken 恢复为全绿。
- 原规格把 SecretResolver 定义为同步调用：改为 async，避免 Keyring 阻塞 qasync GUI 线程，并新增 ADR-008。
- Provider 包指纹初版只覆盖 manifest/metadata：改为覆盖插件包全部源文件并排除缓存字节码。
- 插件数据目录的可读名称可能碰撞：增加 plugin ID SHA-256 后缀。
- 模型表在绑定模型前设置列宽，视觉验收出现半宽表头：改为绑定后设置 resize mode，并补充右侧详情面板。
- Provider 凭据运行时引用初版可由 ID 重建：改为从 CredentialMetadata 读取真实 `credential_ref`，避免未来引用格式迁移破坏旧数据。
- 提交后事件失败会被普通异常补偿逻辑误判：新增端口级 `PostCommitDispatchError`，保持 DB/SecretStore 一致。
- 动态页面在无 asyncio loop 的测试环境可能遗留未 await coroutine：安全关闭未调度协程，生产 qasync 行为不变。

## 4. 退出标准映射

| Phase 2 退出标准 | 结论 |
|---|---|
| Core 不知道 Mock 插件 ID 也能完成闭环 | PASS；Core 源码零 Mock ID，插件由 entry point/descriptor 驱动 |
| 重启后 Provider/Model 恢复且密钥不在 DB | PASS；重启集成测试与全数据 canary 扫描 |
| 插件导入失败不阻止应用启动 | PASS；失败/不兼容/冲突隔离测试 |
| Model 同步保留显示名和默认参数 | PASS；目录 revision 1 → 2 集成测试 |
| Provider SDK contract suite 全绿 | PASS |
| GUI 具备动态参数表单预览 | PASS；SchemaForm 组件与 pytest-qt 测试 |

## 5. 延后但未虚构为通过的范围

- Git 提交、远端仓库和 GitHub Actions 实际执行继续按用户要求延后。
- Windows 与 Linux 尚无真实 OS 执行证据；Windows 同步 CI、Linux Beta 前纳入的政策不变。
- Core HTTP transport 和第一个真实网络 Provider 尚未实现；Mock Provider 明确不需要网络。
- Task Runtime、Playground 请求提交、请求日志、产物下载与恢复属于 Phase 3。
- 插件权限当前是可信本机代码的声明与审计边界，不是 OS 级安全沙箱。

## 6. 阶段结论

Phase 2 在 macOS 本地范围内达到退出标准，可以进入 Phase 3 的 Task Runtime、Playground、调用日志与产物最小创作闭环。远端和跨操作系统状态继续保持“待验证”。
