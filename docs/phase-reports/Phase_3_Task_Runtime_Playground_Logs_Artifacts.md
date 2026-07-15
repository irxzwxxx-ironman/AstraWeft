# Phase 3 Task Runtime、Playground、日志与产物验收报告

- 项目：AstraWeft（星纬）
- 日期：2026-07-15
- 状态：LOCAL PASS
- 范围：macOS 本地 Phase 3 完整门禁；Git 与远端 Windows/Linux CI 按用户要求延后

## 1. 阶段交付

### 1.1 Task Domain 与数据层

- 完整 Task 状态机覆盖创建、排队、提交、运行、轮询、重试等待、取消、恢复、成功、失败、超时和人工确认，所有迁移经过 Domain 守卫。
- Task、Task Attempt、Request Log、Artifact 实体及不可变业务约束。
- Alembic revision `20260715_0003` 创建四类持久化记录、约束和列表索引。
- Task `row_version` 和 Attempt 期望状态比较实现乐观并发控制。
- 未知用量或成本保存为 `NULL`，不伪造为零。

### 1.2 Durable Task Runtime

- 创建时先验证 Model JSON Schema；外部调用前持久化 Task 与 RUNNING Attempt，网络期间不持有数据库事务。
- 每个 Task 使用稳定 idempotency key，所有安全重试复用同一个 key。
- Worker、PollingCoordinator 与 RuntimeCoordinator 提供持久优先级、全局并发和 Provider 声明并发限制。
- 同步完成、异步 accepted/poll、429/5xx 重试、取消、超时、Request Log 和产物落盘形成统一执行路径。
- 重启恢复已有远程 ID 的任务时只轮询；不支持幂等或身份不完整的任务进入 `NEEDS_ATTENTION`。
- 关闭流程先等待短 SQLite 操作退出，再在有界时间后取消长动作；中断意图由下一次启动恢复。

### 1.3 Mock Provider 故障与恢复模型

- Mock 远程任务状态原子保存到插件私有 `remote-tasks.json`，新进程和新 client 可继续轮询，不依赖 Core 识别 Mock ID。
- Mock 覆盖同步结果、异步任务、持久远程身份、失败、限流、取消和认证语义。
- 插件 filesystem 权限声明收紧为仅 `plugin_data`。

### 1.4 产物与可观测性

- 本地 Artifact Writer 支持文本、JSON 和 Base64；使用 `.partial`、大小检查、SHA-256 与原子替换。
- 产物相对路径受根目录 containment 检查，Task/Artifact ID 不能逃逸数据目录。
- Request Log 只保存输入字段名和类型，不保存 prompt 或凭据值；记录安全响应摘要、标准错误、耗时、用量和已知成本。
- Core wheel 打包规则已验证包含新增 `infrastructure/artifacts` 模块。

### 1.5 GUI 垂直切片

- Playground：Provider/Model 选择、Schema 动态参数、持久任务提交和结果摘要。
- Task Center：最近 1000 个任务、状态、进度、Attempt 详情和取消入口。
- 调用日志：最近 1000 条脱敏记录、错误和成本；成本 `NULL` 精确显示“未知”。
- 产物库：已校验路径、大小、SHA-256、来源 Task 和打开本地目录。
- Dashboard：今日调用、成功率、已知/未知成本、运行中任务和 Provider 状态，全部来自真实本地数据。

## 2. 验收证据

主验证环境：macOS arm64，Python 3.12.13，Qt/PySide6 6.10.3。

