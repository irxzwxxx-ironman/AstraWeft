# ADR-004：凭据默认存入操作系统密钥环

- 状态：Accepted
- 日期：2026-07-15

## 背景

AstraWeft 管理可产生费用的 Provider 凭据。把密钥加密后与解密密钥一起保存在本地数据库或配置目录，不能提供可靠边界；日志、崩溃包和临时 URL 也可能泄密。

## 决策

- macOS 使用 Keychain，Windows 使用 Credential Manager，Linux 使用 Secret Service。
- SQLite 只保存 `credential_ref`、凭据类型和脱敏提示，不保存 secret。
- 没有可用密钥环时只提供会话临时凭据；不默认明文降级。
- SecretValue 的字符串表示始终脱敏，插件仅在构建请求时短暂解包。
- 请求日志、异常、trace、导出包和 URL 经过统一递归脱敏。
- 自动化测试使用 fake SecretStore 与 secret canary，不访问开发者真实密钥环。

## 后果

- 数据库备份不会包含可直接使用的 API Key，跨机器恢复需要重新授权。
- Linux 桌面环境可能没有 Secret Service，需要清晰的降级体验。
- OAuth 刷新、删除孤立凭据和多账号需要显式生命周期管理。

## 守卫

禁止在 ORM、Pydantic 普通 DTO、settings 或日志字段中定义明文 secret。CI 扫描 canary；任何诊断导出在写文件前再次脱敏。

## 替代方案

- SQLite 内加密：密钥管理边界不足，拒绝作为默认方案。
- `.env`：仅允许开发者手工测试，不是产品凭据存储。
- 强制云端 Secret Manager：违背 Local First，拒绝。
