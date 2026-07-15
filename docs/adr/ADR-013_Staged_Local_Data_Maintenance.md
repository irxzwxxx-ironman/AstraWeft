# ADR-013：分阶段本地数据维护与可恢复删除

- 状态：Accepted
- 日期：2026-07-15
- 关联：ADR-001、ADR-003、ADR-004、ADR-009、ADR-011

## 背景

AstraWeft 的 SQLite 数据库、Artifact、插件数据和结构化日志都默认保存在本机。
备份、恢复、数据目录迁移和永久删除都会直接影响用户的唯一本地真相。
仅复制处于 WAL 模式的 `.db` 文件可能得到不一致快照；在运行进程内直接替换
数据库可能与已打开的连接、WAL 侧车文件冲突。直接删除产物又会破坏 Task 和
Workflow 血缘。

## 决策

1. 备份必须使用 SQLite online backup API 创建一致性快照，完成后再执行
   `integrity_check`、`foreign_key_check`、Alembic revision、表计数和 SHA-256 校验。
2. 恢复分为“检查影响→复制到固定 pending 文件→写入原子 marker”，运行进程
   不替换数据库。下次启动取得单实例锁后、打开 SQLAlchemy engine 前，再校验
   marker/哈希/完整性，创建 `pre-restore` 安全备份并原子替换。
3. 迁移数据目录时，当前目录不动。在目标的隐藏 partial 目录内生成 SQLite 快照、
   复制配置/日志/产物/插件数据，逐文件校验并写入 `migration.complete.json`后
   才将目录原子发布。中断时不发布目标，保留 failure marker。
4. Artifact 删除默认为可恢复迁移：文件移入专用 trash root，数据库只设置
   `deleted_at`，Task 和 Workflow `ArtifactLink` 不删除。回收站恢复使用原子文件
   移动，原位置存在冲突时拒绝覆盖。
5. 永久删除只允许已在回收站且无 Workflow 端口引用的 Artifact，命令必须携带
   完整 SHA-256 作为确认对象。日志硬删除和 Artifact 清理使用独立保留周期。
6. 诊断包禁止包含数据库内容、Secret、请求正文和 Artifact。导出时对 settings 与
   最近日志重新执行递归脱敏，只输出版本、完整性、表计数和运行时摘要。

## 理由

- 将“制作可用副本”与“切换真实数据”分开，缩小不可恢复窗口。
- 恢复、迁移和删除都有影响预览、完整性证据和回退路径。
- Artifact 的物理文件与血缘元数据采用不同保留节奏，不会因节省磁盘而伪造历史。

## 后果

- 数据维护是独立 Port/Application Service/Infrastructure Adapter，GUI 不直接调用
  SQLite、文件复制或 ZIP API。
- 迁移完成后的目标目录可以独立启动，但旧目录不会自动删除；用户验证新目录后
  再显式清理。
- 应用启动会执行日志与回收站保留策略，但不会因一个仍被 Workflow 引用的产物
  超期而强制删除。

## 执行守卫

- 数据库替换只能发生在单实例锁已取得且任何 engine/session 未打开的启动阶段。
- 任何 hash、revision、integrity 或 foreign-key 检查失败都必须在原数据未变更时终止。
- 迁移不复制 cache/实例锁/Gateway 临时状态，不跟随符号链接。
- 脱敏诊断包必须用敏感样例值执行反向泄漏断言。
- 回收站文件与 `deleted_at` 更新中任一步失败时，应尽力恢复到命令前的物理位置。

## 迁移与回滚

本 ADR 不需要新表。`Artifact.deleted_at` 和 `ArtifactLink` 已由现有 schema 提供。
启动时 Alembic revision 不同会先生成 `pre-migration` 在线备份；迁移失败时删除或
忽略 partial 目录即可，源目录不需要回滚。恢复后如需回退，使用自动生成的
`pre-restore` 备份重复同一暂存流程。

## 替代方案

- 直接复制 `.db` 文件：拒绝，WAL 模式下可能不一致。
- 在运行进程内直接恢复：拒绝，已打开连接和侧车文件使替换不可证明安全。
- 迁移成功立即删除旧目录：拒绝，取消了实机启动验证和用户回退窗口。
- 产物直接硬删除并级联血缘：拒绝，会破坏可审计的 Workflow/Task 历史。
