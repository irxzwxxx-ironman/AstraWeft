# ADR-001：采用分层模块化单体

- 状态：Accepted
- 日期：2026-07-15

## 背景

AstraWeft 是 Local First 桌面应用，需要统一 GUI、任务调度、SQLite、文件、插件和 ComfyUI 生命周期。初期引入微服务、Redis 或独立 Worker 会增加部署和恢复复杂度，但无边界的单体会导致 Qt、ORM 和 Provider SDK 渗入业务规则。

## 决策

v1 采用单进程分层模块化单体：

```text
Presentation → Application → Domain
                         ↘ Ports ← Infrastructure / Plugins
Bootstrap 负责装配所有层
```

- Domain 不依赖框架和外部系统。
- Application 定义用例与事务边界。
- Ports 定义 Repository、Provider、Secret、Storage、Clock 等接口。
- Infrastructure 和 Plugins 实现 Ports。
- Presentation 只通过 Application DTO 和用例工作。
- Bootstrap 是允许了解全部实现的 composition root。

## 后果

- 单包安装和本地调试简单，仍保留未来拆 Worker 的接口边界。
- 需要持续防止跨层捷径；使用 import-linter、评审和测试执行。
- 不提前实现 PostgreSQL、Redis 或分布式一致性，但数据模型避免依赖 SQLite 隐式类型。

## 守卫

架构依赖规则写入 `pyproject.toml` 并在 CI 执行。任何新的跨层例外必须通过 superseding ADR，不能以循环依赖或临时导入方式解决。

## 替代方案

- 无分层单体：短期更快，长期无法维护和测试，拒绝。
- 微服务/多进程优先：与 Local First 零依赖目标冲突，暂不采用。
