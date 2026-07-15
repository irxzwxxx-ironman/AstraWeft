# ADR-008：Provider SDK 独立打包与异步密钥解析

- 状态：Accepted
- 日期：2026-07-15
- 关联：ADR-002、ADR-004、ADR-005、ADR-006

## 背景

Provider 插件必须能独立开发、测试和发布，同时不能导入 AstraWeft Core、Qt、SQLAlchemy 或具体密钥环实现。原接口草案把 `SecretResolver.get` 定义为同步调用，但生产实现可能访问 macOS Keychain、Windows Credential Manager 或 Linux Secret Service；同步调用会阻塞 qasync 所在的 GUI 事件循环。

## 决策

1. 公共接口作为独立工作区包 `astraweft-provider-sdk` 发布，Core 与插件只通过该包交换不可变 DTO、Protocol、标准错误和 contract test kit。
2. Provider 插件作为独立发行包，通过 `astraweft.providers` entry point 注册，并携带可在导入 Python 代码前读取的 `plugin.toml`。
3. Core 先检查 manifest、API/Python 兼容范围、包指纹和 plugin ID 冲突，再隔离导入；任一插件失败不得阻止应用启动。
4. `SecretResolver.get` 是异步接口：

   ```python
   class SecretResolver(Protocol):
       async def get(self, credential_ref: str, field: str) -> SecretValue: ...
   ```

5. 插件只接收 Core 注入的 `ProviderContext`。Application 通过 `ProviderContextFactory` 端口请求上下文，不直接依赖 Infrastructure 构造器。
6. SDK、Mock Provider 和 Core 分别构建 wheel；导入边界由 import-linter 自动验证。

## 理由

- 独立包把公开兼容面从 Core 私有实现中物理分离。
- 异步密钥解析允许 Keyring 适配器把阻塞 I/O 放入工作线程，不冻结 GUI。
- 静态 manifest 让 Core 在执行不受信插件代码前完成大部分兼容性判断。
- 上下文工厂端口维持 Application → Ports 的依赖方向。

## 后果

- 插件作者必须在密钥读取处使用 `await`，并不得持久化或记录解包值。
- SDK 的破坏性变更需要新的主 API 版本和兼容范围。
- 当前插件是“本机已安装的可信代码”，不是安全沙箱；权限声明用于审计和未来隔离，不代表 OS 级强制。
- 第一个真实网络 Provider 接入前，Core HTTP transport 保持显式不支持；Mock Provider 不需要网络权限。

## 执行守卫

- SDK 禁止导入 `astraweft`、插件、Qt 和 SQLAlchemy。
- 插件禁止导入 Core、Qt、SQLAlchemy 和 keyring。
- Core Presentation/Application 禁止按插件 ID 分支。
- contract suite 必须覆盖 manifest/descriptor、一致性、Schema、健康检查、模型目录和幂等关闭。
- 数据库与日志 secret canary 扫描必须为零命中。

## 替代方案

- 将 SDK 留在 Core 包内：拒绝，会形成隐式私有 API 和循环依赖风险。
- 同步密钥读取：拒绝，会阻塞 GUI，且迫使插件了解线程策略。
- 直接扫描插件目录并立即 import：拒绝，无法在执行代码前进行可靠兼容性检查。
