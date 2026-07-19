# AstraWeft（星纬）设计文档索引

## 当前工程基线

1. [架构设计 v2](./Local_AI_Workflow_Manager_Architecture_v2.md) — 产品定位、总体架构和原始开发要求，最高层范围基线。
2. [架构评审与缺口分析](./Architecture_Review_and_Gap_Analysis.md) — 对 v2 的合理性判断、遗漏、风险与优化建议。
3. [产品级实施计划](./Product_Implementation_Roadmap.md) — 模块顺序、阶段交付、测试、审查和发布门禁。
4. [详细技术设计（模块级）](./Local_AI_Workflow_Manager_Detailed_Technical_Design.md) — 分层、模块职责、状态机、恢复、安全与性能。
5. [数据库 ER 设计](./Local_AI_Workflow_Manager_Database_ER_Design.md) — 实体、关系、约束、索引、迁移和保留策略。
6. [GUI 原型设计](./Local_AI_Workflow_Manager_GUI_Prototype_Design.md) — Dark Cyber AI 界面结构、页面、组件、交互和状态。
7. [Provider 插件接口规范](./Local_AI_Workflow_Manager_Provider_Plugin_Interface_Spec.md) — 插件发现、DTO、能力、错误、幂等、兼容和合约测试。
8. [ADR-000：项目名称与技术标识](./adr/ADR-000_Project_Naming.md) — `LingWeave` 冲突核验与 `AstraWeft（星纬）` 正式决策。
9. [ADR 索引](./adr/README.md) — 当前有效的架构决策及变更规则。
10. [质量门禁](./development/Quality_Gates.md) — 本地、CI、覆盖率与阶段审查标准。
11. [Phase 0 验收报告](./phase-reports/Phase_0_Engineering_Baseline.md) — 已验证证据、修复项与远程 CI 待验项。
12. [Phase 1 验收报告](./phase-reports/Phase_1_Local_Runnable_Foundation.md) — 本地桌面基础设施与 Design System。
13. [Phase 2 验收报告](./phase-reports/Phase_2_Provider_Model_Loop.md) — Provider SDK、Mock 插件与 Provider/Model 闭环。
14. [Phase 3 验收报告](./phase-reports/Phase_3_Task_Runtime_Playground_Logs_Artifacts.md) — Durable Task Runtime、Playground、日志和产物闭环。
15. [OpenAI Responses API Adapter 设计](./provider-adapters/OpenAI_Responses_API_Adapter_Design.md) — Phase 4 首个真实 Provider 的官方契约、安全边界和测试门禁。
16. [Runway 异步视频 Adapter 设计](./provider-adapters/Runway_Async_Video_Adapter_Design.md) — 远程提交、轮询、取消、恢复和临时成果下载契约。
17. [Phase 4 验收报告](./phase-reports/Phase_4_Real_Provider_Integration.md) — OpenAI、Runway、Core HTTP 与 URL Artifact 完整本地门禁。
18. [Workflow Engine 实施设计](./workflows/Workflow_Engine_Implementation_Design.md) — Phase 5 不可变 DAG、调度、恢复、血缘和 GUI 契约。
19. [Phase 5 验收报告](./phase-reports/Phase_5_Workflow_Engine.md) — 不可变工作流、持久 DAG 调度、Artifact 血缘、画布与运行观察器。
20. [ComfyUI 集成实施设计](./comfyui/ComfyUI_Integration_Implementation_Design.md) — Phase 6 实例/模板、持久执行、WebSocket 进度、产物与 Loopback Gateway 契约。
21. [Phase 6 验收报告](./phase-reports/Phase_6_ComfyUI_Integration.md) — ComfyUI 双向集成、恢复语义、Loopback 安全、Custom Node 与发行包证据。
22. [本地数据维护实施设计](./operations/Local_Data_Maintenance_Implementation_Design.md) — Phase 7 备份/恢复、目录迁移、回收站、保留与脱敏诊断契约。
23. [Phase 7 产品完善验收报告](./phase-reports/Phase_7_Product_Hardening.md) — 查询、成本、产物、运维、本地化、无障碍与 10 万/100 万规模门禁证据。
24. [用户入门](./user/Getting_Started.md) — 安装、Mock 首次任务、真实 Provider、工作流、ComfyUI、备份与快捷键。
25. [故障排查](./user/Troubleshooting.md) — 启动、凭据、远端不确定状态、ComfyUI、产物和数据库安全恢复。
26. [Provider 插件开发指南](./development/Provider_Development_Guide.md) — 独立包、manifest、Schema、运行边界、合约测试和 wheel 验证。
27. [Beta 发布、迁移与回滚政策](./release/Beta_Release_and_Rollback_Policy.md) — SemVer、候选提升、自动备份、升级与二进制/数据库配套回滚。
28. [Phase 8 本地发行工程报告](./phase-reports/Phase_8_Local_Release_Engineering.md) — macOS 候选包、冷启动、升级回滚、SBOM、许可证与外部门禁。
29. [Custom REST Provider 设计](./provider-adapters/Custom_REST_Provider_Design.md) — 任意 HTTPS JSON API、多接口转发、密钥注入和响应映射。

## 历史资料

- [架构设计 v1](./Local_AI_Workflow_Manager_Architecture.md) — 保留用于追踪早期设计，不作为新实现的优先依据。

## 文档优先级

出现冲突时按以下顺序处理：

1. 用户最新确认的产品决策与 ADR。
2. 架构设计 v2 的产品定位和不可变原则。
3. 产品级实施计划与架构评审结论。
4. 各模块详细规范。
5. 历史 v1 文档。

若详细规范需要改变 v2 的核心方向，必须先提交 ADR 并获得评审确认，不能通过实现代码隐式改变架构。

## 当前阶段

Phase 0–7 已通过 macOS 本地门禁。Phase 8 的 macOS 本地发行工程、manifest v2 独立复核与归档
解包回验已通过，三平台候选工作流已定义；平台签名、Windows/Linux 原生执行、三平台干净机与最终
恶意软件扫描仍开放，因此尚未宣告 Beta 完成。

已确认：

- 正式项目名为 `AstraWeft（星纬）`。
- 开源许可证为 Apache-2.0。
- macOS 主开发，Windows 同步 CI，Linux 在 Beta 前纳入。
- 按阶段质量门禁推进，每阶段完成审查、测试和验收后继续。
- GitHub 远端已配置；跨平台 CI 尚待实际推送和原生执行，Windows/Linux 状态不得从本地结果推断为通过。
