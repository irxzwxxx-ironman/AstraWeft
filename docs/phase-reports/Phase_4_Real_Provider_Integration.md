# Phase 4 真实 Provider 接入验收报告

- 项目：AstraWeft（星纬）
- 日期：2026-07-15
- 状态：LOCAL PASS
- 范围：macOS 本地完整门禁；未调用真实付费 API；Git 与远端 Windows/Linux CI 按用户要求延后

## 1. 阶段交付

### 1.1 Core 统一 HTTP 边界

- Core 持有共享 `httpx.AsyncClient`，Provider 只使用公共 SDK 注入的 transport。
- 静态 manifest 主机权限、HTTPS/443、userinfo、fragment、方法、响应大小和无重定向
  策略由 Core 统一执行。
- Request Log 新增安全 HTTP 元数据：method、URL template、status 和经过约束的远程
  request ID；不保存请求正文、Authorization 或完整 URL。
- 普通 Provider 响应上限 16 MiB；远程 Artifact 使用 512 MiB 流式上限。

### 1.2 OpenAI 同步/即时 Provider

- 独立包 `astraweft-openai-provider`，只依赖 Provider SDK。
- `GET /v1/models` 模型发现与保守文本模型筛选。
- `POST /v1/responses` 文本生成，固定 `store=false`，支持 instructions、可选
  max output tokens、token usage、refusal/incomplete 和 request ID 映射。
- 认证、权限、限流、超时、服务不可用、坏 JSON 等错误均转换为公共 Provider 错误。
- 未找到可依赖的提交幂等保证，因此不发送幂等 Header；不确定提交进入
  `NEEDS_ATTENTION`，禁止自动重复计费。

### 1.3 Runway 远程异步视频 Provider

- 独立包 `astraweft-runway-provider`，只依赖 Provider SDK。
- 首个稳定切片支持 `gen4.5` 文本转视频，覆盖提交、PENDING/THROTTLED/RUNNING
  轮询、精确进度、SUCCEEDED/FAILED/CANCELLED 和 DELETE 取消。
- 依据官方 failure code 区分可重试与不可重试失败，不把远程失败原文暴露给用户或日志。
- 已有 remote task ID 的重启恢复只继续轮询；SUBMITTING 结果不确定时不重复提交。
- 没有可靠 usage/cost 字段时保持成本未知，不根据易变网页价格推算账单。

### 1.4 临时 URL 成果安全落盘

- 成果 URL 主机必须匹配插件 manifest 权限；Runway 首切片只授权 API 主机和
  CloudFront 子域。
- 下载禁止重定向，先检查 Content-Length，再对流式字节计数；空响应和超限响应失败。
- `.partial`、流式 SHA-256、长度复核和原子替换保证数据库不引用半成品。
- Artifact 只保存 `https://<host>/<redacted>`，路径和签名 query 不落库。

## 2. 验收证据

主验证环境：macOS arm64，Python 3.12.13，Qt/PySide6 6.10.3。

| 门禁 | 结果 | 证据摘要 |
|---|---|---|
| Ruff lint / format | PASS | 152 个 Python 文件格式一致，0 lint |
| mypy strict | PASS | 147 个源码与测试文件，0 issues |
| import-linter | PASS | 148 个文件、578 条依赖；7 个契约保持，0 broken |
| pytest | PASS | 218 passed |
| coverage | PASS | 90.45%，高于 90% 门槛 |
| OpenAI 离线 E2E | PASS | Core transport → Provider → Task/Log/DB；`store=false`，无真实调用 |
| Runway 离线 E2E | PASS | submit → running → poll → download；recovery、cancel、uncertain submit 全绿 |
| URL Artifact 安全 | PASS | host 权限、无重定向、大小、空响应、hash、partial 清理和 URL 脱敏 |
| dependency audit | PASS | 第三方依赖无已知漏洞；5 个本地未发布包按预期跳过 PyPI 查询 |
| 5 个包构建 | PASS | Core、SDK、Mock、OpenAI、Runway 各 1 个 sdist + wheel |
| package metadata/content | PASS | 10 个产物 Twine 全绿，5 个 wheel contents 全绿 |
| isolated wheel smoke | PASS | 仅 wheel 安装；发现 mock/openai/runway；迁移、offscreen GUI 启停全绿 |

## 3. Phase 4 退出标准映射

| 退出标准 | 结论 |
|---|---|
| 至少一个同步/即时 Provider 与一个远程异步 Provider 稳定工作 | PASS；OpenAI Responses + Runway async video |
| 全部插件通过统一 contract suite | PASS；Mock、OpenAI、Runway 均通过公共 SDK baseline |
| 限流、错误、取消和用量映射有固定 fixture | PASS；用量缺失时保持未知 |
| Provider 官方 API 变更可只更新对应插件 | PASS；Core 无具体 plugin ID 执行分支，导入契约保持 |

## 4. 安全与审查结论

- API Key 仍只通过 SecretResolver/Keychain 边界读取；普通设置和 SQLite 不保存明文。
- Prompt、Authorization、远程错误原文和临时签名 URL 均有 canary 回归。
- OpenAI 与 Runway 插件不直接依赖 Core、Qt、SQLAlchemy、keyring、httpx 或厂商 SDK。
- Runway 官方输出 URL 的 24–48 小时过期语义通过立即本地化处理。
- 所有测试均为固定离线响应，未发送任何真实生成请求或产生费用。

## 5. 明确延后范围

- Git 操作、远端仓库和 Windows/Linux CI 实际执行继续延后；不能从 macOS 本地结果
  推断其他平台已通过。
- Live smoke 必须由用户显式配置真实凭据和费用上限后手动触发，本阶段未执行。
- OpenAI streaming/background、多模态，Runway 其他模型、上传输入和大于 512 MiB
  成果不在本阶段范围。
- 供应商价格变化快，本阶段不内置或推算价格；版本化价格目录属于后续成本治理。

## 6. 阶段结论

Phase 4 在 macOS 本地范围内达到完整退出标准。公共 Provider SDK 已同时承载同步文本和
远程异步视频，Core 保持无供应商特例；AstraWeft 可以进入 Phase 5 Workflow Engine。
