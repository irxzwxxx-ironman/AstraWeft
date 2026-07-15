# Phase 8 本地发行工程验收报告

- 项目：AstraWeft（星纬）
- 日期：2026-07-16
- 状态：LOCAL RELEASE ENGINEERING PASS / PHASE OPEN
- 范围：macOS arm64 本地候选包、升级回滚、供应链证据；未外推到 Windows/Linux

## 1. 本地交付

- 共享 PyInstaller one-folder spec，显式收集 Provider entry point 元数据、插件 manifest、Alembic
  migration、SQLite 异步驱动、Keyring 后端和 Qt 运行资源。
- macOS `.app` 本地候选包；Windows/Linux 复用同一 spec 生成平台原生目录包。
- 构建脚本附带 LICENSE/NOTICE、可安装 ComfyUI Custom Node、逐文件 SHA-256 和不依赖 Git 的
  build provenance。
- 冷启动脚本验证打包入口、版本、空数据目录初始化、GUI 事件循环、干净退出和数据库 revision。
- 升级脚本验证 `20260715_0006 → 20260715_0007`、迁移前一致性备份、数据保留、回滚复制和
  再次前向恢复。
- 从 5 个 wheel 安装的干净运行环境生成 CycloneDX SBOM、第三方许可证清单和 OSV 漏洞审计。

## 2. 验收证据

主环境：macOS arm64，Python 3.12.13，PyInstaller 6.21.0，PySide6 6.10.3。

| 门禁 | 结果 | 证据 |
|---|---|---|
| macOS desktop build | PASS | `dist/desktop/AstraWeft.app`，磁盘占用约 119 MiB |
| payload provenance v2 | PASS | 493 个条目：366 文件 + 127 符号链接；payload SHA-256 `f99a3df67a04bc7cf1bcfeb31a436398dc9ca3a2a5f9f6cbaafa0c2c16dd382b`；manifest SHA-256 `528a1f23795565d04f14646214e734de1997e96107da2f3c08690518e4cb443a` |
| independent manifest verification | PASS | 内容、尺寸、执行位、链接目标、成员集合与聚合摘要全部复核 |
| native archive round trip | PASS | 51 MiB `.tar.gz`，SHA-256 `c206eae71e50c3bb14ccbc65f03f96085516b4f68f7e29b0851108f8c78baf0b`；解包后 manifest v2 与 macOS 结构签名仍通过 |
| packaged cold start | PASS | `AstraWeft 0.1.0.dev0`，空目录建库到 `20260715_0007`，Gateway ready 并干净退出 |
| unsigned Keychain fallback | PASS | macOS 拒绝未签名包后安全降级为进程内会话存储；无明文持久化，Gateway 仍可绑定 |
| upgrade / rollback | PASS | `0006 → 0007 → 0006 → 0007`；integrity `ok`、外键问题 0、探针数据保留 |
| wheel build | PASS | Core、SDK、Mock、OpenAI、Runway 共 5 个 wheel |
| wheel metadata/content | PASS | twine/check-wheel-contents 全绿；migration、manifest、LICENSE/NOTICE 均存在 |
| SBOM | PASS | CycloneDX 1.6；45 个组件 |
| third-party licenses | PASS | 41 个第三方 distribution；未知许可证元数据 0 |
| known vulnerability audit | PASS | OSV；已知漏洞 0 |
| Ruff / mypy / architecture | PASS | Ruff 全库无违规；221 个严格类型文件；7 个架构契约、0 broken |
| full regression / coverage | PASS | 403 passed、1 个规模门禁默认跳过；branch coverage 90.53% |
| whole-product visual audit | PASS | 中文 11 页 + 英文 11 页及 Workflow editor，均为 1440×900 真实 Qt 截图；固定中文文案扫描覆盖页面、对话框、共享控件与 Provider Schema |
| secondary-text contrast | PASS | `TEXT_DIM` 在 Canvas / Surface / Surface Alt 上分别为 5.22:1 / 4.79:1 / 4.52:1 |
| 100k / 1m data scale | PASS | opt-in 大规模查询门禁 3.88 s |
| documentation links | PASS | 仓库 Markdown 本地链接缺失 0 |

本地证据位于：

- `dist/desktop/release-manifest.json`
- `build/phase8/manifest-verification.json`
- `build/phase8/release-artifacts/`
- `build/phase8/package-smoke.json`
- `build/phase8/upgrade-smoke.json`
- `build/phase8/release-evidence/`
- `build/ui-audit-20260715/accepted/`
- `build/ui-audit-20260715/english-final/`
- `build/ui-audit-20260715/audit.md`

## 3. 构建问题与修复

首次真实冷启动发现两项源码测试无法发现的打包缺口：

