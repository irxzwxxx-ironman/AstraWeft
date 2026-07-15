# AstraWeft Provider 插件接口规范

> 规范版本：1.0.0-draft  
> 适用范围：内置与第三方 AI Provider 插件  
> 状态：Phase 2–3 Mock 实现与 contract suite 已验证；Phase 4 真实 Provider 接入前继续冻结兼容面

## 1. 目标

Provider Plugin API 为不同 AI 服务商提供稳定、可测试的接入边界。核心系统不依赖 OpenAI、火山、可灵等具体 SDK；插件也不得依赖 Core 的数据库、GUI 或私有实现。

本规范需要同时覆盖：

- 文本、图片、视频、音频和多模态操作。
- 同步响应与“提交—轮询—完成”的异步响应。
- 模型发现、动态参数 Schema、能力和价格元数据。
- 健康检查、取消、幂等、超时、限流、重试和用量。
- 凭据安全、统一 HTTP 观测、日志脱敏与插件兼容。

## 2. 非目标与信任边界

- v1 插件是本地可信 Python 代码，不提供安全沙箱。
- 插件不能直接访问 AstraWeft 数据库、Qt Widget、任务调度器或文件存储内部结构。
- 插件不负责决定业务重试次数、轮询调度、Task 状态迁移和产物保留策略。
- v1 不提供在线插件市场、自动执行远程代码或运行时 `pip install`。
- 若未来支持不受信任插件，应在独立进程中运行，并设计新的 IPC 协议；不得假设当前 Python API 已提供隔离。

## 3. 插件包结构

```text
astraweft_provider_example/
├── pyproject.toml
├── README.md
├── LICENSE
├── src/astraweft_provider_example/
│   ├── __init__.py
│   ├── plugin.py
│   ├── client.py
│   ├── schemas.py
│   └── plugin.toml
└── tests/
    ├── test_contract.py
    └── test_mapping.py
```

通过 Python entry point 发现：

```toml
[project.entry-points."astraweft.providers"]
example = "astraweft_provider_example.plugin:ExampleProviderPlugin"
```

核心使用 `importlib.metadata.entry_points(group="astraweft.providers")` 发现插件。插件导入失败必须被隔离并展示为“加载失败”，不能阻止应用启动。

## 4. Manifest

`plugin.toml` 是无需导入 Python 即可读取的静态元数据：

```toml
manifest_version = 1
plugin_id = "com.example.image-provider"
name = "Example Image Provider"
version = "1.2.0"
plugin_api = ">=1.0,<2.0"
python = ">=3.12,<3.14"
entry_point = "astraweft_provider_example.plugin:ExampleProviderPlugin"
description = "Example image and video provider"
homepage = "https://example.com/plugin"
license = "Apache-2.0"

[publisher]
name = "Example Team"
url = "https://example.com"

[permissions]
network = ["api.example.com", "uploads.example.com"]
filesystem = "none"
subprocess = false

[capabilities]
operations = ["image.generate", "video.generate"]
async_tasks = true
cancel = true
model_discovery = true
usage = true
```

规则：

- `plugin_id` 使用反向域名格式，发布后不可更改。
- `version` 遵循 SemVer。
- `plugin_api` 声明兼容的 Core Plugin API 范围。
- permissions 在 v1 是透明度和审查信息，不是强安全边界。
- manifest 声明与运行时 descriptor 不一致时拒绝启用插件。
- 插件目录安装时记录包 hash、来源和安装时间。

## 5. 核心类型

公开 SDK 包建议命名为 `astraweft-provider-sdk`，只包含稳定类型、协议、异常和测试工具。

### 5.1 操作标识

操作使用可扩展字符串，不使用封闭枚举阻止未来能力：

```text
text.generate
image.generate
image.edit
image.upscale
video.generate
audio.speech
audio.transcribe
embedding.create
model.list
```

第三方扩展操作必须使用命名空间，如 `com.example.avatar.generate`。

### 5.2 Plugin Descriptor

```python
from dataclasses import dataclass
from typing import Mapping

@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    plugin_id: str
    name: str
    version: str
    plugin_api: str
    description: str
    operations: frozenset[str]
    supports_async_tasks: bool
    supports_cancel: bool
    supports_model_discovery: bool
    supports_usage: bool
    default_endpoint: str | None
    settings_schema: Mapping[str, object]
    settings_ui_schema: Mapping[str, object]
    credential_schema: Mapping[str, object]
    redaction_paths: tuple[str, ...]
```

