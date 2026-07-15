# Phase 6 ComfyUI 集成验收报告

- 项目：AstraWeft（星纬）
- 日期：2026-07-15
- 状态：LOCAL PASS（当前 Codex 未签名启动器的 Keychain/Gateway 降级见第 5 节）
- 范围：macOS 本地完整门禁；Git 与远端 Windows/Linux CI 按用户要求延后

## 1. 阶段交付

### 1.1 ComfyUI 资源与协议适配

- `ComfyUIInstance`、不可变 API-format `ComfyUITemplate` 和持久
  `ComfyUIExecution` 已落地；实例支持安全 URL 规范化、启停、探测和软删除。
- aiohttp 客户端覆盖 `/system_stats`、`/features`、`/object_info`、`/prompt`、
  `/queue`、`/history`、`/view`、`/interrupt` 与 `/ws` 进度提示。
- 请求、响应、帧、下载、超时和 redirect 均有边界；远端错误不会把响应正文或
  Secret 写入诊断日志。
- Workflow 草稿冻结 prompt、template/workflow checksum、输入 patch target 和非空输出
  节点列表；Execution 再次冻结输出列表，只物化所选节点文件。

### 1.2 持久执行与恢复

- NodeRun 先保存 `planned_comfyui_execution_id`，Execution 先保存 `PLANNED`，再向
  ComfyUI 提交，避免崩溃窗口生成新的本地执行身份。
- `SUBMITTING` 恢复通过本地 execution marker 搜索 queue/history；提交结果不确定时
  转 `NEEDS_ATTENTION`，不会盲目重提。
- WebSocket 只提供尽力进度；持久 queue/history 轮询是状态真相。取消结果、远端任务
  缢失、断线、超时、成果为空和第 N 个成果落盘失败均有保守终态。
- ComfyUI 成果使用稳定 Artifact ID、`.partial`、大小限制和 SHA-256 后再进入本地
  Artifact/血缘记录；已完成执行再次推进或取消保持幂等。

### 1.3 GUI 与工作流

- 资源区新增 ComfyUI 页面：实例添加/编辑/测试/启停/删除、API Format JSON 导入、
  常用输入映射和成果节点选择。
- Workflow 画布新增 `COMFYUI` 节点类型、模板选择、发布校验、运行调度、恢复、取消和
  Artifact 输出；Provider Task 与 ComfyUI Execution 使用相互独立的计划/正式 ID。
- 顶部状态可显示 ComfyUI 在线、离线、未测试或未配置；错误通过安全中文提示呈现。

### 1.4 Loopback Gateway 与 Custom Nodes

- Gateway 固定绑定 `127.0.0.1:17493`，全部路由 Bearer 认证，并校验 Host、Origin、
  CORS preflight、256 KiB body、滑动限流、未知字段和 Artifact 根目录。
- API 仅开放健康、非机密目录、Task 创建/查询/取消、Task Artifact 列表和按 ID 下载；
  不开放 Secret、Provider 配置、SQL、日志或任意路径。
- `AstraWeftProviderImage` / `AstraWeftProviderVideo` 不含 API Key widget，校验
  `astraweft.loopback/v1`，经真实 Gateway 调用 Mock Provider Task、轮询成功并下载本地
  Artifact 的双向测试已通过。

## 2. 验收证据

主验证环境：macOS arm64，Python 3.12.13，Qt/PySide6 6.10.3。

