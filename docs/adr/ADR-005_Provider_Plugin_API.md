# ADR-005：Provider Plugin API 与可信插件模型

- 状态：Accepted
- 日期：2026-07-15

## 背景

Provider 在认证、模型、同步/异步、轮询、取消、用量和产物方面差异明显。简单 `BaseProvider.generate_image()` 会把特例推入 Core。Python 插件本身可以执行任意本地代码，不能被误称为沙箱。

## 决策

- 插件通过 `astraweft.providers` Python entry point 发现。
- Manifest 声明稳定 plugin ID、版本、Plugin API 范围、能力和透明权限。
- Core 注入 HTTP、Secret、Clock、Logger 和受控数据目录。
- Submit 返回 completed 或 accepted；轮询、重试、取消和 Task 状态由 Core 管理。
- DTO、错误、幂等、用量、产物和兼容语义遵循 Provider 插件规范。
- 每个插件只依赖公开 Provider SDK，并通过统一 contract suite。
- v1 插件是用户信任的本地代码；不宣称隔离。未来不受信任插件需要独立进程与新 IPC ADR。

## 后果

- 新 Provider 不修改 Core 或 GUI，能力通过 descriptor 和 Schema 呈现。
- 真实 Provider 前必须先有 Mock Provider 验证异常与恢复。
- SDK 需要独立版本和兼容策略，升级成本高于随意内部基类，但生态更稳定。

## 守卫

禁止 Core/GUI 出现 `if plugin_id == ...`。插件不能导入 AstraWeft 私有 Core、Qt 或数据库模块。Plugin API 变更需要兼容测试和 ADR。

## 替代方案

- Core 内置所有 Provider：扩展和维护不可持续，拒绝。
- 任意 Python 文件扫描：缺少包元数据和兼容性，拒绝。
- v1 即沙箱化：显著扩大范围，待实际第三方分发需求出现后设计。
