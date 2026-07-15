# AstraWeft Beta 发布、迁移与回滚政策

## 1. 版本政策

AstraWeft 使用 SemVer。`0.x` 表示公开接口和数据格式仍可能变化，但每个 Beta 仍必须提供明确的
迁移路径；不能以“未到 1.0”为理由破坏用户数据或静默改变任务语义。

- Patch：兼容性修复，不主动改变 Provider API 或数据库语义；
- Minor：可增加功能、migration 和向后兼容的 SDK 能力；
- Provider API、Workflow 格式或不可逆数据变化必须在发行说明中单独标出；
- 发布的 WorkflowVersion 永不原地改写，导入或升级产生新版本。

## 2. 发布通道

`development → release candidate → beta → stable`。候选包只有在相同字节经过验证后才能提升，
不能在测试通过后重新构建一个“等价”包替代。每个候选包附带：

- 平台签名和时间戳；
- SHA-256 构建 manifest 和 provenance；
- CycloneDX SBOM；
- 第三方许可证清单；
- 漏洞与恶意软件扫描结果；
- CHANGELOG、支持平台和已知限制。

候选归档必须由 manifest v2 独立验证，并在解包后再次验证文件内容、执行位和符号链接目标。CI 上传
的是已验证归档本身；签名、扫描和人工验收必须针对同一归档中的字节，不能在门禁通过后重新构建。

## 3. 数据迁移

启动时发现旧 revision 后，AstraWeft 在运行 migration 前创建一致的 `pre-migration` 备份，记录
来源 revision、完整性、SHA-256 和表计数。迁移完成后再次执行 SQLite integrity 与外键检查。

- migration 必须可在真实上一 Beta 数据副本上演练；
- 不允许应用启动后在后台静默执行高风险结构迁移；
- migration 失败时不删除备份，不继续启动任务运行器；
- 新版本不得自动降级旧数据库；
- 涉及远端任务语义的迁移必须保留远端 ID、幂等键、尝试和不确定状态。

## 4. 用户升级步骤

1. 等待运行中的付费任务到达可确认状态；
2. 在设置页创建手工备份并确认完整性通过；
3. 退出 AstraWeft；
4. 验证候选包平台签名和发布方提供的校验值；
5. 安装并启动新版本；
6. 检查 Dashboard、Provider、任务、工作流和产物；
7. 完成一个 Mock Provider 任务后再继续付费调用。

## 5. 回滚步骤

应用二进制回滚和数据库回滚必须配套。旧二进制不得直接打开已由新 schema 修改的数据库。

1. 退出所有 AstraWeft 实例并暂停 ComfyUI Custom Node 调用；
2. 复制整个当前数据目录作为故障现场；
3. 重新安装前一个受支持的 Beta；
4. 通过恢复预览选择升级前的 `pre-migration` 备份；
5. 重启以原子应用恢复，系统会先保留当前数据库的 `pre-restore` 安全备份；
6. 验证 revision、完整性、远端任务 ID、工作流和产物血缘；
7. 在确认 Provider 控制台状态前，不重提状态不确定的付费任务。

若新版本创建了旧版本无法理解的外部副作用，恢复数据库不会撤销 Provider 端任务或费用；这类状态
必须人工对账。

## 6. 平台支持与退出门禁

- macOS 是主开发平台，公开包要求 Developer ID、hardened runtime、公证与 stapling；
- Windows 与 macOS 同步 CI，公开包要求 Authenticode 和干净 VM SmartScreen 验证；
- Linux 在 Beta 前纳入，发行格式、桌面集成、Secret Service 依赖和卸载行为必须明确；
- 三平台都必须完成安装、首次启动、Mock 任务、升级、诊断、卸载且不误删用户数据。

任一平台签名、恶意软件扫描、第三方许可证、已知高危漏洞或上一 Beta 迁移门禁失败时，不得发布该
平台产物。
