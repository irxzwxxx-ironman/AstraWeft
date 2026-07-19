# ADR-014：用户配置的 REST Provider 与动态网络权限

- 状态：Accepted
- 日期：2026-07-16

## 背景

当前 Provider 边界可以通过独立插件扩展，但普通用户只能使用已安装插件预编程的 API。
OpenAI 和 Runway 的 endpoint 固定，manifest 的网络主机权限也是静态列表。若直接把任意 URL、
Authorization 和可执行模板放进设置，会绕过 Secret Store、SSRF 防护、日志脱敏和可重现任务语义。

## 决策

- 新增独立 `astraweft-custom-rest-provider` 包，只依赖公共 Provider SDK。
- 用户以 JSON 宣言模型、参数 Schema、HTTP 方法/路径/查询/头/请求体、同步输出或异步轮询/
  取消和 JSON Pointer 响应映射。不接受 Python、JavaScript、Jinja、Shell 或任意表达式。
- 模板只支持受限变量：`${input.<field>}`、`${model_id}`、`${remote_task_id}`、`${trace_id}`、
  `${idempotency_key}` 和固定 `${secret.<field>}`；精确占位符保留 JSON 类型，字符串内插值只产生文本。
- 凭据仅通过固定 credential schema 进入 Secret Store；支持 none、Bearer、单 API-Key header、
  HTTP Basic 和安全 secret 模板。Host、Content-Length等 transport headers 始终被拒绝。
- `ProviderContext` 增加 Core 已验证的实例 endpoint。manifest 只能通过显式
  `user_configured_endpoint = true` 请求将该 endpoint 主机加入 transport allowlist。
- 额外 Artifact/CDN 主机仅能从 manifest 声明的设置字段读取，必须是显式公网 DNS 主机；不允许
  wildcard、IP literal、localhost、`.local`、userinfo、非 HTTPS 或非 443 端口。
- 第一版边界是 JSON REST：GET/POST/PUT/PATCH/DELETE/HEAD、query、JSON body、JSON/text 响应、
  URL/base64/text/JSON Artifact。multipart、任意二进制上传和 OAuth 交互登录需要后续独立协议，不用
  不受控字节通道绕过 Core。

## 后果

- 用户不写 Python 即可接入绝大多数公网 JSON AI API，包括同步和异步任务。
- 实例 endpoint 与追加主机仍由 Core 创建 allowlist，插件不能根据远端响应自由跳转。
- 宣言错误在保存 Schema 校验、Provider 连接测试和任务提交三层被阻止，不会以“空成功”吞掉。
- 由于定义可改变远端副作用，Generic Provider 始终声明 `idempotency="none"`；不确定提交仍进入
  `NEEDS_ATTENTION`，不因用户配置了 header 就假设远端具有幂等契约。

## 守卫

- Core 不按 Custom REST plugin ID 分支；动态 endpoint 是 manifest + SDK 通用能力。
- 定义与凭据使用 secret canary 测试，SQLite、Request Log、JSONL、异常和 `repr` 都不得出现原值。
- 未被 manifest/用户 allowlist 明确授权的 host 必须由 Core transport 拒绝。
- 任何新增模板变量、认证模式或二进制请求通道都需要 ADR 复核。
