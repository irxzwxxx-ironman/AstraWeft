# AstraWeft Custom REST Provider 设计说明

- 状态：Approved for implementation
- 日期：2026-07-16
- 插件包：`astraweft-custom-rest-provider`
- 插件 ID：`dev.astraweft.custom-rest-provider`

## 1. 用户目标

用户可在 Provider 配置对话框中填写公网 HTTPS endpoint、认证方式、密钥和一份受约束的
JSON 定义，然后执行“测试连接 → 同步模型 → Playground/Workflow 调用”，无需编写或安装
新 Python 插件。

## 2. 配置结构

Provider 实例字段：

- `endpoint`：必填，仅公网 HTTPS/443，可包含 API base path。
- `auth_mode`：`none | bearer | api_key_header | basic | custom_templates`。
- `auth_header_name` / `auth_prefix`：单 API-Key header 的名称和前缀。
- `request_timeout_seconds`：1–900 秒。
- `additional_allowed_hosts`：Artifact/CDN 公网 DNS 主机 JSON 数组，默认空。
- `definition`：模型和请求声明 JSON 对象。

凭据字段固定为 `api_key`、`api_secret`、`username`、`password`，全部只进入 Secret Store。

## 3. 声明示例

```json
{
  "models": [
    {
      "id": "example-image-v1",
      "name": "Example Image",
      "modality": "IMAGE",
      "operations": ["image.generate", "video.generate"],
      "input_schema": {
        "type": "object",
        "properties": {
          "prompt": {"type": "string", "title": "Prompt", "minLength": 1},
          "width": {"type": "integer", "default": 1024}
        },
        "required": ["prompt"],
        "additionalProperties": false
      },
      "input_ui_schema": {"prompt": {"ui:widget": "textarea"}},
      "requests": {
        "image.generate": {
          "submit": {
            "method": "POST",
            "path": "/images/generations",
            "headers": {"X-Client-Trace": "${trace_id}"},
            "body": {
              "model": "${model_id}",
              "prompt": "${input.prompt}",
              "width": "${input.width}"
            }
          },
          "response": {
            "mode": "sync",
            "output": {
              "data": {"provider_id": "/id"},
              "artifacts": [
                {"kind": "image", "source": "url", "pointer": "/data/0/url"}
              ]
            }
          }
        },
        "video.generate": {
          "submit": {
            "method": "POST",
            "path": "/videos",
            "body": {"prompt": "${input.prompt}"}
          },
          "response": {
            "mode": "async",
            "task_id": "/id",
            "poll": {"method": "GET", "path": "/tasks/${remote_task_id}"},
            "cancel": {"method": "DELETE", "path": "/tasks/${remote_task_id}"},
            "state": "/status",
            "states": {
              "queued": ["queued", "pending"],
              "running": ["running"],
              "succeeded": ["succeeded", "done"],
              "failed": ["failed"],
              "canceled": ["canceled", "cancelled"]
            },
            "progress": "/progress",
            "poll_after_seconds": 2,
            "output": {
              "data": {},
              "artifacts": [
                {"kind": "video", "source": "url", "pointer": "/output/url"}
              ]
            }
          }
        }
      }
    }
  ],
  "health": {"method": "GET", "path": "/health"}
}
```

JSON Pointer 使用 RFC 6901 转义：`~1` 表示 `/`，`~0` 表示 `~`；空字符串指向根值。

## 4. 异步请求

`response.mode="async"` 时包含：

- `task_id`：提交响应中的远程任务 ID pointer。
- `poll`：查询请求的 method/path/query/headers/body。
- `state` / `states`：远程状态 pointer 及 queued/running/succeeded/failed/canceled 别名集。
- `progress`：可选进度 pointer。
- `cancel`：可选的取消请求。
- `poll_after_seconds`：0.1–3600 秒。

路径可使用 `${remote_task_id}`。未知远程状态不会默认失败，而是返回协议错误并保留 Task 现场。

## 5. 响应映射

- `data`：输出字段名到 JSON Pointer。
- `artifacts`：`kind`、`source`、`pointer`、可选 `many/mime_type/filename_hint`。
- `usage.units`：单位名到 JSON Pointer，只接受非负整数或字符串。
- `usage.cost_micros` + `currency`：两者必须同时存在才产生已知费用。
- `finish_reason`：可选终止原因 pointer。

pointer 不存在时，必填映射失败；仅显式标记 `optional=true` 的项可忽略。这防止远程协议漂移被
错误地记为成功。

## 6. 安全与限制

- endpoint 和额外主机由 Core 验证并绑定 transport；返回 3xx 不自动跟随。
- 定义不得包含明文 secret；`custom_templates` 可在 header/body/query 中引用
  `${secret.api_key}`、`${secret.api_secret}`、`${secret.username}`或 `${secret.password}`。
- 请求/响应 body 不进入 Request Log；只记录脱敏 method/path/status/request ID。
- 当前不支持 multipart、文件上传、WebSocket/streaming、OAuth 授权码流和非 JSON 二进制请求。
- 这些限制不影响返回 URL/base64/text/JSON Artifact；Core 仍负责大小限制、下载和 SHA-256。

## 7. 验收门禁

1. 独立插件合约、wheel 和 entry point 发现通过。
2. 同步文本、Artifact、异步 poll/cancel 与恢复通过 fake transport 测试。
3. 自定义 endpoint 仅放行实例主机，额外 host 需显式授权，内网/IP/非 HTTPS 被拒绝。
4. 认证值不进入 SQLite、宣言、日志、异常或 DTO `repr`。
5. 非法模板、header、JSON Pointer、状态映射、响应类型和超限定义在远程调用前拒绝。
6. Provider 配置对话框可编辑/恢复 object/array JSON，错误位置可读，中英文界面无固定文案回归。
