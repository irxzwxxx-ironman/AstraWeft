# AstraWeft 产品级实施计划

> 基线：Architecture v2 + 架构评审 + 详细技术设计 + ER + GUI 原型 + Provider 插件规范  
> 当前状态：Phase 0–7 macOS 本地门禁通过（远端跨平台 CI 延后）；下一阶段为 Phase 8  
> 目标：成熟、可维护、可扩展、可跨平台发布的开源桌面 AI 工具

## 1. 实施策略

不按“先把所有页面画出来，再补后端”的方式推进，也不按“先把三个 Provider SDK 直接接进页面”的方式推进。采用纵向切片：每个阶段交付一条从 Domain、Application、Infrastructure 到 GUI 的可验证闭环，并在阶段末完成代码审查、自动化测试和跨平台冒烟。

核心策略：

- 架构先行：稳定边界先于真实 Provider。
- Mock 先行：用可控 Mock Provider 验证成功、失败、限流、异步和恢复。
- Domain 先行：状态机、DAG、Schema 和错误模型先写测试后接 UI。
- 设计系统先行：Modern Dark UI 通过可复用组件实现，不靠页面散落 QSS。
- 风险前置：密钥泄露、重复计费、数据库迁移、崩溃恢复在 MVP 前验证。
- 每阶段可退出：任何阶段结束时主分支应可运行、可测试、可打包，不留下长期半成品。

## 2. 目标架构与依赖方向

```text
Presentation (PySide6 Views / ViewModels / Design System)
        ↓
Application (Commands / Queries / Use Cases / DTO)
        ↓
Domain (Provider / Model / Task / Workflow / Artifact)
        ↑
Ports (Repository / Provider SDK / Secret / Storage / Clock)
        ↑
Infrastructure (SQLite / Keyring / HTTP / Files / ComfyUI)
        ↑
Plugins (Mock / OpenAI / Runway / future providers)
```

依赖规则：

- Domain 不依赖 Qt、SQLAlchemy、httpx 或 Provider SDK 实现。
- Presentation 不访问 ORM，不导入具体 Provider。
- 插件只依赖公开 Provider SDK，不依赖 Core 私有包。
- Infrastructure 实现 Ports，不向 Domain 泄露外部对象。
- 业务事务在 Application 层结束；网络请求期间不持有数据库事务。

## 3. 建议仓库结构

```text
AstraWeft/
├── .github/
│   ├── workflows/
│   ├── ISSUE_TEMPLATE/
│   └── PULL_REQUEST_TEMPLATE.md
├── docs/
│   ├── adr/
│   ├── architecture/
│   ├── development/
│   └── user-guide/
├── src/astraweft/
│   ├── bootstrap/
│   ├── presentation/
│   │   ├── design_system/
│   │   ├── pages/
│   │   ├── widgets/
│   │   └── viewmodels/
│   ├── application/
│   │   ├── commands/
│   │   ├── queries/
│   │   ├── services/
│   │   └── dto/
│   ├── domain/
│   │   ├── provider/
│   │   ├── model/
│   │   ├── task/
│   │   ├── workflow/
│   │   └── artifact/
│   ├── ports/
│   ├── infrastructure/
│   │   ├── database/
│   │   ├── secrets/
│   │   ├── network/
│   │   ├── storage/
│   │   └── comfyui/
│   └── resources/
├── packages/
│   ├── provider-sdk/
│   └── comfyui-custom-nodes/
├── plugins/
│   ├── mock/
│   ├── openai/
│   ├── volcengine/
│   └── kling/
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── gui/
│   └── fixtures/
├── scripts/
├── pyproject.toml
├── README.md
├── CONTRIBUTING.md
├── SECURITY.md
├── CODE_OF_CONDUCT.md
├── CHANGELOG.md
└── LICENSE
```

首期可以保留单仓库，使用 Python workspace/多个可安装 package 管理 Core、SDK 与内置插件。这样能真实验证插件只依赖公开 SDK。

## 4. 阶段路线图

## Phase 0：工程与架构基线

### 目标

建立可长期演进的仓库和质量规则，不实现业务功能。

