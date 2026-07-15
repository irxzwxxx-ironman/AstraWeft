# ADR-012：ComfyUI 独立执行适配器与 Loopback Gateway

- 状态：Accepted
- 日期：2026-07-15
- 关联：ADR-001、ADR-003、ADR-004、ADR-009、ADR-010、ADR-011

## 背景

ComfyUI 是本地节点图执行引擎，不是云 Provider。它的 `/prompt`、`/ws`、
`/history/{prompt_id}` 和 `/view` 协议、任务恢复与成果文件语义都与 Provider SDK
不同。反向调用又要求 ComfyUI Custom Node 访问 AstraWeft 的 Provider/Task 能力。

若把 ComfyUI 伪装成 Provider，会污染 Provider/Model 语义、制造虚假 Task 外键，并复制
已有调度账本。若 Custom Node 把 Gateway token 或 Provider API Key 写入 workflow JSON，
工作流文件、PNG metadata 和分享模板都可能泄密。

## 决策

1. ComfyUI 作为 `Infrastructure` 中的独立 Execution Adapter，通过 `ports.comfyui`
   与 Application 交互；不注册 Provider plugin，不创建虚假 Provider/Model/Task。
2. 新增 `ComfyUIInstance`、`ComfyUITemplate`、`ComfyUIExecution` 账本。已发布
   WorkflowNode 冻结 API-format prompt、checksum、输入 patch 目标和输出节点。
3. ComfyUI NodeRun 先持久 `planned_comfyui_execution_id`，再幂等确保 Execution
   存在，最后关联 `comfyui_execution_id`。不重用 `planned_task_id/task_id`。
4. `/prompt` 提交前持久完整 prompt、client ID 和本地 execution ID。提交携带
   `extra_data.astraweft_execution_id`。若响应前崩溃，恢复先在 `/queue` 与
   `/history` 按该标记对账；无法确认时转 `NEEDS_ATTENTION`，不盲目重提。
5. WebSocket 是低延迟进度信号，不是唯一真相。终态、恢复和成果始终以
   `/history`、`/queue` 和已本地物化 Artifact 为准。
6. ComfyUI 成果通过原子 `.partial → replace` 路径写入 Artifact Store，`task_id`
   保持 `NULL`，并通过 NodeRun `ArtifactLink` 建立血缘。
7. Loopback Gateway 只绑定 `127.0.0.1:17493`，API 独立版本化为 `/api/v1`。
   256 KiB 请求上限、Bearer token、常量时间比较、严格 Origin/CORS、路由级
   限流和统一错误封装为强制守卫。
8. Gateway token 使用 256-bit CSPRNG 生成并存入 OS Keychain。Custom Node 从同一
   Keychain account（或显式的进程环境注入）读取，不将 token 或 Provider Key
   定义为节点输入。
9. Phase 6 的稳定兼容面以能力探测而不是猜测版本号：必须具备
   `/system_stats`、`/prompt`、`/history/{id}`、`/view`；`/features`、`/object_info`、
   `/ws` 用于增强诊断与进度。探测结果和 ComfyUI 版本保存快照。

## 理由

- 保持 Provider SDK 专注云/远程模型，同时让 Workflow Executor 可以扩展到其他引擎。
- 持久执行意图与保守对账关闭了“网络已提交、本地未记录”的重复执行窗口。
- 将 WebSocket 降级为可重建的观察信号，断线不会破坏持久状态。
- 本地令牌只解决进程间授权，Provider 凭据仍只存在 AstraWeft Keychain 边界。

## 后果

- NodeRun 增加两个 ComfyUI 执行引用字段，WorkflowExecutionService 增加第三种
  执行分支，但 Provider Task 与 ComfyUI Execution 仍有独立状态机。
- 已发布工作流可能包含较大的 ComfyUI prompt JSON，仍受 1 MiB AstraWeft
  导出上限限制。
- Gateway 是新的本地网络攻击面，必须与桌面资源共同启停，且不能在
  绑定失败时静默切换到其他端口或全网卡。

## 执行守卫

- import-linter 继续禁止 Application 导入 aiohttp/Infrastructure。
- workflow JSON、SQLite 普通字段、日志和诊断输出中不得出现 Gateway token。
- Adapter 的 base URL 拒绝 userinfo、query 和 fragment；明文 HTTP 只允许 loopback。
- 不能因 WebSocket 失败将已知 remote prompt 判为失败；必须回退到轮询。
- 对提交不确定、损坏 history、超限成果、路径穿越、恶意 Origin、无 token、
  错误 token、超限 body 和限流必须有故障注入测试。

## 迁移与回滚

Phase 6 migration 仅新增 ComfyUI 表并扩展 NodeRun，不改写现有 Provider Task、
Workflow 定义和 Artifact。升级前使用 SQLite online backup。回滚前必须确认
不存在需保留的 ComfyUI Execution；应用级回滚不删除产物文件。

## 替代方案

- 将 ComfyUI 实现为 Provider plugin：拒绝，引擎图、模型列表、任务和成果语义不等价。
- 为每个 ComfyUI Node 创建隐藏 Task：拒绝，需要虚假 Provider/Model 且污染成本统计。
- 仅依赖 WebSocket 判定结束：拒绝，重启和断线时不可恢复。
- token 作为 Custom Node widget：拒绝，会进入 workflow JSON 和图像 metadata。
- 监听 `0.0.0.0`：拒绝，超出 Local First 默认信任边界。