Schema 使用 JSON Schema Draft 2020-12；UI Schema 只控制顺序、分组、控件和帮助文本，不承担数据验证。

### 5.3 Model Definition

```python
@dataclass(frozen=True, slots=True)
class ProviderModel:
    remote_model_id: str
    display_name: str
    modality: str
    operations: frozenset[str]
    parameter_schema: Mapping[str, object]
    parameter_ui_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    capabilities: Mapping[str, object]
    pricing: tuple["PricingRule", ...] = ()
    deprecated: bool = False
```

`remote_model_id` 在同一个 Provider 实例内唯一。插件必须保留远端模型 ID 原值，显示名可以本地覆盖。

### 5.4 Request

```python
@dataclass(frozen=True, slots=True)
class ProviderRequest:
    operation: str
    remote_model_id: str
    inputs: Mapping[str, object]
    idempotency_key: str
    trace_id: str
    timeout_seconds: float
    metadata: Mapping[str, str]
```

Core 在调用插件前完成通用 Schema 校验。插件仍需执行 Provider 特有的交叉字段校验，并抛出标准 `ProviderValidationError`。

`metadata` 只允许非敏感字符串，例如工作流运行关联；插件不得把它未经筛选发送到第三方。

### 5.5 Submission Result

```python
from typing import Literal

@dataclass(frozen=True, slots=True)
class SubmissionResult:
    mode: Literal["completed", "accepted"]
    remote_task_id: str | None
    output: "ProviderOutput | None"
    progress: int | None
    poll_after_seconds: float | None
    provider_request_id: str | None
```

约束：

- `completed` 必须包含 `output`，不得包含待轮询语义。
- `accepted` 必须包含 `remote_task_id`，可提供建议轮询间隔。
- 插件不得在 `submit()` 内自行无限轮询。
- Core 根据结果更新 Task 状态并安排下一次操作。

### 5.6 Remote Task Snapshot

```python
@dataclass(frozen=True, slots=True)
class RemoteTaskSnapshot:
    state: Literal["queued", "running", "succeeded", "failed", "canceled"]
    progress: int | None
    output: "ProviderOutput | None"
    error: "RemoteError | None"
    poll_after_seconds: float | None
    provider_updated_at: str | None
```

Provider 的未知原始状态不能默认为失败；插件应抛出 `ProviderProtocolError` 并附上已脱敏状态摘要。

### 5.7 标准化输出与产物

```python
@dataclass(frozen=True, slots=True)
class ProviderOutput:
    data: Mapping[str, object]
    artifacts: tuple["RemoteArtifact", ...]
    usage: "Usage | None"
    finish_reason: str | None

@dataclass(frozen=True, slots=True)
class RemoteArtifact:
    kind: Literal["image", "video", "audio", "text", "json"]
    source: Literal["url", "base64", "text", "json"]
    value: str | Mapping[str, object]
    mime_type: str | None
    filename_hint: str | None
    expires_at: str | None
    metadata: Mapping[str, object]

@dataclass(frozen=True, slots=True)
class Usage:
    units: Mapping[str, int | str]
    cost_micros: int | None
    currency: str | None
    pricing_source: str | None
```

规则：

- 临时下载 URL 视为敏感信息，不进入普通日志。
- Base64 只允许在 Provider 无 URL/流式方式时使用，并受 Core 大小限制。
- 插件返回远程产物描述；Core Artifact Service 负责下载、校验、命名和保存。
- 未知成本必须为 `None`，不能填 0。
- 货币使用 ISO 4217；成本使用微单位整数避免浮点误差。

## 6. Provider Plugin 与 Client 协议

### 6.1 插件工厂

```python
from typing import Protocol

class ProviderPlugin(Protocol):
    @property
    def descriptor(self) -> ProviderDescriptor: ...

    def create_client(
        self,
        context: "ProviderContext",
        settings: Mapping[str, object],
        credential_ref: str | None,
    ) -> "ProviderClient": ...

    def migrate_settings(
        self,
        from_version: str,
        settings: Mapping[str, object],
    ) -> Mapping[str, object]: ...
```

`create_client` 不执行网络请求。每个 Provider 配置实例拥有独立 Client；Core 控制其生命周期。

### 6.2 Provider Client