### 工作项

- 确认项目正式名、包名、应用 ID 和开源许可证。
- 初始化 Git，清除/忽略 `.idea`、缓存、构建产物和本地数据。
- 建立 `pyproject.toml`、Python 版本和依赖锁定策略。
- 配置 Ruff、mypy、pytest、coverage、pre-commit。
- 建立 GitHub Actions：Linux 静态检查/测试，Windows/macOS 冒烟矩阵。
- 添加 README、CONTRIBUTING、SECURITY、CODE_OF_CONDUCT、CHANGELOG。
- 建立 ADR 模板，记录重大技术决策。
- 把 v2 与配套设计文档整理成统一索引。

### 需要确认的 ADR

- ADR-001：模块化单体与分层依赖。
- ADR-002：PySide6 + qasync。
- ADR-003：SQLite + SQLAlchemy + Alembic。
- ADR-004：OS Keychain 凭据策略。
- ADR-005：Provider Plugin API 与可信插件模型。
- ADR-006：JSON Schema + UI Schema。
- ADR-007：开源许可证和第三方依赖政策。

### 退出标准

- 新环境执行一条标准命令可安装开发依赖并运行空测试套件。
- 三平台至少完成 Python import/启动器 smoke test。
- 静态检查和测试是受保护分支的必需检查。
- 文档链接与 ADR 索引有效。
- 无业务代码，用户评审确认后进入 Phase 1。

## Phase 1：应用基础设施与 Design System

### 目标

交付一个真正的应用骨架：可启动、可关闭、有现代暗色外壳、可配置、可迁移、可诊断，但不伪装为完整产品。

### 后端模块

- Bootstrap/Dependency Container。
- 平台目录与配置优先级。
- 结构化文件日志和 trace ID。
- SQLite engine、基础 migration、Unit of Work。
- SecretStore port 与 Keyring 实现。
- 进程内 Event Bus、Clock、ID 生成。
- 单实例锁与受控退出流程。

### UI 模块

- App Shell、Sidebar、Topbar、内容容器、状态栏。
- Design Tokens：颜色、字体、间距、圆角、阴影、动效。
- 基础组件：Button、IconButton、Input、Select、Card、Table、Tabs、Badge、Toast、Dialog、Drawer、Skeleton、Empty State、Error State。
- 深色主题为默认；浅色主题只保证架构可切换，可延后精修。
- 高 DPI、键盘焦点、最小窗口与系统“减少动态效果”。

### 测试与审查

- Bootstrap 和配置优先级单测。
- migration 从空库创建测试。
- Keyring 使用 fake backend，不在 CI 写真实系统凭据。
- Qt 页面启动/关闭、主题切换、焦点顺序冒烟。
- 截图基准只用于稳定组件，不对所有平台做像素级相等。

### 退出标准

- 冷启动显示高级暗色 App Shell，无传统原生控件拼接感。
- UI 主线程无网络/数据库长操作。
- 应用异常可生成不含秘密的诊断日志。
- SQLite 和配置目录遵循三平台规范。
- 打包 smoke 可启动并正常退出。

## Phase 2：Provider SDK、Provider 与 Model 管理闭环

### 目标

使用 Mock Provider 完成“插件发现 → 添加配置 → 安全保存凭据 → 测试连接 → 同步模型 → 动态参数表单预览”。

### Domain / SDK

- Provider、Credential metadata、Model 实体和值对象。
- Provider Plugin manifest、descriptor、client protocol、能力与错误模型。
- JSON Schema 2020-12 验证和 UI Schema 渲染约定。
- Provider contract test kit。

### Infrastructure / Application

- 插件发现、版本校验、加载失败隔离。
- Provider/Model repositories 与 migrations。
- Create/Edit/Enable/Delete/Test/Sync commands。
- 密钥只进入 Keyring；统一脱敏器和 secret canary 测试。
- Mock Provider 覆盖成功、认证失败、429、5xx、延迟和模型变化。

### UI

- Provider 列表、添加/编辑向导、连接诊断。
- Model 主从列表、Schema/能力/价格详情。
- SchemaForm 组件库及字段级验证。

