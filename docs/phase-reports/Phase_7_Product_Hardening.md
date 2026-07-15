# Phase 7 产品完善与可运维性验收报告

- 项目：AstraWeft（星纬）
- 日期：2026-07-15
- 状态：LOCAL PASS
- 范围：macOS 本地完整门禁；Git 与远程 Windows/Linux CI 按用户要求延后

## 1. 阶段交付

### 1.1 安全可恢复的本地数据维护

- SQLite online backup 生成一致性快照和带 SHA-256/表计数的 manifest；迁移前自动备份。
- 恢复先校验和预览，只暂存不热替换；下次启动替换前再保留当前库。
- 数据目录迁移先做空间/冲突预检，再逐文件复制和哈希校验；中断时不发布目标。
- Artifact 支持影响预览、回收站、恢复、引用保护的永久删除与保留期清理。
- 请求日志保留、数据库 integrity/foreign-key 检查和不含数据库/产物/密钥的二次脱敏诊断包已落地。

### 1.2 查询、Dashboard、成本与产物

- 只读 Query Service 提供 Dashboard 聚合、Task/Request Log/Artifact 稳定游标分页与筛选，GUI 不持有 ORM Session。
- Dashboard 展示真实的当日调用、成功率、已知/未知成本、运行任务、Provider 健康、最近任务和产物。“当日”按本地时区换算 UTC 边界，包含 DST 23 小时日测试。
- 成本分析按 Provider/模型/币种和 7/30/90 天/全部时间分组；多币种不混加，未知成本始终与已知金额分开。
- 产物库支持类型/时间查询、元数据/来源血缘、缺失文件状态、安全打开/复制路径，以及按内容哈希惰性生成的原子图片缩略图缓存。
- migration `20260715_0006` 增加 keyset 索引，`20260715_0007` 增加 Artifact 筛选与 Task Provider/Model 查询索引。

### 1.3 插件、桌面交互与可访问性

- Provider 插件管理器显示 manifest/API/Core 兼容性、包哈希、诊断和受影响 Provider；启停需预览且可重新启用，重扫描可识别外部升级。
- `Cmd/Ctrl+K` 命令面板、`Cmd/Ctrl+,` 设置快捷键、明确 Tab 顺序和 Escape 关闭短暂界面已接入。
- Task 终态通过 post-commit EventBus 触发原生系统通知；设置可持久化开关并立即生效。
- 基础中文/英文本地化覆盖应用外壳、命令面板、Dashboard、成本、通知和偏好；数字/金额使用 Locale 格式。
- 高 DPI rounding policy 在 QApplication 创建前设置；减少动态效果遵循系统或用户偏好。
- 全窗口自动无障碍审计覆盖输入、选择、列表、表格、详情区和按钮；当前无缺失 accessible name 或不可键盘聚焦的用户控件。

## 2. 验收证据

主验证环境：macOS arm64，Python 3.12.13，Qt/PySide6 6.10.3。

| 门禁 | 结果 | 证据摘要 |
|---|---|---|
| Ruff lint / format | PASS | 226 个 Python 文件格式一致，0 lint |
| mypy strict | PASS | 219 个源码与测试文件，0 issues |
| import-linter | PASS | 198 个模块、960 条依赖；7 个契约保持，0 broken |
| pytest | PASS | 381 passed；1 个 100k/1m 规模门禁默认跳过 |
| branch coverage | PASS | 90.30%，高于 90.00% 门槛 |
| Phase 7 scale | PASS | 100,000 Task + 1,000,000 Request Log；独立门禁 2.27 s |
| 备份/恢复 | PASS | online snapshot、manifest、篡改恢复拒绝、启动替换与 safety backup 全绿 |
| 迁移中断 | PASS | 故障注入后不发布目标，当前数据不变，部分目录可识别 |
| GUI / usability | PASS | Provider → Playground → Task/Log/Artifact/Dashboard/Cost 真实本地数据旅程与高风险预览全绿 |
| accessibility | PASS | 完整产品外壳的命名/键盘自动审计 0 issues |
| localization | PASS | 中英文外壳、命令、Dashboard/成本和通知测试；偏好原子持久化 |

## 3. Phase 7 退出标准映射

| 退出标准 | 结论 |
|---|---|
| 关键用户旅程通过可用性走查 | PASS；真实 Mock 持久链路与全外壳 GUI/键盘/无障碍测试覆盖 |
| 10 万 Task / 100 万 Request Log 基准达标 | PASS；游标分页、Dashboard 聚合和索引门禁 2.27 s |
| 备份恢复和数据目录迁移经过中断测试 | PASS；篡改/损坏/未来版本/复制中断均保留当前数据 |
| 高风险操作有明确影响预览和恢复路径 | PASS；恢复、迁移、插件停用、Artifact 删除/永久清理均有预览与保守默认 |

## 4. 已知限制与 Phase 8 输入

- 未执行远程 Windows/Linux CI，不将 macOS 结果外推为跨平台通过。
- 当前 Codex 未签名 Python 在 macOS Keychain 创建 loopback token 时可返回 `-25293`；GUI/Core 可运行，Gateway 保守降级。签名应用包属 Phase 8。
- 英文基础本地化已建立边界；公开 Beta 前需在 Phase 8 完成全页面文案盘点和原生翻译资源打包。
- 插件升级当前由外部 Python 包管理后重扫描发现；安全的签名插件市场/自动更新属 Phase 8 发布体系。

Phase 7 在 macOS 本地范围内结束。下一阶段是 Phase 8：开源 Beta 与跨平台发布。