| 门禁 | 结果 | 证据摘要 |
|---|---|---|
| Ruff lint / format | PASS | 135 个 Python 文件格式一致，0 lint |
| mypy strict | PASS | 132 个源码与测试文件，0 issues |
| import-linter | PASS | 136 个文件、531 条依赖；5 个契约保持，0 broken |
| pytest | PASS | 139 passed |
| coverage | PASS | 90.27%，高于 90% 门槛 |
| Task/Recovery 集成 | PASS | 同步、异步、重试、取消、超时、无重复提交和人工确认路径全绿 |
| GUI 垂直切片 | PASS | Playground → Task → Log → Artifact 使用同一真实本地数据闭环 |
| 大数据 GUI 门禁 | PASS | 1000 个等待 Task + 100,000 条 Request Log；两页各只装载 1000 行且均低于 3 秒 |
| 极速关闭回归 | PASS | 25ms 启动后关闭连续 5 轮通过，SQLite worker 无事件循环销毁竞态 |
| secret canary | PASS | 本地 SQLite 与 JSONL 对已知 canary 为 0 matches |
| dependency audit | PASS | 第三方依赖无已知漏洞；3 个本地未发布包按预期跳过 PyPI 查询 |
| SDK / Mock / Core 构建 | PASS | 3 个 sdist + 3 个 wheel；Twine 与 wheel contents 全绿 |
| isolated wheel smoke | PASS | 安装 wheel、发现插件、迁移至 `20260715_0003`、启动 GUI 并受控退出 |

## 3. 退出标准映射

| Phase 3 退出标准 | 结论 |
|---|---|
| Mock 同步/异步任务端到端成功 | PASS |
| 应用重启不重复提交可恢复任务 | PASS；已有 remote ID 只 poll，安全重提复用 idempotency key |
| 用户可解释失败阶段并执行正确下一步 | PASS；Task 状态、Attempt、标准错误和 NEEDS_ATTENTION 语义可见 |
| 1000 等待任务、10 万历史记录时 UI 保持响应 | PASS；固定数据集 GUI 门禁 |
| 成本未知显示未知而不是 0 | PASS；数据库 NULL + GUI“未知”测试 |

## 4. 代码审查发现并修复

- qasync 事件循环尚未运行时直接 `asyncio.create_task` 会启动失败：统一从已安装事件循环创建后台任务。
- 极速退出会取消正在进行的 aiosqlite 查询并在 Qt signal bridge 销毁后回调：增加有界优雅收尾，再取消长动作，并用连续进程测试固定。
- 同一事务写父 Task/Attempt 与子记录时，SQLAlchemy 可能缺少确定 flush 顺序：Repository 在依赖写入前显式 flush。
- 初版 Core wheel 因通用 `artifacts/` ignore 规则漏掉源码目录：把运行目录 ignore 限定为仓库根，并以隔离 wheel 启动回归验证。
- Qt offscreen 环境的系统 FixedFont 返回不存在的 `Monospace` 别名：改为从已安装字体选择平台等宽字体。
- 恢复中的 RUNNING Attempt 初版可能保持未结束：在恢复前以 `process_interrupted` 关闭，保留可解释时间线。

## 5. 明确延后范围

- Git 提交、远端仓库和 GitHub Actions 实际执行继续按用户要求延后。
- Windows 与 Linux 尚无真实 OS 执行证据；Windows 同步 CI、Linux Beta 前纳入的政策不变。
- 真实 Provider、Core 受控 HTTP transport、URL 产物下载与临时 URL 保护属于 Phase 4。
- 开发态 GUI 是直接 Python 进程，系统级视觉连接器不能把它识别为独立 `.app`；本阶段以真实窗口启动、pytest-qt 页面交互和隔离 wheel offscreen 生命周期验收。正式安装包视觉检查在桌面打包阶段再次执行。
- Task Center/Logs 当前按固定上限装载最近 1000 条；完整游标分页、保留策略配置和 10 万 Task/100 万 Log 优化属于 Phase 7。

## 6. 阶段结论

Phase 3 在 macOS 本地范围内达到退出标准。AstraWeft 已具备第一个可运行的创作垂直切片，可进入 Phase 4 的真实 Provider 设计与接入；在调用任何真实付费 API 前，必须先基于当时官方文档确定 adapter、费用上限和 live-test 开关。远端和跨操作系统状态继续保持“待验证”。
