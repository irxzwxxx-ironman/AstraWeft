# ADR-003：采用 SQLite、SQLAlchemy 2 与 Alembic

- 状态：Accepted
- 日期：2026-07-15

## 背景

Local First 首发需要零外部中间件、单机可靠和可备份，同时希望未来迁移 PostgreSQL。仅使用手写 SQLite SQL 容易形成隐式类型、无迁移历史和不可审查的启动升级。

## 决策

- v1 业务数据库使用 SQLite 3。
- 数据访问使用 SQLAlchemy 2，迁移使用 Alembic。
- 启用 `foreign_keys=ON`、WAL、`busy_timeout` 和短事务。
- 网络请求期间不持有数据库事务。
- 所有发布后的 Schema 变更新增 migration，不修改旧 revision。
- 升级前使用 SQLite online backup API；迁移后执行外键和关键数据校验。
- Repository port 隔离 ORM，GUI 和 Domain 不接触 Session。

## 后果

- 单机安装简单，Schema 演进和 PostgreSQL 可迁移性可审查。
- SQLite 仍是单机写入模型，不被描述为多 Worker 数据库。
- JSON 字段需要 Application/Domain 层 Schema 校验。
- 大日志表需要分页、索引、保留和基准测试。

## 守卫

每次数据库改动必须包含升级测试、受影响查询索引审查和恢复说明。CI 从每个已发布 revision 演练到最新版本。

## 替代方案

- 原生 `sqlite3`：可以工作但迁移和未来适配成本更高，不采用。
- 首发 PostgreSQL：违背零依赖 Local First，不采用。
- ORM 自动建表替代 migration：无法安全升级用户数据，拒绝。