```python
class ProviderClient(Protocol):
    async def health_check(self) -> "HealthCheckResult": ...

    async def list_models(self) -> tuple[ProviderModel, ...]: ...

    async def submit(self, request: ProviderRequest) -> SubmissionResult: ...

    async def get_task(self, remote_task_id: str) -> RemoteTaskSnapshot: ...

    async def cancel_task(self, remote_task_id: str) -> "CancelResult": ...

    async def close(self) -> None: ...
```

能力约束：

- 不支持模型发现时，`list_models` 返回插件内置清单；若完全无清单则抛 `UnsupportedOperationError`。
- 不支持远程异步任务时，`get_task/cancel_task` 抛 `UnsupportedOperationError`。
- `close` 必须幂等，释放自建 SDK client；插件通常应优先使用 Core 注入的 HTTP transport。
- 所有网络方法都是异步；不得阻塞 Qt 主线程。

## 7. Provider Context

Core 向插件注入受控能力：

```python
@dataclass(frozen=True, slots=True)
class ProviderContext:
    http: "HttpTransport"
    secrets: "SecretResolver"
    logger: "PluginLogger"
    clock: "Clock"
    plugin_data: "PluginDataDirectory"
    core_version: str
    plugin_api_version: str
```

### 7.1 HTTP Transport

统一 transport 提供：

- 连接池、代理、TLS、用户 CA、超时与重定向限制。
- trace ID 和稳定 User-Agent。
- Request Log 钩子和统一 headers/body 脱敏。
- 流式上传下载和响应体大小限制。
- 测试时可替换为 mock transport。

插件不得记录 `Authorization`、签名 header、完整临时 URL 或原始凭据。若第三方 SDK 无法使用注入 transport，插件需在 manifest 说明，并自行实现等价观测和脱敏。

### 7.2 Secret Resolver

```python
class SecretResolver(Protocol):
    async def get(self, credential_ref: str, field: str) -> "SecretValue": ...
```

密钥环访问可能阻塞，因此解析接口必须异步；插件使用 `await context.secrets.get(...)`。`SecretValue` 的 `repr` 和 `str` 必须脱敏；插件只在构建请求时短暂解包。不得缓存到磁盘、设置对象或异常信息。该修订由 [ADR-008](./adr/ADR-008_Provider_SDK_Packaging_and_Async_Secrets.md) 固化。

### 7.3 Plugin Data Directory

插件可在专属目录保存非敏感缓存。路径由 Core 提供，插件不得自行假设用户目录。配额、清理和备份策略由 Core 管理。manifest 声明 `filesystem = none` 时不得使用。

## 8. 健康检查与模型同步

```python
@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    status: Literal["healthy", "degraded", "unavailable"]
    latency_ms: int | None
    message: str
    details: Mapping[str, str]
```

- 健康检查应使用低成本端点，不能触发计费生成。
- 认证失败必须映射为 `ProviderAuthenticationError`，不能笼统显示“连接失败”。
- Core 为健康检查设置短超时并写入 Request Log，但不创建业务 Task。
- 模型同步不得删除历史模型；远端消失的模型标记 unavailable/deprecated。
- 用户显示名、标签、收藏和默认参数在同步时保留。

## 9. 能力协商

插件 descriptor 声明粗粒度能力，模型定义声明细粒度能力。Core 使用两者交集：

| 能力 | 说明 |
|---|---|
| `operations` | 支持的操作字符串 |
| `async_tasks` | submit 可能返回 accepted |
| `cancel` | 支持远程取消 |
| `idempotency` | `native/emulated/none` |
| `progress` | `exact/estimated/none` |
| `streaming` | 支持文本或二进制流 |
| `model_discovery` | 远端动态枚举模型 |
| `usage` | 返回用量 |
| `pricing` | 返回或内置版本化价格规则 |
| `input_files` | 支持文件、URL、Base64 的集合 |

GUI 不按插件 ID 写条件分支，而是按能力与 Schema 决定可用控件和操作。

## 10. 错误模型

SDK 提供以下标准异常：

```text
ProviderError
├── ProviderValidationError
├── ProviderAuthenticationError
├── ProviderPermissionError
├── ProviderRateLimitError
├── ProviderUnavailableError
├── ProviderNetworkError
├── ProviderTimeoutError
├── ProviderTaskFailedError
├── ProviderProtocolError
├── UnsupportedOperationError
└── PluginConfigurationError
```

每个异常包含：

```python
class ProviderError(Exception):
    code: str
    user_message: str
    technical_message: str
    retryable: bool
    retry_after_seconds: float | None
    provider_code: str | None
    provider_request_id: str | None
    safe_details: Mapping[str, object]
```

