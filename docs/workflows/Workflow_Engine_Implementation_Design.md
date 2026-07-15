# Workflow Engine 实施设计

- 状态：Implemented and accepted by Phase 5 macOS local gates
- 日期：2026-07-15
- 范围：本地 DAG 定义、不可变版本、Provider Task 编排、恢复与血缘
- 关联：ADR-003、ADR-006、ADR-009、ADR-011

## 1. 产品边界

Phase 5 把 AstraWeft 从单次 Provider 调用升级为可复现的多节点运行系统，但不把
工作流定义成编程语言。v1 只支持有限、静态的 DAG：

- `PROVIDER_MODEL`：委托现有 Task Runtime；
- `TRANSFORM`：Core 内置、确定性且不执行用户代码的投影/文本模板转换；
- `COMFYUI`、`CONDITION`、`APPROVAL`：保留稳定类型和导入兼容，但在对应执行器
  上线前不能发布为可运行版本。

循环、动态节点展开、任意 Python/JavaScript、shell、远程表达式和分布式 Worker
不进入 v1。

## 2. 定义与版本

```text
Workflow (稳定身份)
  └─ WorkflowVersion (DRAFT / PUBLISHED / ARCHIVED)
       ├─ WorkflowNode
       └─ WorkflowEdge
```

- 每个 Workflow 最多一个 Draft；保存草稿以“完整定义替换”实现，避免残留孤儿边。
- 发布把 Draft 原子转换为不可变 PUBLISHED，并把上一当前版本转为 ARCHIVED。
- 编辑已发布版本时创建下一版本号的 Draft；节点和边使用新本地 ID，但 `node_key`
  保持稳定。
- Published/Archived 的节点、边、Schema、配置和 checksum 均禁止原地修改。
- checksum 基于规范化 JSON，只包含输入/输出 Schema、按 `node_key` 排序的节点、
  按端口排序的边和输出绑定；不包含数据库 ID、时间戳或 UI 临时状态。

## 3. 节点与端口

每个节点保存发布时的输入/输出 JSON Schema 快照。Provider 节点同时保存 Provider、
Model、operation 引用；运行时 Task 仍保存自己的 Provider/Model 配置快照，因此历史
运行不会依赖后来同步到的新 Schema。

输入来源只有三种：

1. 工作流输入：`{"kind":"workflow_input","name":"prompt"}`；
2. 常量：`{"kind":"constant","value":...}`；
3. 上游输出：由 `WorkflowEdge(source_node, source_port, target_node, target_port)` 表达。

不解析字符串表达式。所有路径都落在显式端口；JSON Schema Draft 2020-12 同时用于
发布和运行输入校验。Provider 节点输出额外获得系统端口 `artifacts`，值为已持久化
Artifact ID 数组，用于血缘和后续支持文件型执行器。

## 4. 发布校验

发布被以下错误阻止：

- 节点 key 重复、边引用缺失节点、自环或有环；
- 源/目标端口不存在，或一个目标端口被多条边占用；
- 必填输入既没有上游边，也没有工作流输入/常量绑定；
- Schema 基础类型不兼容；`integer` 可流向 `number`，反向不允许；
- 工作流必填输出没有绑定到节点输出；
- Provider/Model/operation 不存在、不可用或已禁用；
- 配置树出现 `api_key`、`password`、`authorization`、`client_secret`、
  `access_token`、`refresh_token`、`private_key` 等机密字段；
- 节点类型尚无受支持执行器。

Draft 可以保存为无效状态，问题面板必须完整展示；只有发布操作被阻止。

## 5. 运行与崩溃一致性

```text
WorkflowRun
  └─ NodeRun
       ├─ planned_task_id (先持久化的执行意图)
       ├─ task_id (Task 建立后关联)
       └─ ArtifactLink (INPUT / OUTPUT)
```

