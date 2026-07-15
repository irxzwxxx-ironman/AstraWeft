# AstraWeft ComfyUI 集成实施设计

- 状态：Implemented and accepted by Phase 6 macOS local gates
- 日期：2026-07-15
- 依据：Architecture v2、架构评审、详细技术设计、ADR-009–012

## 1. 目标与边界

Phase 6 交付双向但边界清晰的组合：

1. AstraWeft 将冻结的 ComfyUI API-format workflow 作为 `COMFYUI` WorkflowNode
   提交给用户配置的实例，观察进度、恢复、取消并物化成果。
2. ComfyUI 中的 AstraWeft Image/Video Gateway nodes 通过只监听 loopback 的
   `/api/v1` 调用真实 TaskService，不接触 Provider API Key。

非目标：不替代 ComfyUI 画布；不实现 Comfy Cloud 付费 API；不自动安装
ComfyUI/模型/Custom Node；不将 ComfyUI 当作 Provider；不支持局域网 Gateway。

## 2. 官方协议基线

当前实施依据 ComfyUI 官方资源：

- [Server routes](https://docs.comfy.org/development/comfyui-server/comms_routes)：`/ws`、
  `/system_stats`、`/features`、`/prompt`、`/history/{prompt_id}`、`/queue`、
  `/interrupt`、`/view`。
- [Server communication overview](https://docs.comfy.org/development/comfyui-server/comms_overview)：
  aiohttp 服务端和消息模型。
- [Official WebSocket API example](https://github.com/comfyanonymous/ComfyUI/blob/master/script_examples/websockets_api_example.py)：
  `clientId`、`prompt_id`、`executing.node is null` 完成信号、`/history` 和 `/view` 查询。
- [Custom Node lifecycle](https://docs.comfy.org/custom-nodes/backend/lifecycle) 与
  [node properties](https://docs.comfy.org/custom-nodes/backend/server_overview)：
  `NODE_CLASS_MAPPINGS`、`INPUT_TYPES`、`RETURN_TYPES`、`FUNCTION` 契约。

协议处于持续发展中，因此代码使用宽容读取、严格写入和能力探测，不对
未验证版本号作硬编码推断。

## 3. 模块与依赖

```text
Presentation
  ComfyUIPage / InstanceDialog / TemplateImportDialog / Workflow ComfyNodeDialog
       ↓
Application
  ComfyUIService / Gateway facade / WorkflowExecutionService
       ↓
Domain + Ports
  ComfyUIInstance / Template / Execution / ports.comfyui
       ↑
Infrastructure
  SQL repositories + aiohttp ComfyUI client + LoopbackGateway + Artifact writer

integrations/comfyui_custom_nodes/AstraWeftGateway
  stdlib HTTP client + OS Keychain lookup + Image/Video nodes
```

Application 不导入 aiohttp、SQLAlchemy、Qt 或 Custom Node package。ComfyUI Adapter 不经过
Provider Registry，Gateway 不绕过 TaskService 直接调用 Provider plugin。

## 4. 领域与数据模型

### 4.1 ComfyUIInstance

- 稳定 ID、唯一活跃名称、规范化 base URL、enabled、row version。
- 最后探测的 ComfyUI/Python 版本、features、node class 摘要、队列信息、
  检测时间和安全诊断错误。
- base URL 不允许 userinfo/query/fragment。`http` 只允许 `localhost`、
  `127.0.0.1`、`::1`；其他主机必须使用 `https`。

### 4.2 ComfyUITemplate

- 属于一个 Instance，保存名称、API-format prompt、SHA-256 checksum、row version。
- 输入定义由 JSON Schema 与显式 patch target 组成：
  `port → {node_id, input_name}`。不支持任意 JSONPath/Jinja/Python。
- 输出节点是非空的显式 node ID 列表；Execution 冻结该列表，只物化所选节点文件。
- 导入上限 1 MiB，根对象的每个节点必须有 `class_type` 文本和 `inputs` 对象；
  拒绝 secret 键、非标准 JSON、非本地 `$ref` 和非法 patch target。

### 4.3 ComfyUIExecution

状态：

```text
PLANNED → SUBMITTING → QUEUED → RUNNING → MATERIALIZING → SUCCESS
             │           │        │              ├→ FAILED
             └→ NEEDS_ATTENTION  └→ CANCELING → CANCELED
```

保存：NodeRun、Instance、Template 快照/checksum、已 patch prompt、client ID、remote
prompt ID、progress、标准输出、artifact IDs、错误、超时/取消和时间戳。

`node_runs` 新增 `planned_comfyui_execution_id` 与 `comfyui_execution_id`。两者不复用
Task 列，且正式 ID 必须与 planned ID 一致。

## 5. 模板冻结与输入 patch

在 Workflow 草稿中添加 ComfyUI 节点时，将 Template 冻结为节点 config：

```json
{
  "instance_id": "...",
  "template_id": "...",
  "template_checksum": "<sha256>",
  "prompt": {"6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}},
  "input_targets": {"prompt": {"node_id": "6", "input_name": "text"}},
  "output_nodes": ["9"]
}
```

运行时只能将已解析的端口值写入 `prompt[node_id].inputs[input_name]`。发布校验
确认实例已启用、checksum 与冻结 prompt 一致、target 存在、节点 class
在最后能力快照中可用，且 config 无机密。Template 后续修改不影响已发布版本。

## 6. Adapter 协议与恢复

### 6.1 探测

1. `GET /system_stats`，读取版本/设备摘要。
2. `GET /features`，缺失时记为可选能力不可用，不直接判死。
3. `GET /object_info`，保存 node class 名称的排序 checksum，不将整份巨大响应入库。
4. 所有响应都有超时、JSON 类型和字节上限。

### 6.2 提交

`POST /prompt` 只发送：

- `prompt`：已冻结并 patch 的 API-format 对象；
- `client_id`：本地持久 UUID；
- `extra_data.astraweft_execution_id`：持久本地执行 ID；
- `extra_data.astraweft_workflow_checksum`：已发布 WorkflowVersion checksum。

响应必须包含非空 `prompt_id`；`node_errors` 非空时转为用户可读发布/运行错误。

### 6.3 WebSocket

`/ws?clientId=<client_id>` 仅接受 JSON text frame，限制单帧 2 MiB，忽略预览二进制帧。
按 `prompt_id` 过滤 `execution_start`、`executing`、`progress`、`executed`、
`execution_success`、`execution_error`、`execution_interrupted`。进度以 `value/max` 换算并限制
0–99，成功 100 只由 history 物化后设置。

WebSocket 是尽力而为的进度提示；断线不改变执行状态，下一次持久轮询会重新建立观察。

### 6.4 真相轮询

- `/history/{prompt_id}` 有 completed 记录：解析 status/messages 与 outputs。
- 未完成：`/queue` 中的 prompt ID 映射为 QUEUED/RUNNING。
- 已知 prompt ID 在 history/queue 都不可见：保守转 `NEEDS_ATTENTION`，不猜测成功、
  失败或自动重提。
- SUBMITTING 且无 prompt ID：在 `/queue` 与 `/history` 搜索
  `extra_data.astraweft_execution_id`；找到则绑定，未找到则 `NEEDS_ATTENTION`。

### 6.5 取消

尝试通过 `/queue` 删除待执行 prompt；已运行时发送 `/interrupt`。由于某些
ComfyUI 版本的 interrupt 是实例级而非 prompt 级，GUI 必须明示影响；远程状态
不确定时不伪造 CANCELED，转 `NEEDS_ATTENTION`。

## 7. 成果解析与 Artifact

history `outputs` 中识别 `images`、`gifs`、`video`、`videos`、`audio` 数组。
每个文件只接受文本 `filename`、`subfolder`、`type`，并使用 `/view` 查询参数下载。

- 禁止 redirect，校验 Content-Length 和流式字节，单产物默认上限 512 MiB。
- 只写 `.partial`，流式 SHA-256 和长度复核后原子替换。
- `Artifact.task_id = NULL`，metadata 记录 `source=comfyui`、instance/template checksum、
  node ID 和不含敏感数据的文件摘要。
- 全部成果落盘并入库后 Execution/NodeRun 才能 SUCCESS。

## 8. Loopback Gateway v1

### 8.1 路由

| 方法 | 路由 | 用途 |
|---|---|---|
| GET | `/api/v1/health` | API/Core 版本和就绪状态 |
| GET | `/api/v1/catalog` | 已启用 Provider/Model/operation 的非机密摘要 |
| POST | `/api/v1/tasks` | 通过 TaskService 创建 Provider Task |
| GET | `/api/v1/tasks/{task_id}` | 查询标准状态、进度、错误与 artifact IDs |
| POST | `/api/v1/tasks/{task_id}/cancel` | 请求取消 |
| GET | `/api/v1/tasks/{task_id}/artifacts` | 查询任务的本地产物摘要 |
| GET | `/api/v1/artifacts/{artifact_id}` | 返回本地产物字节 |

Gateway 不提供 Provider 创建/编辑、Secret 查询、任意文件路径、SQL/日志、
Workflow 修改或插件安装能力。

### 8.2 安全守卫

- 启动时确保 Keychain 中存在 32-byte URL-safe token；旋转立即使旧 token 失效。
- 所有路由（包括 health）都需 `Authorization: Bearer <token>`。
- 无 Origin 的本地 Python 请求可接受；存在 Origin 时只接受 Gateway 自身的
  loopback origin。拒绝 CORS preflight，不使用 `*` CORS，不支持浏览器直连。
- aiohttp `client_max_size=256 KiB`；JSON 必须是 UTF-8 object，拒绝未知字段。
- 所有已认证请求共用进程内滑动窗口，默认每分钟 120 次；超限返回 429。
- artifact path 由数据库 ID 解析，`resolve()` 后必须在 artifact root 内。
- 错误响应只包含稳定 code 和用户消息，不返回 traceback、Secret、Provider 原文或路径。

## 9. ComfyUI Custom Node 包

`integrations/comfyui_custom_nodes/AstraWeftGateway` 提供：

- `AstraWeftProviderImage`：Provider/Model/Prompt → 提交 image Task → 轮询 → 下载首个
  image Artifact → ComfyUI `IMAGE` tensor + task ID。
- `AstraWeftProviderVideo`：Provider/Model/Prompt → video Task → 轮询 → 下载到 ComfyUI
  output/temp 目录 → 返回本地文件 `STRING` + task ID。

两个节点都不定义 token/API Key widget。令牌读取顺序：测试专用环境注入 →
OS Keychain `AstraWeft / loopback_gateway:access_token`。Gateway URL 默认
`http://127.0.0.1:17493/api/v1`，只允许环境覆盖为 loopback URL。

Custom Node 每次执行前校验 health 的 `astraweft.loopback/v1` 标识，不兼容时在
ComfyUI 节点中立即显示可操作错误。

## 10. GUI

### 10.1 ComfyUI 资源页

- 实例列表：名称、URL、启用、连通、版本、设备、节点摘要、最后探测。
- 新建/编辑：地址实时安全验证，保存后才连接测试，失败显示安全诊断。
- 模板：导入 API-format JSON、校验 checksum、配置输入端口/patch target 和输出节点。
- Gateway：运行状态、loopback 地址、API 版本、令牌已配置标记和“旋转令牌”；
  令牌永不回显。

### 10.2 Workflow 编辑器

- 节点库新增 ComfyUI；对话框选择已启用/已探测实例与模板。
- 节点卡显示 ComfyUI 版本/模板 checksum 摘要；发布问题面板显示缺失实例、
  模板篡改、节点不可用和不安全 prompt。
- 观察器详情显示 local execution ID、remote prompt ID、实例、进度、断线/轮询
  状态和 Artifact，不显示 prompt 中的大体积或敏感值。

## 11. 资源生命周期

AppContext 启动顺序：迁移 → DB → SecretStore → HTTP clients → Services → Gateway bind →
Task/Workflow runtimes。关闭顺序：Gateway 停止接受新请求 → Workflow runtime → Task runtime →
ComfyUI WebSocket/client → Core HTTP → DB。

Gateway 绑定失败不得让数据库迁移或主界面失败，但必须在顶部状态和 ComfyUI
页显示“Gateway 不可用”；不得自动改用随机端口。

## 12. Phase 6 门禁

### 领域/安全

- Instance/Template/Execution/NodeRun 状态机、URL 规则、prompt/patch/checksum 单元测试。
- Gateway 无/错 token、恶意 Origin、CORS preflight、超限 body、坏 JSON、未知字段、
  限流、路径穿越、日志/DB 无 token canary。

### Adapter/恢复

- 固定 mock ComfyUI server 覆盖 probe、submit、queue、WebSocket progress、history success/error、
  `/view` 流式成果、interrupt、损坏 JSON、timeout、超限和断线重连。
- 崩溃窗口：持久 planned ID 后、Execution 创建后、POST 发出后响应前、
  remote ID 持久后、第 N 个 Artifact 落盘时。
- 断开 WebSocket 后使用 history/queue 成功恢复；已完成节点不重提。

### 双向 E2E/GUI

- AstraWeft Workflow 调用一个 mock ComfyUI prompt，收到进度并物化图像 Artifact。
- Custom Node client 经真实 loopback Gateway 调用 Mock Provider Task，轮询成功并下载 Artifact。
- 实例/模板/Gateway 页、Workflow ComfyUI 节点和运行观察 pytest-qt 全链路。
- 至少 100 个同时 ComfyUI Execution 记录查询不阻塞 UI；进度事件去抖入库。

### 统一门禁

- Ruff format/lint、mypy strict、import-linter、pytest + branch coverage ≥ 90.00%。
- dependency audit；5 个现有包与 Custom Node 包内容检查；全新 wheel 环境的
  migration、plugin discovery、Gateway bind 和 offscreen GUI 启停。
