# AstraWeft Provider 插件开发指南

Provider 插件是独立 Python distribution，只依赖公共
`astraweft-provider-sdk`，不得导入 Core、数据库、GUI 或其他插件。Core 通过 entry point 和
`plugin.toml` 发现插件，通过稳定 DTO/Protocol 调用能力，不按插件 ID 写特例。

完整规范见 [Provider 插件接口规范](../Local_AI_Workflow_Manager_Provider_Plugin_Interface_Spec.md)，
可运行参考见 `plugins/mock`。

## 1. 最小包结构

```text
my-provider/
├── pyproject.toml
├── LICENSE
├── NOTICE
├── README.md
├── src/astraweft_my_provider/
│   ├── __init__.py
│   ├── client.py
│   ├── plugin.py
│   ├── plugin.toml
│   └── schemas.py
└── tests/
    ├── test_client.py
    └── test_contract.py
```

`pyproject.toml` 必须声明 Provider entry point：

```toml
[project.entry-points."astraweft.providers"]
my-provider = "astraweft_my_provider.plugin:plugin"
```

entry point 名称、manifest ID 和插件对象 ID 必须一致。包内应包含 `plugin.toml`、许可证、Notice
和类型信息；构建后用 wheel 内容检查确认这些文件没有遗漏。

## 2. Manifest 与兼容性

Manifest 声明插件版本、API 版本、Core 兼容范围、入口、能力和安全边界。Core 在加载任何客户端
代码之前验证 manifest，并将不兼容、缺文件或元数据不一致显示为诊断记录，而不是让整个应用启动
失败。

- 使用 SemVer 管理插件自身版本；
- 只声明真实实现的 capability；
- 公共 DTO/错误枚举的破坏性变化必须提升 Provider API 主版本；
- 不要把 API Key、默认密钥、签名 URL 或内部服务地址写入 manifest。

## 3. Schema 驱动的配置和任务表单

Provider 配置、模型操作输入使用 JSON Schema Draft 2020-12 的安全子集，UI 提示使用受限
UI Schema。不要从插件传入可执行 UI 代码、任意 QSS、HTML 或脚本。

Schema 应：

- 明确 `type`、`required`、范围、枚举和默认值；
- 使用稳定字段名，兼容已保存工作流；
- 把秘密字段标记为 credential，由 Core 写入 `SecretStore`；
- 区分用户可修正输入错误和 Provider 协议错误；
- 为产物类型、模态、操作和可取消/可轮询能力提供机器可读描述。

面向中英文用户的字段文案使用 `x-astraweft-i18n`，仅承载 locale 对应的纯文本
`title` / `description`；字段名、枚举值和验证规则不得因语言改变。完整格式见接口规范
“Schema 文案本地化扩展”。

## 4. 客户端实现边界

插件通过 SDK 获得受控 HTTP 和 Secret 接口。禁止自行持久化密钥、直接写 SQLite、直接写 Artifact
目录或绕过 Core 的日志脱敏。

异步操作必须实现清晰的提交、轮询、取消和恢复语义：

- 提交使用 Core 提供的稳定 idempotency key；
- Provider 不支持幂等时必须明确声明，网络不确定状态不得自动重复提交；
- 返回远端 task ID 后立即交给 Core 持久化；
- 轮询尊重退避、超时和服务端提示；
- 临时下载 URL 只作为短生命周期传输信息，Core 负责校验、流式下载、哈希和原子发布；
- 错误映射到 SDK 稳定分类，并保留可脱敏的 Provider request ID。

## 5. 必需测试

每个 Provider 包至少覆盖：

1. manifest/entry point/插件对象一致性；
2. SDK contract suite；
3. 模型发现、Schema 合法性及声明语言的表单文案回退；
4. 鉴权失败、限流、超时、不可用、协议错误；
5. 同步完成或异步提交/轮询/取消/恢复；
6. usage、成本和产物映射；
7. 日志和异常中不泄露密钥、正文与签名 URL；
8. 安装后的 wheel 隔离发现测试。

Mock Provider 必须继续作为零网络、零费用的基准实现；真实 Provider 的测试默认使用 fake transport，
不能在普通 CI 中调用付费 API。

## 6. 本地验证

```bash
uv sync --locked --all-groups
uv run pytest plugins/my-provider/tests -m contract
uv run ruff check plugins/my-provider
uv run mypy plugins/my-provider/src plugins/my-provider/tests
uv build --package astraweft-my-provider
uv run twine check dist/*my_provider*.whl
uv run check-wheel-contents dist/*my_provider*.whl
```

最后在一个没有源码路径的干净虚拟环境安装 Core、SDK 和插件 wheel，确认 entry point 可发现、
`plugin.toml` 可定位、Mock/连接测试可运行。

## 7. 评审清单

- 是否只依赖 SDK，未穿透 Core/GUI/数据库边界？
- 是否会在网络等待期间持有数据库事务？
- 是否可能因恢复或重试造成重复计费？
- secret、Header、签名 URL、提示词和用户数据是否可能进入日志？
- 模型/操作/Schema 更新是否仍能解释历史 WorkflowVersion？
- 取消、失败和不确定状态是否诚实映射？
- wheel 是否包含 manifest、LICENSE、NOTICE 和 README？