### 退出标准

- Core 不知道 Mock 插件 ID 也能完成整个闭环。
- 关闭并重启应用后 Provider/Model 配置正确恢复，密钥不在 DB。
- 插件导入失败不会阻止应用启动。
- Model 同步保留用户显示名和默认参数。
- Provider SDK contract suite 全绿。

## Phase 3：Task Runtime、Playground、日志与产物

> 实施状态：2026-07-15 macOS 本地门禁通过；详见 [Phase 3 验收报告](./phase-reports/Phase_3_Task_Runtime_Playground_Logs_Artifacts.md)。

### 目标

完成第一个真正的创作垂直切片，并解决异步、重试、恢复和重复计费风险。

### Domain

- 完整 Task 状态机与状态迁移守卫。
- Task Attempt、Retry Policy、Poll Policy、Cancellation Policy。
- Artifact metadata、hash、血缘基础。
- Request Log、Usage、Cost 与标准错误。

### Application / Infrastructure

- TaskScheduler、Worker、PollingCoordinator、RecoveryService。
- 全局/Provider 并发限制和优先级队列。
- 提交前持久化、稳定 idempotency key、乐观锁。
- Artifact 下载、partial 文件、hash 校验、原子落盘。
- Request Log 脱敏摘要、分页和保留策略。
- 应用重启后恢复远程任务。

### UI

- Playground：Provider/Model 选择、动态表单、提交和结果预览。
- Task Center：筛选、状态、进度、Attempt 时间线、取消/重试。
- Logs：列表、请求/响应摘要、成本、诊断。
- Artifact 基础预览与打开文件夹。
- Dashboard 最小状态卡：Provider、运行中、成功率、已知/未知成本。

### 故障测试

- submit 前、submit 后未保存 remote ID、poll 中、下载中强杀进程。
- 429 + Retry-After、5xx、断网、超时、重复响应、临时 URL 过期。
- 不支持幂等的 Provider 进入 `NEEDS_ATTENTION`，不自动重提。
- secret canary 扫描 DB、日志、异常、导出包。

### 退出标准

- Mock 同步/异步任务端到端成功。
- 应用重启不重复提交可恢复任务。
- 用户可解释失败发生的阶段并执行正确下一步。
- UI 在 1000 个并发等待远程任务、10 万历史任务数据集下保持响应。
- 成本未知时显示未知而不是 0。

## Phase 4：真实 Provider 接入

### 目标

用真实 Provider 验证 SDK 通用性，而不是为各 Provider 向 Core 增加分支。

### 顺序

1. 一个相对简单的真实 Provider：验证认证、模型、Schema、同步产物。
2. 一个异步视频 Provider：验证 submit/poll/cancel/recovery。
3. 其余计划 Provider：验证多区域、签名认证、文件上传等差异。

具体 Provider API 在实现前必须以当时官方文档为准建立 adapter design note，不能依赖记忆中的接口。Phase 4 已以 OpenAI Responses 和 Runway async video 完成该门禁。

### 规则

- 每个 Provider 是独立 package，仅依赖 Provider SDK。
- SDK 特例通过能力声明或可选协议扩展表达。
- 禁止 `if plugin_id == ...` 出现在 Core 或 GUI。
- Live tests 使用显式环境变量、费用上限和手动触发，普通 PR 不调用真实付费 API。

### 退出标准

- 至少一个同步/即时 Provider 与一个远程异步 Provider 稳定工作。
- 全部插件通过统一 contract suite。
- 限流、错误、取消和用量映射有固定 fixture。
- Provider 官方 API 变更可只更新对应插件。

## Phase 5：Workflow Engine（macOS 本地门禁已通过）

### 目标

从单次调用升级为可复现的多节点 DAG 工作流。

### Domain

- Workflow、不可变 WorkflowVersion、Node、Edge、Port。
- DAG、端口、Schema、Provider/Model 引用发布校验。
- WorkflowRun、NodeRun 状态机和失败策略。
- 输入表达式/映射的受限安全语法；v1 不执行任意 Python。

### Application / Infrastructure

