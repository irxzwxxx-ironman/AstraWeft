# ADR-009：Task 提交前持久化、稳定幂等与保守恢复

- 状态：Accepted
- 日期：2026-07-15
- 关联：ADR-001、ADR-003、ADR-005、ADR-008

## 背景

生成类 Provider 可能在远端已经接受请求、但本地尚未保存响应时发生进程退出、断网或数据库故障。若重启后直接再次提交，可能产生重复任务和重复计费；若一律不再处理，又会丢失可安全恢复的远程任务。同步结果、异步远程 ID、重试、取消和产物落盘必须遵守同一套持久化顺序。

## 决策

1. 每次外部动作开始前，先在短事务中持久化 Task 状态和 RUNNING Task Attempt；网络调用期间不持有数据库事务。
2. 每个 Task 创建一次稳定 idempotency key，提交重试始终复用，不按 Attempt 生成新 key。
3. Provider 返回远程任务 ID 后，在同一业务更新中保存远程身份、状态和安全请求日志；Task 使用 `row_version` 乐观锁阻止并发覆盖。
4. 重启恢复采用保守规则：
   - 已有远程任务 ID 的 RUNNING/POLLING Task 进入 RECOVERING，只轮询，不重新提交。
   - SUBMITTING 且 Provider 声明支持幂等时，关闭中断 Attempt 后可用同一 key 安全重提。
   - Provider 不支持幂等、缺少远程 ID 或本地已有未确认输出时进入 NEEDS_ATTENTION，由用户确认。
   - 中断的 RUNNING Attempt 以 `process_interrupted` 结束，不伪装为成功。
5. 取消意图先持久化；关闭时给短数据库操作有限收尾时间，超时后才取消后台协程，下次启动继续恢复。
6. Request Log 只保存字段名、类型、安全响应摘要和标准错误。未知成本使用数据库 `NULL`，界面显示“未知”，不得写成 0。
7. 产物先写同目录 `.partial` 文件，验证大小与 SHA-256 后原子替换为正式文件；数据库只引用已校验产物。

## 理由

- 持久化事实先于外部副作用，缩小“远端发生、本地未知”的窗口。
- 稳定幂等 key 让支持幂等的 Provider 可以安全吸收重复提交。
- 对不支持幂等或身份不完整的任务停止自动操作，优先避免重复计费。
- 状态机、Attempt 和 Request Log 分离后，用户可以解释失败发生在哪个阶段。

## 后果

- Provider descriptor 必须明确声明幂等语义；Core 不按具体 plugin ID 推断。
- 恢复可能要求人工处理少量不确定任务，这是避免自动重复计费的有意取舍。
- Mock Provider 必须把远程任务状态持久化到插件私有数据目录，才能真实验证跨进程恢复。
- URL 产物已由 Phase 4 的 Core 受控 HTTP transport 实现；主机权限、临时 URL 保护、大小限制与原子下载详见 ADR-010。

## 执行守卫

- Task 状态迁移只能通过 Domain 守卫。
- Task/Attempt 写入、乐观锁、恢复、取消、重试和产物原子性必须有单元或集成测试。
- Core 与 GUI 禁止出现 Mock plugin ID 分支。
- secret canary 必须在 SQLite、普通日志和运行数据中零命中。
- 1000 个等待任务和 10 万条 Request Log 的页面门禁必须保持在 3 秒以内。

## 替代方案

- 收到 Provider 响应后才创建 Task：拒绝，进程中断时无法判断远端是否已接受。
- 每次重试生成新幂等 key：拒绝，会绕过 Provider 的重复提交保护。
- 所有 SUBMITTING 任务重启后自动重提：拒绝，对无幂等 Provider 有重复计费风险。
- 所有非终态任务都要求人工恢复：拒绝，会浪费已有远程身份和安全轮询能力。
