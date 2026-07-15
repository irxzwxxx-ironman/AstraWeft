# AstraWeft 用户入门

本指南面向第一次安装 AstraWeft 的用户。AstraWeft 的配置、任务、工作流、日志和产物默认
保存在本机；API Key 由操作系统凭据存储管理，不写入 SQLite 数据库或普通设置文件。

## 1. 安装与首次启动

公开 Beta 应只使用项目发布页提供且通过平台签名验证的安装包。当前仓库中的
`dist/desktop/AstraWeft.app` 是本地开发候选包，仅用于本机验收，不应转发给其他用户。

首次启动会自动：

1. 创建平台原生的配置、数据、缓存和日志目录；
2. 初始化 SQLite 数据库并迁移到当前版本；
3. 发现已安装的 Provider 插件；
4. 启动本地任务恢复器和仅绑定 `127.0.0.1` 的 ComfyUI 网关；
5. 打开 Dashboard。AstraWeft 不会在首次启动时自动调用付费 Provider。

默认数据位置：

| 平台 | 数据目录 |
|---|---|
| macOS | `~/Library/Application Support/AstraWeft/` |
| Windows | `%LOCALAPPDATA%\AstraWeft\AstraWeft\` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/AstraWeft/` |

卸载应用不会自动删除数据目录。这是为了防止任务、工作流和产物被意外删除；确认不再需要数据后
再手工清理。

## 2. 用 Mock Provider 完成第一次本地任务

Mock Provider 不访问网络，也不会产生费用，适合验证安装是否正常。

1. 打开左侧 **Provider** 页面，选择“添加 Provider”。
2. 选择 Mock Provider，填写一个便于识别的名称。
3. 在凭据字段输入 `mock-valid-key`。它是公开测试值，不是真实密钥。
4. 保存并执行连接测试，然后同步模型目录。
5. 打开 **Playground**，选择刚才的 Provider 和可用模型。
6. 填写 Schema 自动生成的参数表单并提交任务。
7. 在 **任务中心** 查看持久状态，在 **请求日志** 查看脱敏调用记录，在 **产物库** 查看结果。

任务提交后即使关闭窗口，持久状态仍会保留。对于无法确认远端结果的付费调用，AstraWeft 会进入
需要人工确认的保守状态，不会因为重启而盲目重复提交。

## 3. 配置真实 Provider

- OpenAI 和 Runway 插件需要各自服务签发的 API Key。
- 只在 Provider 配置窗口输入密钥；不要把密钥写进工作流、提示词、日志或诊断备注。
- “连接测试”成功后再同步模型。模型能力、参数 Schema 和价格元数据均由插件提供。
- 成本页只汇总 Provider 明确返回的已知价格；未知成本不会显示为零。

如果系统凭据存储不可用，界面会显示凭据仅在本次会话有效。此时不要依赖后台恢复或 ComfyUI
网关，修复系统 Keychain/Credential Manager/Secret Service 后再配置真实密钥。

## 4. 工作流与 ComfyUI

- 工作流发布后版本不可原地修改；继续编辑会产生新草稿，历史运行仍指向原版本。
- 发布前会验证 DAG、端口类型、必填输入和 Provider/模型引用。
- ComfyUI 页面可登记本地实例、测试能力并导入 API workflow 模板。
- `extras/AstraWeftGateway` 可复制到 ComfyUI 的 `custom_nodes` 目录。重启 ComfyUI 后使用
  AstraWeft Provider Image/Video 节点。
- 网关 Token 由操作系统凭据存储管理；不要把 Token 写入 workflow JSON 或公开截图。

## 5. 备份与诊断

打开 **设置与本地数据**：

- “立即备份”使用 SQLite 在线备份生成一致性快照，并附带完整性和 SHA-256 记录；
- “从备份恢复”只暂存候选文件，重启时才替换，并先为当前数据库创建安全备份；
- “导出脱敏诊断包”不包含数据库、API Key、完整提示词或用户产物。

升级 Beta 前建议手工创建一份备份。数据库迁移检测到旧版本时也会自动创建
`pre-migration` 备份。

## 6. 键盘与语言

- `Cmd/Ctrl+K`：打开全局命令面板；
- `Cmd/Ctrl+,`：打开设置；
- `Esc`：关闭命令面板或临时界面；
- `Tab` / `Shift+Tab`：按明确顺序移动焦点。

语言、主题、减少动态效果和系统通知偏好会原子保存到本机设置文件。

遇到问题时参阅 [故障排查](./Troubleshooting.md)。
