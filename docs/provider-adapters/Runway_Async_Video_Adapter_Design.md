# Runway 异步视频 Provider 适配器设计

状态：Phase 4 实现基线  
版本：0.1  
日期：2026-07-15

## 1. 范围与依据

首个版本只适配 Runway `gen4.5` 的文本转视频能力，操作名为
`video.generate`。接口依据 [Runway 官方 API Reference](https://docs.dev.runwayml.com/api/)、
[官方 Python SDK](https://github.com/runwayml/sdk-python) 的 OpenAPI 生成代码、
[任务失败说明](https://docs.dev.runwayml.com/errors/task-failures/) 和
[成果格式说明](https://docs.dev.runwayml.com/assets/outputs/)：

- `POST /v1/text_to_video` 提交异步任务；
- `GET /v1/tasks/{id}` 查询任务；
- `DELETE /v1/tasks/{id}` 取消或删除任务；
- `GET /v1/organization` 验证凭据；
- 所有 API 请求固定发送 `X-Runway-Version: 2024-11-06`。

不进行付费在线调用。自动化测试使用固定的离线响应，并通过 Core 的真实 HTTP
边界验证请求、状态映射、持久化和成果落盘。

## 2. 插件边界

插件包为 `astraweft-runway-provider`，插件 ID 为
`com.runwayml.api-provider`。插件只依赖公开的 Provider SDK，不直接依赖
Core、HTTP 客户端、GUI、数据库或 Runway SDK。

网络权限声明为：

- `api.dev.runwayml.com`：生成与任务 API；
- `*.cloudfront.net`：只供 Core 对 Provider 返回的临时成果地址做受限下载。

Core 仍负责实际网络连接池、主机权限校验、大小上限、安全日志、原子写入和
SHA-256 校验。插件不能拿到本地成果目录。

## 3. 模型与参数

静态模型目录只发布 `gen4.5`，避免把未经当前官方契约验证的模型混入首个稳定
切片。输入参数：

| 字段 | 类型 | 约束 | 默认值 |
|---|---|---|---|
| `prompt` | string | 1–1000 个 UTF-16 code units | 必填 |
| `duration` | integer | 2–10 秒 | 5 |
| `ratio` | enum | `1280:720` / `720:1280` | `1280:720` |
| `seed` | integer | 0–4294967295 | 可选 |

插件提交时映射为 `promptText`、`duration`、`ratio`、`seed`，模型字段固定使用
任务快照中的远程模型 ID。Runway 未声明本适配切片可用的幂等提交协议，因此
descriptor 的 `idempotency` 为 `none`；崩溃发生在提交结果落库前时，Core
必须转为 `NEEDS_ATTENTION`，不得自动重复付费提交。

## 4. 状态、重试与取消

| Runway 状态 | AstraWeft 状态 | 处理 |
|---|---|---|
| `PENDING` / `THROTTLED` | `queued` | 至少 5 秒后轮询 |
| `RUNNING` | `running` | 将 0–1 进度换算为 0–100 |
| `SUCCEEDED` | `succeeded` | 立即下载临时成果并本地持久化 |
| `FAILED` | `failed` | 依据 `failureCode` 决定是否可重试 |
| `CANCELLED` | `canceled` | 本地进入终态 |

`SAFETY`、`ASSET.INVALID` 不自动重试；`INPUT_PREPROCESSING.INTERNAL`、
`THIRD_PARTY.UNAVAILABLE`、`INTERNAL` 或缺失 failure code 可延迟重试。
HTTP 429、502、503、504 可重试；400、401、403、404、405、422 不自动重试。
取消仅由运行中或轮询中的本地任务触发，成功的 DELETE 视为远程终态取消。

## 5. 成果下载安全

成功响应中的 URL 会在 24–48 小时内失效，所以不能只把 URL 暴露给 GUI。
Core 下载器执行以下约束：

1. 只允许 HTTPS、标准 443 端口、无 userinfo、无 fragment；
2. 主机必须匹配插件静态 manifest 的网络权限；
3. 禁止重定向，避免权限边界在下载过程中漂移；
4. 单个成果最大 512 MiB，先检查 `Content-Length`，再对流式字节二次计数；
5. 只记录方法、主机、状态、字节数和 trace ID，不记录完整 URL 或 query；
6. 写入 `.partial` 文件，边下载边计算 SHA-256，完成后用原子替换发布；
7. 失败时删除 partial 文件，数据库中不生成半成品 Artifact。

本地 Artifact 只保存脱敏来源 `https://<host>/<redacted>`，不保存路径中的潜在
标识或临时签名查询参数。

## 6. 可观测性与隐私

Request Log 只持久化安全模板：`/v1/text_to_video` 或
`/v1/tasks/{task_id}`。请求正文、Prompt、API Key、远程失败原文和成果签名
URL 均不得进入日志。远程 request ID 仅接受非空 ASCII 且最多 512 字符。
Runway 当前任务响应没有可靠的 usage/cost 字段，因此成本保持未知，不根据易变
价格表推算账单。

## 7. 离线验收

门禁至少覆盖：插件 manifest/descriptor 契约、参数校验、HTTP 错误归一化、
提交/轮询/成功/失败/取消映射、结果 URL 安全下载、大小限制、无重定向、原子
落盘、请求日志脱敏、RUNNING/POLLING 崩溃恢复继续轮询，以及 SUBMITTING 不
重复提交。所有测试均不得访问真实 Runway 服务。
