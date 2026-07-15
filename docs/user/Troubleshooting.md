# AstraWeft 故障排查

先保留现场，再尝试修复。不要直接删除数据库、锁文件、备份或任务记录；这些文件可能是恢复远端
任务和避免重复计费所需的唯一证据。

## 应用无法启动或立即退出

1. 确认当前数据目录没有另一个 AstraWeft 实例正在运行。
2. 检查磁盘是否有足够空间，数据目录是否可写。
3. 从“设置与本地数据”或日志目录获取最近的 `astraweft.jsonl`。
4. 若升级后失败，保留 `data/backups/*-pre-migration.db`，不要反复启动或覆盖它。
5. 使用当前版本重新安装不会删除用户数据；仍失败时导出或手工收集脱敏诊断信息。

开发候选包没有 Developer ID 签名与公证，不代表公开发行包的 Gatekeeper 行为。不要通过关闭系统
安全功能或全局允许未知应用来“修复”安装问题。

## 显示“凭据存储不可持久化”

macOS 需要可用的 Keychain，Windows 需要 Credential Manager，Linux 需要兼容的 Secret Service。
未签名开发解释器或无桌面会话的 Linux 环境可能拒绝访问凭据存储。

- 当前会话可继续使用 Mock Provider；
- 不要把真实 Key 写入设置文件作为替代方案；
- AstraWeft 本机网关会使用仅驻留内存的会话 Token 继续运行，但独立的 ComfyUI Custom Node
  无法跨进程读取该 Token；系统密钥环恢复前不要把这视为可用的 ComfyUI 外部接入；
- 修复系统凭据服务后，重新启动并在 Provider 页面重新保存凭据。

## Provider 连接或模型同步失败

- 确认选择了正确插件，插件管理器没有将其禁用；
- 重新输入凭据并执行连接测试；
- 检查系统时间、代理、DNS 和 Provider 服务状态；
- 遇到 `authentication`、`rate_limit`、`timeout`、`unavailable` 时按界面建议处理；
- 不要对状态为 `UNKNOWN_REMOTE_STATE` 的付费任务直接重复提交，先到 Provider 控制台核对。

普通日志只保存脱敏请求元数据，不保存 Authorization、签名 URL 或完整敏感正文。

## 任务一直等待或重启后没有自动重试

这是有意的安全策略。对没有可靠幂等键的远端提交，网络断开可能发生在 Provider 已受理但本机尚未
收到响应之后。自动重试可能造成重复计费，因此 AstraWeft 会要求人工核对。

对于可恢复异步任务，可使用 Provider 返回的远端任务 ID 继续轮询。取消操作也以 Provider 的最终
状态为准，本机不会把“已发送取消请求”误写成“远端已取消”。

## ComfyUI 无法连接 AstraWeft

- 确认 AstraWeft 正在运行且凭据存储可用；
- 确认 Custom Node 来自当前发行包的 `extras/AstraWeftGateway`；
- 只连接 `127.0.0.1`，不要把网关暴露到局域网或公网；
- 重启 ComfyUI，使 Custom Node 和 Token 配置重新加载；
- 在 AstraWeft 的 ComfyUI 页面重新测试实例连接和能力。

Host、Origin、认证、请求体大小、速率或文件路径校验失败时，网关会直接拒绝请求。

## 产物显示缺失

产物记录和文件是两个不同层次。文件被外部移动或删除后，AstraWeft 会保留血缘记录并显示缺失，
不会静默伪造结果。

- 检查数据目录是否被移动、同步软件是否未完成下载；
- 检查产物回收站并尝试恢复；
- 不要把同名文件复制回来冒充原产物；内容哈希不一致时应重新运行或从可信备份恢复。

## 数据库或升级问题

设置页的数据库检查应满足：`integrity_check = ok`、外键问题数为 0、revision 为当前版本。

恢复流程：

1. 退出 AstraWeft；
2. 复制整个数据目录作为现场备份；
3. 使用设置页选择通过验证的 AstraWeft `.db` 备份；
4. 确认影响并重启；
5. 验证任务、工作流、产物血缘和 Provider 配置，再继续付费任务。

不要用普通文件复制覆盖一个正在使用的 SQLite 数据库。

## 提交安全或缺陷报告

公开报告中不要附带 API Key、网关 Token、签名 URL、数据库、真实提示词或用户产物。优先附带：

- AstraWeft 版本、操作系统和安装方式；
- 可使用 Mock Provider 复现的最小步骤；
- 脱敏诊断包和相关 Trace ID；
- 期望行为、实际行为以及是否涉及凭据、文件、费用或远端执行。

安全问题遵循根目录 [SECURITY.md](../../SECURITY.md) 的私密报告流程。