映射规则：

- 认证、权限、参数错误默认不可重试。
- 429 使用 Provider 的 `Retry-After`；缺失时由 Core 退避策略决定。
- 网络错误、部分 5xx 可重试，但最终次数由 Core 决定。
- Provider 明确声明任务失败时抛/返回任务失败语义，不做自动重提。
- 捕获未知 SDK 异常后必须转换成 `ProviderProtocolError`，技术详情脱敏并保留 exception chaining。

插件不得自行吞掉错误并返回空成功结果。

## 11. 幂等、重试与轮询

### 11.1 幂等

- Core 为每个 Task 生成稳定 `idempotency_key`，所有 submit attempt 复用。
- Provider 原生支持时映射到其幂等 header/字段。
- 插件声明 `idempotency=none` 时，Core 在崩溃后的不确定状态进入 `NEEDS_ATTENTION`，禁止自动盲重提。

### 11.2 重试

- 插件只负责识别错误是否可重试和建议等待时间。
- Core 负责最大次数、指数退避、抖动、取消和全局速率控制。
- 插件内部不得使用不可见的长重试循环；第三方 SDK 默认重试必须关闭或显式上报。

### 11.3 轮询

- 插件可返回 `poll_after_seconds`，Core 应限制在全局最小/最大间隔内。
- 轮询请求也写 Request Log 和 Attempt。
- 应用重启后使用 `remote_task_id` 继续查询，不重复 submit。
- Provider 返回临时未知状态时不能立即判定失败，应按错误策略处理。

## 12. 取消语义

```python
@dataclass(frozen=True, slots=True)
class CancelResult:
    accepted: bool
    terminal: bool
    message: str
```

- 用户请求取消时 Core 先持久化 `cancel_requested_at`。
- 不支持取消时，Core 停止主动等待需由产品策略决定；不得伪造远程任务已取消。
- Provider 接受取消但仍在处理时，Task 进入 `CANCELING` 并继续低频查询。
- 取消不保证退款；插件可在 safe details 中提供 Provider 说明链接标识，但 GUI 使用统一风险文案。

## 13. 配置与凭据 Schema

配置分为两类：

- settings：endpoint、region、organization、project、API version 等非敏感内容，可持久化到 DB。
- credentials：API key、secret key、OAuth token 等，只进入 Secret Store。

示例：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "api_key": {
      "type": "string",
      "title": "API Key",
      "minLength": 1,
      "x-astraweft-secret": true
    }
  },
  "required": ["api_key"],
  "additionalProperties": false
}
```

Core 拒绝把标记为 secret 的字段写入普通 settings。插件升级改变 settings 结构时通过 `migrate_settings` 执行纯函数迁移；凭据迁移必须要求用户重新授权或由 Secret Store 的显式操作完成。

### 13.1 Schema 文案本地化扩展

插件仍以标准 JSON Schema 的 `title` / `description` 作为默认文案。需要适配 AstraWeft
支持的其他界面语言时，可在字段 Schema 上添加非执行型扩展 `x-astraweft-i18n`：

```json
{
  "type": "string",
  "title": "故障模式",
  "description": "仅用于本地测试。",
  "x-astraweft-i18n": {
    "en_US": {
      "title": "Failure mode",
      "description": "For local testing only."
    }
  }
}
```

- 当前稳定 locale key 为 `zh_CN` 与 `en_US`；没有匹配项时回退到标准关键字。
- 扩展只允许纯文本 `title` 和 `description`，不得包含 HTML、脚本、QSS 或可执行 UI 定义。
- 本地化内容不参与参数验证、字段名、持久化、工作流快照或 checksum 语义。
- 插件应至少为其默认语言提供标准 `title`；内置插件必须通过中英文表单渲染测试。

## 14. 插件生命周期

```text
Discover manifest
  → Verify compatibility and hash
  → Import entry point
  → Validate descriptor
  → Enable plugin
  → Create client per Provider instance
  → Health check / operations
  → Close client
  → Disable or upgrade