开始运行时固定引用一个 PUBLISHED/ARCHIVED version，保存输入、definition checksum
和节点快照。调度器采用拓扑就绪规则：所有上游成功后节点才 READY；上游失败、跳过
或取消时，依赖节点 SKIPPED；独立分支可继续收尾。

Provider 节点使用两阶段本地协议：

1. NodeRun 先从 READY 转为 RUNNING，并持久化唯一 `planned_task_id` 和解析后输入；
2. TaskService 用该 ID 执行幂等的“确保 Task 存在”；
3. NodeRun 再关联 `task_id`。

若进程在任一步退出，恢复器根据 planned ID 查找或创建同一个 Task，不生成第二个
Task 身份。远程是否可重提仍遵循 Task Runtime/Provider 的幂等策略。

节点只有在 Task 成功且所有 Artifact 已本地持久化后才 SUCCESS。NodeRun 保存解析后
输入和规范化输出快照；ArtifactLink 保存输入/输出端口血缘。工作流输出只从成功节点
的显式端口解析并再次通过输出 Schema 校验。

## 6. 状态机

WorkflowRun：

```text
CREATED -> RUNNING -> SUCCESS
                  \-> FAILED
                  \-> WAITING -> RUNNING
CREATED/RUNNING/WAITING -> CANCELED
```

NodeRun：

```text
PENDING -> READY -> RUNNING -> SUCCESS / FAILED
PENDING ---------------------> SKIPPED / CANCELED
RUNNING -> WAITING_APPROVAL -> RUNNING / SUCCESS / FAILED / CANCELED
```

`continue_on_error` 只允许不依赖失败节点的其他分支继续；依赖失败输出的节点仍跳过，
最终 WorkflowRun 仍明确标记 FAILED，不能把部分失败伪装成成功。

## 7. 受限 Transform

首版只提供两种纯函数：

- `project`：配置 `outputs` 将输出端口映射到输入端口；
- `text_template`：模板仅允许 `{input_port}` 占位符，值必须是标量；禁止属性访问、
  下标、格式说明、转换标志和函数调用。

转换不访问网络、文件、环境变量或时间，不读取密钥，因此同一输入必得同一输出。

## 8. 导入导出

导出格式 `astraweft.workflow/v1` 使用显式允许字段和 definition checksum。导入执行：

- 1 MiB 文档上限；
- 根对象、Schema、节点、边、绑定和枚举的严格结构校验；
- 拒绝未知字段、远程 `$ref`、机密键、路径/URL 执行语义；
- 本地 ID 全部重建；相同 checksum 默认去重；
- 导入结果始终为 Draft，经本机 Provider/Model 再验证后才能发布。

为支持跨安装导入，`workflow_nodes.provider_id/model_id` 在定义层是软引用：导入草稿可
暂时引用源安装的身份，GUI 负责引导用户重新绑定；发布校验必须解析为本机已启用的
Provider/Model。真正执行时创建的 `tasks.provider_id/model_id` 仍使用严格外键。因此
可移植草稿不会削弱运行态账本的一致性。

## 9. GUI

Phase 5 页面分三态：

- 列表：草稿/当前版本、节点数、最近运行、状态与主操作；
- 编辑：节点库、可缩放 DAG 画布、Schema 属性、连接、自动保存和问题面板；
- 观察：版本只读，节点呈现等待/运行/成功/失败/跳过，详情展示解析输入、Task、
  Artifact 与错误。

画布只修改 Draft command，不直接访问数据库；所有异步操作经 qasync，不阻塞 UI。

## 10. 阶段门禁

- DAG、Schema/端口、机密、状态机、checksum、受限 transform 单元测试；
- migration、唯一/外键/不可变守卫、导入导出、血缘集成测试；
- 两个 Provider 节点端到端运行、失败分支、取消和进程恢复测试；
- GUI 草稿、验证、发布、运行观察和空状态测试；
- 1000 节点 DAG 校验和 1000 个活跃 NodeRun 调度不阻塞主线程；
- 全套 Ruff、mypy、import-linter、pytest/coverage、dependency audit、wheel lifecycle。
