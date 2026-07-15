# ADR-011：不可变 WorkflowVersion 与持久 Node 执行意图

- 状态：Accepted
- 日期：2026-07-15
- 关联：ADR-001、ADR-003、ADR-006、ADR-009

## 背景

工作流发布后，Provider 配置、模型 Schema、节点连线和默认参数仍可能变化。若运行只
引用可编辑草稿，历史结果无法解释。另一方面，若调度器先创建 Task、再写 NodeRun，
进程在两次写入之间退出会留下无法关联的 Task；恢复时再次创建可能导致重复远程调用。

## 决策

1. Workflow 保存稳定身份；WorkflowVersion 保存完整定义。PUBLISHED/ARCHIVED 版本
   永不可修改，编辑时产生新的唯一 Draft。
2. WorkflowRun 固定引用一个已发布版本并保存 definition checksum；NodeRun 保存
   node key、解析输入、输出和错误快照。
3. 节点使用稳定的、预先生成的 `planned_task_id`。调度器先持久化该执行意图，再让
   TaskService 以相同 ID 幂等确保 Task 存在，最后关联正式 task_id。
4. 运行恢复只推进非终态 NodeRun；已有 planned ID 永不重新生成。Task 层继续负责
   Provider 远程幂等和“不确定提交转人工确认”。
5. 节点输出 Artifact 必须完成本地校验后才能标记节点成功；输入/输出 Artifact 通过
   ArtifactLink 与端口建立血缘。
6. 输入映射只允许工作流输入、JSON 常量和显式 DAG 边，不执行任意代码或字符串路径。
7. WorkflowNode 的 Provider/Model 身份在定义层使用发布时校验的软引用，使跨安装导入
   可以先保存为未解析 Draft；Task 执行账本继续使用严格 Provider/Model 外键。

## 理由

- 不可变定义与运行快照共同保证历史可解释和可导出。
- 持久 planned ID 关闭 NodeRun/Task 之间的重复创建窗口。
- 把远程调用继续委托给 Task Runtime，避免 Workflow Engine 复制 Provider 重试、
  取消、轮询和恢复逻辑。
- 受限映射可静态验证、可序列化、可跨平台，并消除代码注入面。

## 后果

- Draft 保存采用完整定义替换；大型画布需要增量 UI 状态，但持久化命令仍保持原子。
- 发布后的小修改也会产生新版本，这是可复现性的有意成本。
- NodeRun 需要同时保存 planned_task_id 与 task_id；前者是恢复意图，后者是已建立引用。
- 条件、人工确认和 ComfyUI 类型在执行器上线前会被发布校验阻止。
- 未重新绑定本机 Provider/Model 的导入草稿可查看和编辑，但绝不能发布或运行。

## 执行守卫

- Repository 禁止替换非 Draft 定义；数据库只允许一个 Draft 和唯一版本号。
- TaskService 对显式 task ID 的重复 create 必须验证定义一致后返回既有 Task。
- DAG、端口、Schema、secret key、checksum 和进程中断窗口必须有自动化测试。
- Core/GUI 不得按具体 Provider plugin ID 决定工作流执行。

## 迁移与回滚

Phase 5 新增表，不修改既有 Provider/Task/Artifact 数据。回滚 migration 仅允许在不存在
需保留的工作流数据时执行。应用级回滚不得删除已发布定义或运行血缘。

## 替代方案

- 运行直接引用可编辑 Workflow：拒绝，历史不可复现。
- 深拷贝定义到每个 Run、但不保留版本：拒绝，去重、版本历史和导入兼容变差。
- 先创建 Task 再写 NodeRun：拒绝，崩溃窗口可能重复计费。
- 允许 Python/Jinja 表达式：拒绝，难以静态验证且扩大安全与跨平台风险。