```

- 禁用插件后不接受新任务；已有远程任务进入可恢复/需处理状态。
- 插件升级前检查是否存在活跃调用，等待安全点后替换。
- 同一插件的不同 Provider 配置相互隔离，不共享凭据。
- Client 是否支持并发必须在 descriptor 中声明；默认 Core 对同一实例应用并发限制。

## 15. API 版本兼容策略

- Plugin API 使用 `MAJOR.MINOR`。
- Core 在同一 MAJOR 内保持向后兼容；新增可选字段提升 MINOR。
- 删除/改义字段提升 MAJOR，并提供迁移周期。
- 插件 manifest 的版本范围与当前 Core 不相交时拒绝加载并给出可操作提示。
- DTO 新增字段必须有默认值；插件应忽略其不认识的可选扩展 metadata。
- 不允许插件导入 `_internal` 或 Core 实现包；CI 通过静态检查阻止内置插件违规。

## 16. 合约测试

SDK 提供 `ProviderContractSuite`，每个插件必须通过：

1. manifest 与 descriptor 一致性。
2. Schema 合法性、示例参数验证及声明语言的表单文案回退。
3. 健康检查成功、认证失败和超时映射。
4. 同步 submit 成功输出。
5. 异步 submit → poll → success。
6. Provider 明确失败、429、5xx、网络中断映射。
7. cancel 支持与不支持路径。
8. 幂等键在重试中保持不变。
9. 模型同步稳定 ID 与远端删除处理。
10. 日志、异常和 DTO 中不出现 secret canary。
11. `close()` 可重复调用。
12. 无隐藏阻塞和不可见长重试。

插件测试分级：

- Unit：纯映射、Schema、错误转换。
- Contract：SDK 的统一 mock transport 场景。
- Sandbox Integration：使用 Provider 官方测试环境或受控账号，默认不在普通 PR 中运行。
- Live Smoke：发布前人工授权运行，使用严格费用上限。

## 17. 最小示例

```python
class ExampleProviderClient:
    def __init__(self, context, settings, credential_ref):
        self._context = context
        self._settings = settings
        self._credential_ref = credential_ref

    async def submit(self, request: ProviderRequest) -> SubmissionResult:
        api_key = self._context.secrets.get(
            self._credential_ref, "api_key"
        )
        response = await self._context.http.post_json(
            "/v1/images",
            headers={"Authorization": api_key.as_bearer()},
            json={
                "model": request.remote_model_id,
                **request.inputs,
            },
            idempotency_key=request.idempotency_key,
            trace_id=request.trace_id,
            timeout=request.timeout_seconds,
        )
        return map_submission(response)

    async def get_task(self, remote_task_id: str) -> RemoteTaskSnapshot:
        response = await self._context.http.get_json(
            f"/v1/tasks/{remote_task_id}"
        )
        return map_remote_task(response)
```

映射函数必须独立、纯净、可用固定 fixture 测试。示例省略了错误转换和其余协议方法，实际插件必须完整实现或明确抛 `UnsupportedOperationError`。

## 18. 禁止事项

插件不得：

- 直接导入 PySide6 并创建页面或弹窗。
- 直接读写 AstraWeft SQLite 数据库。
- 把 API Key 放入 settings、日志、trace、URL 或返回 DTO。
- 在 `submit()` 内无限轮询直到任务结束。
- 自行创建不可控后台线程或常驻进程。
- 绕过 Core Artifact Service 任意写入用户文件目录。
- 把 Provider 原始成功响应未经验证地当作标准输出。
- 将未知成本记为 0 或将未知状态映射为成功。
- 在插件导入阶段执行网络请求、读取凭据或改变系统状态。

## 19. 首批插件实施顺序

1. `MockProvider`：覆盖同步、异步、失败、429、取消和恢复，用于 Core 与 UI 开发。
2. 第一个真实同步 Provider：验证认证、模型同步、Schema 和 Artifact 闭环。
3. 第一个真实异步视频 Provider：验证 submit/poll/cancel/recovery 和费用风险。
4. 第二/第三真实 Provider：检验接口是否真正通用；发现特例时优先扩展能力模型，不在 Core 写插件 ID 分支。

## 20. 规范验收标准

- Core 可在完全不知道插件 ID 的情况下发现、配置、测试和调用插件。
- GUI 仅依赖 descriptor、能力与 Schema 自动呈现。
- 同一异步 Task 在应用重启后不重复提交。
- 插件所有异常都可映射到统一用户错误和安全诊断信息。
- secret canary 不出现在数据库、普通日志、异常 repr 和导出包。
- 一个外部示例插件仅依赖公开 SDK 即可通过 contract suite。
- Plugin API 的兼容范围不满足时，应用安全拒绝加载而不是崩溃。
