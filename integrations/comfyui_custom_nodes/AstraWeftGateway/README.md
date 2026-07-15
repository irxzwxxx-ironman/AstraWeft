# AstraWeft Gateway Custom Nodes

将整个 `AstraWeftGateway` 目录复制到 ComfyUI 的 `custom_nodes` 目录，并在 ComfyUI
使用的 Python 环境中安装 `keyring`，然后重启 ComfyUI。

使用前先启动 AstraWeft 桌面应用。节点只连接固定地址 `127.0.0.1:17493`，访问令牌从
操作系统密钥环读取，不会出现在 ComfyUI 工作流、节点输入或日志中。

- `AstraWeft Provider Image`：等待 Provider 图像任务完成并输出 ComfyUI `IMAGE`。
- `AstraWeft Provider Video`：等待 Provider 视频任务完成并输出本地视频路径。

节点中的 `provider_id`、`model_id` 和 `operation` 可通过 AstraWeft 本机网关的目录接口
获取；Phase 6 首版使用显式字符串，避免在 ComfyUI 前端注入密钥或远程脚本。
