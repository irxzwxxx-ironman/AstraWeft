# AstraWeft Gateway Custom Nodes

将整个 `AstraWeftGateway` 目录复制到 ComfyUI 的 `custom_nodes` 目录，并在 ComfyUI
使用的 Python 环境中安装 `keyring`，然后重启 ComfyUI。

使用前先启动 AstraWeft 桌面应用。节点只连接固定地址 `127.0.0.1:17493`，访问令牌会优先从
操作系统密钥环读取；未签名的本地开发版会改用仅当前用户可读、退出即删除的本机交接文件。
令牌不会出现在 ComfyUI 工作流、节点输入或日志中。`keyring` 建议安装，但本地开发版不强制。

- `AstraWeft Provider Image`：等待 Provider 图像任务完成并输出 ComfyUI `IMAGE`。
- `AstraWeft Provider Video`：等待 Provider 视频任务完成并输出本地视频路径。
- `AstraWeft Provider JSON`：调用任意已配置 operation，输出标准化 JSON，用于文本、音频或自定义接口。

节点会从 AstraWeft 本机网关自动读取可用的 `provider_id`、`model_id` 和 `operation`
下拉选项。在 AstraWeft 中新增或修改配置后，刷新 ComfyUI 页面即可更新。