- 草稿、发布、复制、导入/导出、checksum。
- 拓扑调度、节点就绪计算、Task 委托、Artifact 传递。
- 运行快照、重试、取消和应用重启恢复。
- Workflow repository、版本 migration 和血缘查询。

### UI

- Workflow 列表、模板和版本历史。
- 节点库、画布、端口连线、右侧属性、问题面板。
- 自动保存草稿、验证、发布、试运行。
- 运行观察模式和节点级 Task/Log/Artifact 详情。

### 退出标准

- 发布版本不可修改，编辑自动生成新草稿。
- 非法环、端口不兼容、缺失输入在发布前被阻止。
- 历史运行可还原版本、解析输入、节点状态和产物血缘。
- 应用重启后运行从可恢复节点继续，不重复完成节点。

## Phase 6：ComfyUI 集成（macOS 本地门禁已通过）

### 目标

使 AstraWeft 与 ComfyUI 形成双向但边界清晰的组合。

### Adapter

- ComfyUI 实例配置、连接测试和能力/版本探测。
- HTTP `/prompt` 提交、WebSocket 进度、`/history` 结果、产物下载。
- 断线重连、远程队列 ID、工作流 JSON checksum。
- ComfyUI 节点作为 Workflow Node 类型。

### Custom Node Gateway

- Image/Video 通用 Gateway nodes。
- Loopback API 仅绑定 `127.0.0.1`。
- 随机访问令牌存 Keyring，严格 CORS、body 大小和速率限制。
- ComfyUI Custom Node 版本与 Core API 兼容检查。

### 退出标准

- AstraWeft 可调用至少一个 ComfyUI 工作流并获取进度/产物。
- ComfyUI 可通过 Custom Node 调用一个 AstraWeft Provider。
- 重启/断线后任务状态不丢失。
- 不在 ComfyUI workflow JSON 中写入 API Key。

## Phase 7：产品完善与可运维性

> 状态：LOCAL PASS（2026-07-15）；验收证据见
> [Phase 7 产品完善验收报告](./phase-reports/Phase_7_Product_Hardening.md)。

### 目标

补齐成熟桌面产品必需但不应阻塞早期核心验证的能力。

### 工作项

- 完整 Dashboard、产物库、成本分析。
- 数据备份/恢复、数据目录迁移、回收站和保留策略。
- 诊断中心、脱敏导出、数据库完整性检查。
- 插件管理、启用/禁用/升级与兼容提示。
- 全局命令面板、键盘快捷键、系统通知。
- 中文/英文基础本地化、高 DPI、键盘和无障碍审查。
- 性能优化：游标分页、聚合查询、懒加载、缩略图缓存。

### 退出标准

- 关键用户旅程通过可用性走查。
- 10 万 Task / 100 万 Request Log 基准达标。
- 备份恢复和数据目录迁移经过中断测试。
- 高风险操作有明确影响预览和恢复路径。

## Phase 8：开源 Beta 与跨平台发布

### 目标

从“开发机可用”达到“陌生用户可安全安装、升级、诊断和贡献”。

### 工作项

- Windows/macOS/Linux PyInstaller 构建与干净 VM 安装测试。
- macOS 签名与公证、Windows 代码签名；Linux 明确发行格式。
- SBOM、依赖许可证清单、恶意软件扫描和构建 provenance。
- 用户文档、Provider 开发指南、插件示例、故障排查。
- Beta 迁移政策、CHANGELOG、SemVer 和回滚说明。
- 安全报告流程与支持范围。
- 遥测默认关闭；若提供必须显式 opt-in 并公开字段。

### 退出标准

- 三平台全新系统可安装、首次启动、配置 Mock/测试 Provider、卸载。
- 发布包来源可验证，第三方许可证完整。
- 从上一个 beta 的数据 migration 和回滚演练通过。
- 外部贡献者可按文档构建并运行测试。

## 5. 模块开发顺序与依赖

