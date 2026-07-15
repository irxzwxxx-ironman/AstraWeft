# OpenAI Responses API Provider Adapter 设计说明

- 状态：Implemented and locally verified in Phase 4
- 日期：2026-07-15
- 插件包：`astraweft-openai-provider`
- 插件 ID：`com.openai.api-provider`
- 首个范围：同步 `text.generate`

## 1. 目标与边界

本适配器是 AstraWeft 第一个真实网络 Provider，用来验证公共 Provider SDK、受控 HTTP transport、真实认证、模型发现、错误映射、用量映射和 Request Log 观测能否保持通用边界。它作为独立 package，只依赖 `astraweft-provider-sdk`，不依赖 Core、Qt、SQLAlchemy、keyring 或 OpenAI Python SDK。

Phase 4 首切片只支持：

- 通过 `GET /v1/models` 验证认证并获取当前账号可见模型。
- 通过 `POST /v1/responses` 完成同步文本生成。
- 映射输入/输出 token、OpenAI request ID、标准 HTTP 错误和 `Retry-After`。
- 由 Core 注入的 HTTP transport 执行网络请求；插件不创建第二套连接池。

本切片不支持：

- Streaming、Background Responses、工具调用和多轮 `previous_response_id`。
- Image、Audio、Video、Files 或 Batch API。
- 真实 API 自动测试、自动读取环境变量密钥或任何默认付费调用。
- OpenAI-compatible 自定义 endpoint；manifest 只授权 `api.openai.com`。

## 2. 官方契约基线

实现依据以下 OpenAI 官方资料，均于 2026-07-15 核对：

- [Create a model response](https://developers.openai.com/api/reference/resources/responses/methods/create)：请求使用 `model`、`input`、可选 `instructions`/`max_output_tokens`；响应文本位于 `output[].content[]` 的 `output_text`，用量包含 input/output/total token。
- [List models](https://developers.openai.com/api/reference/resources/models/methods/list)：返回当前 API Key 可用模型的 `id`、`created`、`object` 和 `owned_by`。
- [API authentication and debugging](https://developers.openai.com/api/reference/overview)：Bearer API Key；可选 `OpenAI-Organization`/`OpenAI-Project`；`X-Client-Request-Id` 必须是 ASCII 且不超过 512 字符；响应 `x-request-id` 用于排障。
- [Error codes](https://developers.openai.com/api/docs/guides/error-codes)：401 认证、403 权限/地区、429 限流或额度、500/503 服务错误需映射到标准 Provider 语义。
- [Current model guidance](https://developers.openai.com/api/docs/guides/latest-model)：当前模型家族和推荐会变化，插件不得把“当前最新”写死为长期产品事实。

OpenAI 官方 Responses 参考没有给出可依赖的提交幂等保证。因此 descriptor 声明 `idempotency="none"`，也不发送未经官方确认的 Idempotency header。不确定的 `SUBMITTING` Task 在重启后进入 `NEEDS_ATTENTION`，防止重复计费。

## 3. 配置与凭据

普通设置：

- `organization`：可选，映射 `OpenAI-Organization`。
- `project`：可选，映射 `OpenAI-Project`。
- `request_timeout_seconds`：1–300 秒，默认 60 秒；Task 剩余时限仍是上界。

凭据：

- `api_key`：必填 secret，只从 `SecretResolver` 临时读取。

禁止把 API Key 放入 settings、日志、异常、DTO、测试 snapshot 或插件数据目录。插件不读取 `OPENAI_API_KEY`，避免绕过 AstraWeft 的 Keychain 边界。

## 4. 模型发现策略

`GET /v1/models` 只提供基础身份，不提供完整 endpoint capability。插件采用保守筛选：

- 接受当前账号可见且 ID 属于 `gpt-4*`、`gpt-5*`、`o1*`、`o3*`、`o4*` 的文本/推理模型及其 fine-tune ID。
- 排除名称中包含 realtime、audio、transcribe、tts、image、embedding、moderation、search、codex、computer-use 或 chat 专用标识的模型。
- 每个模型声明同一最小 `text.generate` Schema；不根据名称猜测价格、上下文窗口或特定采样参数。
- 远端消失模型由现有 Model 同步规则保留历史并标为 unavailable。

模型筛选只是“适合尝试 Responses 文本调用”的保守判断；最终权限和 endpoint 兼容由 OpenAI API 响应确认并映射成可操作错误。

## 5. 请求映射

请求体最小化为：

```json
{
  "model": "<remote_model_id>",
  "input": "<prompt>",
  "instructions": "<optional system instructions>",
  "max_output_tokens": "<optional integer>",
  "store": false
}
```

规则：

- `prompt` 必填且不能为空。
- `instructions` 为空时不发送。
- `max_output_tokens` 可选；未填写时不发送，不在适配器中猜测模型默认值；API 仍是模型实际限制的最终裁决者。
- 固定 `store=false`，不为后续 retrieval 保留响应。
- `X-Client-Request-Id` 使用 Task Attempt 的 ASCII trace ID；它用于诊断，不充当幂等键。
- 不启用插件内部重试；429/5xx 的 retryable/Retry-After 交给 Core 策略。

## 6. 响应与用量映射

- 只收集 `output` 中 `type="message"`、content 中 `type="output_text"` 的文本并按顺序拼接。
- `status="completed"` 返回正常输出。
- `status="incomplete"` 且存在文本时返回部分输出，`finish_reason` 使用 `incomplete_details.reason`。
- API body 内含 error、明确 failed/canceled 或缺少可用文本时抛标准 Provider 错误，不返回空成功。
- `usage.input_tokens`、`output_tokens`、`total_tokens`、cached/reasoning token 明细进入 `Usage.units`。
- Phase 4 不内置易变价格表，`cost_micros=None`、`currency=None`，GUI 显示“未知”。
- `x-request-id` 进入 `provider_request_id` 和 Request Log 安全摘要。

## 7. Core HTTP transport 安全策略

- 每个插件获得按 manifest network hosts 绑定的 transport；OpenAI 只能访问 `api.openai.com`。
- 仅允许 HTTPS、无 URL userinfo、无自动重定向；响应体设置上限。
- Core 统一连接池、超时、User-Agent、网络/超时错误转换和关闭生命周期。
- Transport 不记录 Authorization、请求 body 或完整响应 body。
- Mock transport 使用同样的协议和 HTTPS host policy，不需真实网络。

## 8. 测试门禁

必须通过：

1. 独立 manifest/descriptor/Schema baseline contract。
2. Models API 成功、401、403、429、5xx、超时、坏 JSON 和 secret canary。
3. Responses completed、incomplete、body error、无文本、token usage 与 request ID 映射。
4. Core transport host/scheme/redirect/size/timeout/network 错误策略。
5. OpenAI 插件静态导入边界：不得依赖 Core/Qt/SQLAlchemy/keyring/httpx。
6. Core 源码不得按 OpenAI plugin ID 分支。
7. wheel/sdist 内容、entry point 发现和无密钥本地启动。
8. Live smoke 只能由用户显式配置 Keychain 凭据后手动运行，普通测试永远不调用真实 API。

## 9. 后续扩展

- Streaming 需要 SDK 级增量事件协议，不在同步返回上堆回调特例。
- Background Responses 若启用，必须先验证 remote response ID、retrieve/cancel、幂等和保留策略，再映射为异步 Task。
- Image/Audio/Video 使用各自 operation 和产物协议，不把二进制塞进文本输出。
- 价格通过带生效日期的版本化 catalog 或官方 usage/cost API 接入，不能把网页价格永久写死在插件源码。
