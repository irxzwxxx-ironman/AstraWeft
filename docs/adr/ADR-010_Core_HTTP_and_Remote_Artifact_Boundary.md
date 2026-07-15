# ADR-010：Core 统一 HTTP 与远程成果下载边界

- 状态：Accepted
- 日期：2026-07-15
- 关联：ADR-005、ADR-008、ADR-009

## 背景

真实 Provider 需要认证请求、模型目录、同步响应和异步任务轮询；视频成果通常以
短期签名 URL 返回。若插件自行创建 HTTP 客户端、任意访问 URL 或直接写本地成果
目录，Core 无法统一限制主机、重定向、响应大小、日志脱敏和关闭生命周期。把签名
URL 直接留给 GUI 又会在过期后丢失成果，并可能泄露临时凭据。

## 决策

1. Core 拥有唯一异步 HTTP 连接池，通过 Provider Context 向每个插件注入按静态
   manifest `permissions.network` 绑定的受限 transport。
2. Provider API 和远程成果下载都只允许 HTTPS、标准 443 端口、无 userinfo、无
   fragment，且目标主机必须匹配精确或 `*.` 子域权限。
3. 不自动跟随重定向。插件不得通过响应 Location 把 Core 带出声明的网络边界。
4. 普通 API 响应限制为 16 MiB；远程成果使用独立流式路径，单文件限制为 512 MiB，
   同时校验 Content-Length 和实际接收字节。
5. 远程成果写入同目录 partial 文件，流式计算 SHA-256，完成后原子替换；数据库
   只记录已完成文件、哈希、大小和不含路径/query 的脱敏来源。
6. HTTP 观测只记录方法、主机、状态、字节数和 trace ID。Authorization、请求正文、
   完整 URL、query 和响应正文不得进入普通日志或 Request Log。
7. Task Application 只把本次 Provider 的静态网络权限交给 ArtifactWriter，不按
   plugin ID 或 Provider 名称加入特例。

## 理由

- 静态权限在导入插件代码前即可检查，运行时请求不能扩大范围。
- Core 统一连接池避免每个插件重复管理 TLS、超时和关闭。
- 成果立即本地化可抵御临时 URL 过期，同时 partial + hash 避免半成品进入数据库。
- 不记录完整 URL 可保护签名参数；禁止重定向让权限判断保持单跳、可审计。

## 后果

- 需要 CDN 的插件必须在 manifest 中显式声明成果主机模式；声明过宽会在插件审查时
  被视为安全风险。
- 不支持跨主机重定向；Provider 若改变成果分发方式，需要更新对应插件 manifest，
  而不是放宽 Core 全局策略。
- 超过 512 MiB 的单文件成果当前会安全失败；未来若需要大文件，应新增磁盘配额、
  断点续传和用户确认协议，而不是直接提高默认上限。

## 执行守卫

- Core transport、下载器、主机匹配、重定向、大小限制、超时和 partial 清理必须有
  离线测试。
- 每个真实 Provider 必须有导入隔离 contract，禁止直接依赖 HTTP 实现包。
- secret/prompt/signed-URL canary 不得出现在 SQLite、Request Log 或 JSONL 日志。
- Core 和 GUI 中禁止出现 OpenAI、Runway 等具体 plugin ID 的执行分支。

## 迁移与回滚

该决策不改变数据库 Schema。回滚 Runway 插件不会影响既有本地 Artifact；关闭 URL
下载只会使新的远程 URL 成果安全失败。若替换 HTTP 实现，必须保持 Provider SDK
transport 协议和上述安全行为兼容。

## 替代方案

- 插件自行使用厂商 SDK/HTTP 客户端：拒绝，权限、连接池、日志和依赖不可统一。
- 允许任意 HTTPS 成果 URL：拒绝，恶意或被攻陷插件可把 Core 变成 SSRF/下载代理。
- 把签名 URL 原样保存并由 GUI 打开：拒绝，存在泄露、过期和不可复现问题。
- 自动跟随重定向并只检查首个 URL：拒绝，后续目标可绕过静态主机权限。