| 顺序 | 模块 | 前置 | 首次被验证于 |
|---:|---|---|---|
| 1 | Common types / Error / ID / Clock | 无 | Phase 1 |
| 2 | Config / Platform dirs / Logging | 1 | Phase 1 |
| 3 | Database / UoW / Migration | 1–2 | Phase 1 |
| 4 | SecretStore / Redaction | 1–2 | Phase 1–2 |
| 5 | Design System / App Shell | 1–2 | Phase 1 |
| 6 | Provider SDK / Registry | 1,4 | Phase 2 |
| 7 | Provider / Model Domain + Repos | 3,6 | Phase 2 |
| 8 | SchemaForm | 5–7 | Phase 2 |
| 9 | Task State Machine / Attempts | 1,3,6 | Phase 3 |
| 10 | Scheduler / Worker / Recovery | 9 | Phase 3 |
| 11 | Request Log / Artifact | 3–4,9 | Phase 3 |
| 12 | Playground / Task / Log UI | 5,8–11 | Phase 3 |
| 13 | Real Provider Plugins | 6,9–11 | Phase 4 |
| 14 | Workflow Domain / Executor | 7,9–11 | Phase 5 |
| 15 | Workflow Editor / Observer | 5,8,14 | Phase 5 |
| 16 | ComfyUI Adapter / Gateway | 9–11,14 | Phase 6 |
| 17 | Dashboard / Ops / Release | 全部 | Phase 7–8 |

## 6. 每阶段统一质量门禁

### 6.1 Pull Request 门禁

每个 PR 必须：

- 单一明确目的，避免把重构、功能和格式化混在一起。
- 有问题背景、设计说明、测试证据和风险/回滚说明。
- 通过 Ruff、mypy、pytest、依赖/secret 扫描。
- 新业务规则有单元测试；修复缺陷先有回归测试。
- 数据库变化包含 migration 和升级测试。
- UI 变化包含截图或短录屏、键盘路径和 loading/empty/error 状态。
- 公共 SDK 变化包含兼容性说明和 contract suite 更新。

### 6.2 代码审查清单

- 依赖方向是否被破坏？
- 是否把 Provider 特例写入 Core/GUI？
- UI 主线程是否可能阻塞？
- 网络期间是否持有数据库事务？
- 状态迁移是否经过 Domain Policy？
- 是否会在崩溃后重复提交或重复计费？
- secret、临时 URL、个人数据是否可能进入日志？
- 错误是否可操作且保留安全诊断？
- 跨平台路径、编码、权限和高 DPI 是否考虑？
- 公共接口是否向后兼容或有明确迁移？

### 6.3 阶段完成定义（Definition of Done）

功能只有同时满足以下条件才算完成：

- 需求和验收场景实现。
- 单元、集成、合约或 GUI 测试与风险相匹配。
- 代码审查通过，无未解释的高优先级问题。
- 文档、migration、配置样例和错误文案同步更新。
- 性能和安全没有低于既定基线。
- 三平台 CI 相关检查通过。
- 在干净用户数据目录完成手工冒烟。
- 不依赖开发者机器的隐式环境或秘密。

## 7. 测试体系

| 层级 | 重点 | 工具/方法 |
|---|---|---|
| Unit | 状态机、DAG、Schema、重试、成本、脱敏 | pytest、property-based tests |
| Contract | Provider SDK 一致性 | SDK contract suite + mock transport |
| Integration | SQLite、Keyring fake、HTTP、文件、恢复 | 临时目录/DB、mock server |
| GUI | ViewModel、组件、关键交互、无障碍 | pytest-qt、截图/焦点检查 |
| End-to-End | 首次配置、生成、恢复、工作流 | 打包应用 + 受控环境 |
| Packaging | 安装、启动、升级、卸载 | Windows/macOS/Linux 干净 VM |
| Security | secret、依赖、路径、loopback API | canary、SAST、依赖扫描、模糊输入 |
| Performance | 大列表、并发轮询、冷启动 | 固定数据集和基准脚本 |

覆盖率不是唯一目标，但 Domain 与 Application 的业务规则应接近全分支覆盖。任何未测试的异常恢复路径都不能仅靠“正常路径测试通过”宣称可靠。

## 8. CI/CD 建议

### PR 流水线

