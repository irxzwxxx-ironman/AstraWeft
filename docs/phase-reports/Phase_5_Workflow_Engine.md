# Phase 5 Workflow Engine 验收报告

- 项目：AstraWeft（星纬）
- 日期：2026-07-15
- 状态：LOCAL PASS
- 范围：macOS 本地完整门禁；Git 与远端 Windows/Linux CI 按用户要求延后

## 1. 阶段交付

### 1.1 不可变工作流定义

- `Workflow`、`WorkflowVersion`、`WorkflowNode`、`WorkflowEdge` 与显式 Port/Schema 已落地。
- Draft 可修改；Published/Archived 版本不可原地修改，后续编辑复制为新 Draft。
- 发布前阻止空工作流、环、重复输入、端口/Schema 不兼容、缺失输入、
  无效 Provider/Model、过期 Schema、未支持执行器和机密字段。
- 定义 checksum 覆盖 Schema、绑定、节点、边与配置，用于发布和运行快照校验。

### 1.2 安全可移植格式

- `astraweft.workflow/v1` 导入/导出使用显式允许字段和定义 checksum。
- 导入限制 1 MiB，拒绝坏 UTF-8/JSON、未知或缺失字段、错误类型、远程/文件
  `$ref`、机密键和 checksum 篡改。
- 导入始终生成本地 Draft，Provider/Model 定义保留可重绑的软引用；真正运行的
  Task 仍使用严格外键。
- Core Transform v1 仅支持确定性 `project` 与 `text_template`，不执行任意 Python、
  shell 或路径表达式。

### 1.3 持久 DAG 调度与恢复

- `WorkflowRun`/`NodeRun` 状态机、拓扑就绪计算、显式输入映射、分支失败传播
  和独立分支继续已落地。
- Provider 节点持久化 `planned_task_id` 后才创建 Task；崩溃发生在计划与 Task 创建
  之间时，重启使用同一 ID 继续，不会重复提交。
- 取消、Task 终态对账、失败/跳过、输入/输出 Schema 验证和运行终态均持久化。
- Artifact 输入/输出链接保存到 `artifact_links`，历史运行可还原版本、解析输入、
  节点状态、Task 和本地产物血缘。
- 后台协调器基于持久化活跃运行恢复，单个运行失败不会中止其他运行。

### 1.4 工作流 GUI

- 列表态展示草稿/当前版本、状态、节点数、最近修改和主操作。
- 编辑态提供深色可缩放画布、Provider/Transform 节点、拖动、端口连线、重命名、
  删除、输出绑定、900 ms 自动保存、问题面板和版本历史。
- 已发布版本只读，可创建下一 Draft；导入/导出和 Schema 驱动的运行输入表单
  已接入。
- 观察态在只读画布显示节点状态，详情包含解析输入、输出、Task ID、
  Artifact 链接和节点错误，并可取消活跃运行。

## 2. 验收证据

主验证环境：macOS arm64，Python 3.12.13，Qt/PySide6 6.10.3。

| 门禁 | 结果 | 证据摘要 |
|---|---|---|
| Ruff lint / format | PASS | 179 个 Python 文件格式一致，0 lint |
| mypy strict | PASS | 174 个源码与测试文件，0 issues |
| import-linter | PASS | 166 个文件、713 条依赖；7 个契约保持，0 broken |
| pytest | PASS | 314 passed |
| coverage | PASS | 90.39%，两位精度严格高于 90% 门槛 |
| DAG / recovery | PASS | 双 Provider 拓扑、失败分支、取消、计划后崩溃恢复不重复 Task |
| Artifact lineage | PASS | Task 成果物化，输入/输出链接和历史查询全绿 |
| GUI | PASS | 画布/对话框、编辑—发布—运行—观察—导入/导出—取消链路全绿 |
| 1000 节点规模 | PASS | 1000 Transform DAG 验证、启动、调度和成功终态；推进 < 5 s |
| dependency audit | PASS | 第三方依赖无已知漏洞；5 个本地包按预期跳过 PyPI 查询 |
| 5 个包构建 | PASS | Core、SDK、Mock、OpenAI、Runway 各 1 个 sdist + wheel |
| package metadata/content | PASS | 10 个产物 Twine 全绿，5 个 wheel contents 全绿 |
| isolated wheel smoke | PASS | 全新 venv 仅安装 wheel；3 个插件就绪，`0004`、offscreen GUI 启停全绿 |
| 现有数据升级 | PASS | `0003 → 0004`；integrity `ok`，0 外键违规，8 张工作流表齐全 |
| 本地运行 | PASS | 新版进程已启动，数据库在线，Keychain 持久化后端正常 |

## 3. Phase 5 退出标准映射

| 退出标准 | 结论 |
|---|---|
| 发布版本不可修改，编辑自动生成新草稿 | PASS；状态机、repository 更新守卫与 GUI 全链路验证 |
| 非法环、端口不兼容、缺失输入在发布前被阻止 | PASS；另覆盖 Schema、Provider/Model、执行器、机密与 checksum |
| 历史运行可还原版本、解析输入、节点状态和产物血缘 | PASS；定义快照、NodeRun、Task 与 ArtifactLink 持久化 |
| 应用重启后从可恢复节点继续，不重复完成节点 | PASS；持久执行意图 + Task 显式幂等 ID + 重启恢复测试 |

## 4. 安全与审查结论

- Workflow 定义和导出文件不包含 Secret；Provider 凭据仍只通过 SecretResolver/Keychain。
- 不执行用户 Python/shell；导入器拒绝远程引用、文件引用和未知结构。
- 定义层 Provider/Model 软引用仅用于导入后重绑；发布和执行必须解析本地启用资源。
- 每个 Provider Task 在外部副作用前先持久化计划 ID，恢复时不生成新 Task ID。
- 升级前保留 `build/local-data/data/astraweft-phase4-0003-backup.db`；升级后数据库完整性
  与外键检查全绿。

## 5. 明确延后范围

- Git 操作、远程仓库和 Windows/Linux CI 实际执行继续延后；不能从 macOS 本地
  结果推断其他平台已通过。
- ComfyUI 执行器、Condition/Approval 执行器、Loopback API 和 Custom Node Gateway 属于 Phase 6。
- 本阶段未执行真实付费 Provider 请求，不产生外部费用。

## 6. 阶段结论

Phase 5 在 macOS 本地范围内达到全部退出标准。AstraWeft 已从单次 Provider/Task
调用升级为具有不可变版本、持久 DAG 调度、崩溃恢复、Artifact 血缘和可视化
编辑/观察的本地工作流系统，可进入 Phase 6 ComfyUI 集成。
