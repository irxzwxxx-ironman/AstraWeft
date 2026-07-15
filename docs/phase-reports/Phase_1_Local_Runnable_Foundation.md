# Phase 1 应用基础设施与 Design System 验收报告

- 项目：AstraWeft（星纬）
- 日期：2026-07-15
- 状态：LOCAL PASS
- 范围：本地 Phase 1 完整门禁；Git 与远端跨平台 CI 按用户要求延后

## 1. 阶段交付

### 1.1 生命周期与公共内核

- PySide6 + qasync 桌面进程生命周期和受控退出。
- 每个数据根目录一个实例；第二进程通过本地 IPC 激活现有窗口，不并发打开 SQLite。
- UTC `Clock`、单调时间源和进程内单调 UUID v7 `IdGenerator`。
- 基于 `contextvars` 的异步安全 trace context；启动、关闭和结构化日志自动关联 trace ID。
- 进程内异步 Event Bus；事件可由 Unit of Work 在数据库提交后发布。

### 1.2 配置、数据库、凭据与诊断

- macOS/Windows/Linux 平台目录解析与隔离开发数据根目录。
- Pydantic 设置校验；优先级为默认值 < 文件 < 环境变量 < CLI。
- 设置文件原子替换、`fsync` 和私有文件权限。
- SQLite + SQLAlchemy Async + Alembic migration；重复启动幂等。
- 外键、WAL、NORMAL synchronous 和 busy timeout 安全参数。
- SQLite Unit of Work：显式提交、异常回滚、提交后事件与明确的 post-commit 错误语义。
- OS Keyring SecretStore 与无可用后端时的会话内降级。
- 递归秘密脱敏和滚动 JSONL 文件日志。

### 1.3 Design System 与 App Shell

- 1440×900 现代暗色 App Shell、Sidebar、Topbar、内容区和状态栏。
- Dashboard 只展示真实本地健康与零数据状态，不伪造 Provider、任务、成功率或成本。
- Design Tokens 与 Button、IconButton、Input、Select、Card、Table、Tabs、Badge、Toast、Dialog、Drawer、Skeleton、Empty State、Error State 组件。
- 任务速览为右侧覆盖抽屉，不压缩主内容；按钮和 Esc 均可关闭。
- `⌘/Ctrl+K` 搜索焦点、`⌘/Ctrl+,` 设置跳转、显式 Tab 顺序和可见焦点样式。
- Qt 高 DPI 比例策略、系统字体和 macOS/Windows 减少动态效果偏好读取。
- `dark/system` 主题选择已建立切换边界；浅色视觉精修按计划延后。

## 2. 验收证据

主验证环境：macOS arm64，Python 3.12.13，Qt/PySide6 6.10.3。

| 门禁 | 结果 | 证据摘要 |
|---|---|---|
| uv lock | PASS | 锁文件与项目环境同步 |
| Ruff lint / format | PASS | 67 个 Python 文件已格式化，0 lint |
| mypy strict | PASS | 66 个源码与测试文件，0 issues |
| import-linter | PASS | 3 个架构契约保持，0 broken |
| pytest (Python 3.12) | PASS | 60 passed |
| coverage | PASS | 92.46%，高于 90% 门槛 |
| pytest (Python 3.13) | PASS | 隔离环境 60 passed |
| SQLite migration | PASS | 空库创建、版本提交、重复执行幂等 |
| Unit of Work | PASS | commit/rollback/post-commit 顺序与故障语义通过 |
| 单实例 | PASS | 锁、激活 IPC、锁释放和并发进程测试通过 |
| 受控退出 | PASS | 两次连续冷启动与 shutdown complete 通过 |
| GUI / accessibility smoke | PASS | 组件、主题、导航、焦点、快捷键和降级状态通过 |
| sdist / wheel | PASS | metadata、内容和 Alembic 运行资源检查通过 |
| 隔离 wheel 启动 | PASS | 安装后真实建库、显示窗口生命周期并正常退出 |
| pip-audit | PASS | No known vulnerabilities |
| 视觉验收 | PASS | Dashboard 与右侧任务抽屉 1440×900 渲染已检查 |

## 3. 代码审查发现并修复

- Alembic PRAGMA 打开的隐式事务导致 revision 未提交：迁移前提交 PRAGMA 事务，重复启动不再重复建表。
- Alembic 会重置并禁用应用 logger：嵌入式迁移不再改写全局日志配置。
- 诊断字段名误触秘密脱敏：保留秘密保护，同时让非敏感 Keyring 持久性状态可诊断。
- 数字开头的 migration 文件名触发 wheel 内容警告：改为合法可导入文件名，revision ID 不变。
- 初版 Drawer 挤压 Dashboard：改为真正的右侧覆盖层，并以完整窗口渲染重新验收。
- Toast 重复显示可能叠加计时器：改为组件拥有的单次 QTimer。
- 关闭失败过早标记 context 已关闭：只在数据库成功释放后置为 closed，允许失败重试。
- 已提交 Unit of Work 仍可调用 rollback：新增生命周期守卫。

## 4. 退出标准映射

| Phase 1 退出标准 | 结论 |
|---|---|
| 冷启动显示高级暗色 App Shell | PASS；真实上下文截图与本机启动验证 |
| UI 主线程无网络/数据库长操作 | PASS；迁移在线程执行，数据库启动检查异步且发生在窗口显示前 |
| 异常诊断日志不含秘密 | PASS；递归脱敏、Bearer/字段测试和日志抽检 |
| SQLite 与配置目录遵循平台规范 | PASS；platformdirs 与隔离目录测试 |
| 打包 smoke 可启动并正常退出 | PASS；隔离 wheel 两次启动、迁移与 shutdown 日志验证 |

## 5. 延后但未虚构为通过的范围

- Git 提交、远端仓库和 GitHub Actions 实际执行按用户要求延后。
- Windows 与 Linux 的 CI 配置已存在，但尚无对应真实 OS 执行证据。
- macOS 公证、Windows 签名、PyInstaller 正式安装包、SBOM 和发布 provenance 属于后续发布阶段。
- 浅色主题只建立切换边界，视觉精修不属于 Phase 1 退出标准。

## 6. 阶段结论

Phase 1 在 macOS 本地范围内达到退出标准，可以进入 Phase 2 的 Mock-first Provider/Model 管理闭环。远端与跨操作系统状态继续保持“待验证”，不得从本报告推断为已通过。