1. 格式/静态检查。
2. 单元与合约测试。
3. Linux 集成测试。
4. Windows/macOS GUI smoke（可按变更范围触发）。
5. secret、依赖漏洞与许可证检查。

### Nightly

- 三平台完整测试和打包。
- 大数据集性能基准。
- 数据库 migration 全历史升级。
- ComfyUI 支持版本矩阵。
- 可选 Provider sandbox test，使用费用上限。

### Release

- 固定 tag 和 lockfile 构建。
- 完整测试、签名、公证、SBOM、hash。
- 从上一稳定版本升级测试。
- 生成 CHANGELOG 和已知问题。
- 先发布候选版本，验证后提升 stable。

## 9. 版本与兼容政策

- 应用遵循 SemVer；0.x 期间仍对数据库 migration 承担兼容责任。
- 数据库只自动向前迁移，升级前备份；不承诺任意新库被旧应用直接打开。
- Workflow 导出格式、Provider Plugin API、Loopback API 分别独立版本化。
- 发布的 WorkflowVersion 永不原地修改；导入升级产生新版本。
- 插件声明兼容 Core API 范围；不兼容时安全禁用并保留配置。

## 10. 设计与产品验收场景

### 核心旅程 A：首次生成

安装 → 欢迎页 → 添加 Provider → 密钥进入 Keychain → 测试连接 → 同步模型 → Playground 动态表单 → 提交 → Task/Log/Artifact 可追踪。

### 核心旅程 B：异步恢复

提交视频 → 远端运行 → 强制退出 → 重启 → 恢复轮询 → 下载产物；全程不重复提交、不丢日志。

### 核心旅程 C：失败诊断

遇到 429/认证/参数/服务错误 → 用户看到可理解原因 → 展开安全诊断 → 根据语义重试或修复配置。

### 核心旅程 D：工作流复现

从 Playground 转为工作流 → 增加节点 → 验证/发布 → 运行 → 查看节点状态与产物血缘 → Provider 模型更新后仍能解释历史运行。

### 核心旅程 E：ComfyUI 双向调用

AstraWeft 调用 ComfyUI 工作流；ComfyUI Custom Node 调用 AstraWeft Provider；双方都不保存密钥明文。

## 11. 性能与可靠性基线

初始工程目标，后续通过基准校准：

| 指标 | 目标 |
|---|---|
| 冷启动到主窗口可操作 | ≤ 3 秒，不含首次大型迁移 |
| 普通页面切换 | ≤ 150 ms 感知响应 |
| 10 万 Task 列表首屏 | ≤ 500 ms |
| UI 主线程 | 禁止网络阻塞；单次同步工作尽量 ≤ 16 ms |
| 未完成远程任务管理 | ≥ 1000 个，受控轮询 |
| 崩溃恢复 | 不重复提交支持幂等的 Task |
| Request Log | 默认 90 天，可配置 |
| secret 泄露 | DB、普通日志、导出包中为 0 |

## 12. 历史基线：第一轮编码建议范围

> 本节记录启动项目时的 Phase 0 范围；Phase 0–7 现已完成本地验收，不再代表当前待办。

用户确认本计划后，第一轮只执行 Phase 0，不直接实现 Provider 或完整页面：

1. 初始化 Git 与开源仓库基础文件。
2. 建立 `pyproject.toml`、src layout、测试和质量工具。
3. 添加最小应用入口与 CI 可验证的启动 smoke。
4. 记录 ADR-001 至 ADR-007 草案。
5. 运行代码审查和完整测试，提交 Phase 0 报告。

Phase 0 评审通过后再进入 Phase 1。任何阶段发现架构假设不成立，先通过 ADR 和文档修正，再实现，不在代码中形成隐式例外。

## 13. 已确认的 Phase 0 决策

- 正式名称：`AstraWeft（星纬）`。
- 开源许可证：Apache-2.0。
- 平台顺序：macOS 主开发、Windows 同步 CI、Linux 在 Beta 前纳入。
- 研发方式：按 Phase 0 → Phase 1 的严格质量门禁推进，不直接开发功能页面。

以上决策通过 ADR 固化；后续如需改变，必须新增 superseding ADR，不能直接覆盖历史决定。
