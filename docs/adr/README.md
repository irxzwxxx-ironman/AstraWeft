# AstraWeft Architecture Decision Records

ADR 记录已经影响或将长期影响架构、兼容性、安全、发布和贡献者工作的决策。

## 状态

- Proposed：正在评审，不得作为既定实现依据。
- Accepted：当前有效，代码和文档必须遵循。
- Superseded：已被新 ADR 取代，保留历史。
- Deprecated：不再推荐，但尚未完全移除。

Accepted ADR 不直接重写。需要改变时新增 ADR，在新文档中引用被取代的编号，并更新本索引。

## 索引

| ADR | 决策 | 状态 |
|---|---|---|
| [000](./ADR-000_Project_Naming.md) | AstraWeft（星纬）名称与技术标识 | Accepted |
| [001](./ADR-001_Modular_Monolith.md) | 分层模块化单体 | Accepted |
| [002](./ADR-002_PySide6_and_qasync.md) | PySide6 与 qasync 桌面运行模型 | Accepted |
| [003](./ADR-003_SQLite_SQLAlchemy_Alembic.md) | SQLite、SQLAlchemy 与 Alembic | Accepted |
| [004](./ADR-004_OS_Keychain_Credentials.md) | 操作系统密钥环凭据策略 | Accepted |
| [005](./ADR-005_Provider_Plugin_API.md) | Provider Plugin API 与可信插件模型 | Accepted |
| [006](./ADR-006_JSON_Schema_and_UI_Schema.md) | JSON Schema 与 UI Schema | Accepted |
| [007](./ADR-007_Open_Source_and_Platform_Policy.md) | Apache-2.0 与平台支持政策 | Accepted |
| [008](./ADR-008_Provider_SDK_Packaging_and_Async_Secrets.md) | Provider SDK 独立打包与异步密钥解析 | Accepted |
| [009](./ADR-009_Durable_Task_Submission_and_Recovery.md) | Task 提交前持久化、稳定幂等与保守恢复 | Accepted |
| [010](./ADR-010_Core_HTTP_and_Remote_Artifact_Boundary.md) | Core 统一 HTTP、插件网络权限与远程成果下载 | Accepted |
| [011](./ADR-011_Immutable_Workflow_Versions_and_Durable_Node_Intent.md) | 不可变 WorkflowVersion 与持久 Node 执行意图 | Accepted |
| [012](./ADR-012_ComfyUI_Execution_Adapter_and_Loopback_Gateway.md) | ComfyUI 独立执行适配器与最小权限 Loopback Gateway | Accepted |
| [013](./ADR-013_Staged_Local_Data_Maintenance.md) | 分阶段本地数据维护与可恢复删除 | Accepted |
| [014](./ADR-014_User_Configured_REST_Provider.md) | 用户配置 REST Provider 与动态网络权限 | Accepted |

## 模板

新 ADR 至少包括：状态、日期、背景、决策、理由、后果、执行守卫和替代方案。涉及安全或数据兼容时还必须写明迁移与回滚策略。