| 门禁 | 结果 | 证据摘要 |
|---|---|---|
| Ruff lint / format | PASS | 202 个 Python 文件格式一致，0 lint |
| mypy strict | PASS | 195 个源码与测试文件，0 issues |
| import-linter | PASS | 181 个文件、837 条依赖；7 个契约保持，0 broken |
| pytest | PASS | 351 passed |
| branch coverage | PASS | 90.19%，两位精度高于 90.00% 门槛 |
| ComfyUI protocol/recovery | PASS | probe、submit、queue/history、WS、取消、断线、对账、下载与故障注入全绿 |
| selected outputs | PASS | Execution 冻结输出节点；未选文件不物化，标准输出只保留所选节点 |
| GUI | PASS | 实例/模板、在线/离线、导入、启停、删除和 Workflow ComfyUI 节点全绿 |
| Loopback security | PASS | token、Host、Origin、OPTIONS、body、坏 JSON、未知字段、限流、404/500 安全响应 |
| Custom Node E2E | PASS | Custom Node client → Gateway → Mock image Task → Artifact 下载全链路 |
| dependency audit | PASS | 第三方依赖无已知漏洞；5 个本地未发布包按预期跳过 PyPI 查询 |
| 5 个包构建 | PASS | Core、SDK、Mock、OpenAI、Runway 各 1 个 sdist + wheel |
| package metadata/content | PASS | 10 个产物 Twine 全绿，5 个 wheel contents 全绿；wheel 含 `0005` 与 Custom Nodes |
| isolated wheel smoke | PASS | 全新 venv 仅安装最终 wheel；3 个插件、2 个 Custom Nodes、Gateway 17493 和 offscreen GUI 全绿 |
| fresh migration | PASS | 最终 wheel 新库为 `0005`；integrity `ok`，0 外键违规 |
| 现有数据升级 | PASS | 在线备份后 `0004 → 0005`；integrity `ok`，0 外键违规，3 张 ComfyUI 表齐全 |
| 本地运行 | PASS / DEGRADED | Phase 6 GUI 进程 PID 14989 正在运行；当前未签名 Codex Python 无权新建 Keychain token，Gateway 未监听 |

最终发行产物位于 `build/phase6-gate-dist-final/`；隔离验证数据位于
`build/phase6-wheel-smoke-final-data/`。

## 3. Phase 6 退出标准映射

| 退出标准 | 结论 |
|---|---|
| AstraWeft 可调用 ComfyUI 工作流并取得进度/产物 | PASS；mock ComfyUI HTTP/WS 全链路、持久恢复与本地成果物化已验证；未声称真实用户 ComfyUI 实例已安装 |
| ComfyUI 可通过 Custom Node 调用 AstraWeft Provider | PASS；真实 loopback HTTP + Mock Provider + Artifact 下载 E2E |
| 重启/断线后任务状态不丢失 | PASS；planned identity、SUBMITTING 对账、queue/history 真相和不确定终态已验证 |
| ComfyUI workflow JSON 不写入 API Key | PASS；节点无 Key widget，workflow 仅含 Provider/Model/operation/普通输入，token 来自 Keychain |

## 4. 安全与数据审查结论

- Provider Secret 和 Gateway token 不进入 SQLite、Workflow config、Custom Node widget、
  Request Log 或错误响应；认证比较使用恒定时间函数。
- HTTP 明文 ComfyUI 地址仅允许 loopback；远程实例必须使用 HTTPS，且禁止 URL
  userinfo/query/fragment。
- ComfyUI 提交结果不确定时禁止自动重提；这比“尽量成功”更优先地保护重复计费和
  重复 GPU 工作。
- 升级前保留 `build/local-data/data/astraweft-phase5-0004-backup.db`；备份 revision
  `0004` 且 integrity `ok`。当前数据库为 `0005`，integrity `ok`，外键违规为 0。
- migration 只新增 ComfyUI 表并扩展 NodeRun，不重写现有 Provider、Task、Workflow 或
  Artifact 业务数据。

## 5. 已知限制与延后范围

- 当前 Codex runtime 的 Python 仅有 linker/ad-hoc 签名，macOS Keychain 拒绝由它创建
  新 token（`-25293`）。应用按设计保持 GUI/数据库可用并记录 Gateway 降级；Session
  Secret 隔离测试证明 Gateway 代码与固定端口可运行。正式 macOS App 签名/Keychain
  entitlement 必须在 Phase 8 发布门禁再次实机验证，不能由本次测试替代。
- 未安装或调用用户真实 ComfyUI；协议验证使用官方路由契约和受控 aiohttp mock server，
  不推断第三方 Custom Node 或未来 ComfyUI 版本兼容。
- Git 操作、远端仓库、Windows/Linux CI 实际执行继续延后；不能从 macOS 结果推断其他
  平台通过。
- 本阶段未执行真实付费 Provider 请求，不产生外部费用。

## 6. 阶段结论

Phase 6 的领域、数据、Adapter、Workflow、GUI、Loopback 安全、Custom Node 双向链路、
恢复、打包和现有数据升级均达到 macOS 本地门禁。AstraWeft 可进入 Phase 7 产品完善与
可运维性；当前自动化启动器的 Keychain/Gateway 降级作为签名发布环境风险保留，不应
被误写为当前 17493 已在线。