1. `rfc3987_syntax` 的 Lark grammar 未被自动收集，导致 JSON Schema 导入失败；
2. SQLAlchemy 动态选择的 `aiosqlite` dialect 未被静态分析识别，导致数据库引擎初始化失败。

两项资源现已在 spec 中显式声明，回归冷启动通过。macOS 构建产生的重复 COLLECT 目录会在 `.app`
完成后删除，避免候选目录重复占用空间。

继续运行真实 `.app` 还发现：macOS Keyring 后端虽然报告可用，未签名可执行文件在实际写入时仍以
`SecAuthFailure` 拒绝访问。凭据适配器现会在首次操作失败后锁定到进程内会话存储，不写设置、
SQLite 或明文文件；Gateway 在窗口状态快照前启动，因此界面显示的是实际模式。结构化日志只记录
操作名和错误类型，不记录引用、字段名或值。打包 smoke 使用随机隔离端口，并强制验证
`loopback_gateway_ready` 与非敏感的 `secure_storage_persistent` 字段，防止此问题再次被“窗口能打开”
的弱冒烟掩盖。

原 manifest 只记录 `is_file()` 可见内容，macOS `.app` 中的目录链接及链接目标没有进入 provenance，
且生成器自身没有独立消费者复核。schema v2 现把普通文件与符号链接分型记录，拒绝路径逃逸、断链、
成员增删、执行位变化和内容篡改；独立 verifier 不复用生成器实现。候选归档生成后会解包到临时目录并
再次验证，避免“manifest 正确但归档损坏或丢失链接”的假阳性。

最终回归还发现两个接近阈值的时序/性能风险并完成修复：单实例进程测试改为明确保留主实例就绪
窗口，避免 800 ms 退出竞态；Workflow NodeRun 波次从逐条 SQL 更新改为带 row-version 校验的
单次 executemany 事务，1000 节点工作流在覆盖率插桩下连续通过，stale writer 仍使整批回滚。

全产品截图审计另外发现：设置页内容高度超过窗口时会压缩布局，导致操作说明与按钮重叠；
Fusion 样式的横向滚动条和普通复选框未进入暗色主题；无任务/工作流时破坏性或目标型动作仍呈现可用外观；
概览的主入口和 Provider Registry 健康行与已配置状态不一致。现已改为可滚动的设置画布、统一暗色控件、
明确禁用态和 Provider-aware 主动作，并把小号辅助文字在所有使用表面的对比度提升到至少 4.52:1。

英文复核又发现：部分业务页、对话框、共享状态和插件 Schema 标签绕过了统一翻译器，
Workflow inspector 中的英文问题文案会被截断。现已完成全页面 Translator 注入，对话框、队列、托盘和
共享控件同步语言，并以非执行型 `x-astraweft-i18n` 扩展承载 Provider 字段翻译。自动扫描与 12 张
英文截图均通过，语言选择器中的“中文（简体）”是唯一允许保留的汉字文案。

复合 `run_local_release_gate.py` 已从空 wheel/运行环境执行通过，最新完整重建包含一次网络代理重试，用时约 3 分 37 秒，并生成
`build/phase8/local-release-gate.json`。

Developer ID 构建输入、hardened runtime 校验、notarytool 提交、stapling、Gatekeeper 验证与最终
字节重建 manifest/归档的脚本已实现，离线脚本回归通过；本机无 Developer ID 证书，因此这项
仍只是“工程就绪”，不是签名/公证通过证据。

## 4. 文档与政策

- [用户入门](../user/Getting_Started.md)
- [故障排查](../user/Troubleshooting.md)
- [Provider 插件开发指南](../development/Provider_Development_Guide.md)
- [Beta 发布、迁移与回滚政策](../release/Beta_Release_and_Rollback_Policy.md)
- [桌面打包运行说明](../../packaging/README.md)

## 5. 仍开放的 Phase 8 门禁

本报告不宣告 Phase 8 完成，以下证据必须在对应原生/外部环境取得：

- macOS Developer ID 签名、hardened runtime、公证、stapling、签名包 Keychain 持久读写与干净机
  Gatekeeper 验证；
- Windows 原生构建、Authenticode、SmartScreen、安装/升级/卸载和凭据管理器验证；
- Linux 原生构建、发行格式、桌面集成、Secret Service 和安装/升级/卸载验证；
- 新增的三平台 release-candidate workflow 尚待 GitHub 原生 runner 实际执行并保存首轮证据；
- 三平台恶意软件扫描及最终候选包字节的 provenance 复核；
- 从“上一个公开 Beta”而非合成旧 revision 执行真实数据迁移和回滚；
- 外部贡献者按文档在陌生环境完成构建、测试与 Provider 示例验证。

结论：Phase 8 的 macOS 本地发行工程基线通过，可以继续签名与跨平台候选验证；在上述门禁完成前，
项目状态保持 pre-alpha / Phase 8 open。
