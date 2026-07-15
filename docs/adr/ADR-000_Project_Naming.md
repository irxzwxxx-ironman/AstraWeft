# ADR-000：项目名称与技术标识

- 状态：Accepted
- 日期：2026-07-15
- 决策范围：产品显示名、仓库名、Python 分发名、包名和应用 ID

## 背景

项目初始工作目录采用 `LingWeave（灵织）`。在创建公开仓库、Python package、应用数据目录和签名标识之前，需要检查名称冲突；一旦开始发布，后续改名会影响数据目录、插件 entry point、文档链接、包签名和用户认知。

## 初步核验

公开检索发现 `LingWeave` 已被用于：

- 中建八局一公司的“瓴维（Lingweave）”AI 智能建造工具，公开发布时间为 2025-04-17。
- 一个持续展示中的智能多语言学习应用 UX 项目。
- 其他公司和商店名称。

因此 `LingWeave` 的搜索唯一性较差，也可能造成品牌混淆。该判断只是产品命名预检，不构成商标法律意见。

## 候选比较

| 候选 | 中文建议 | 含义 | 初步冲突 | 判断 |
|---|---|---|---|---|
| `LingWeave` | 灵织 | 智能 + 编织 | 已有 AI 建造工具、语言学习产品 | 不建议作为公开品牌 |
| `AstraWeft` | 星纬 | 星群 + 纬线，多个模型被编排成创作网络 | 通用 Web 搜索未发现同名软件；GitHub 精确仓库搜索为 0；PyPI 精确名称返回 404 | 首选候选 |
| `NodeWeft` | 节纬 | 节点 + 纬线，强调工作流 | 初步未发现同名软件 | 备选，技术感强但品牌温度较弱 |
| `ArcWeft` | 弧纬 | 创作链路 + 纬线 | 存在同名电商账号/展示 | 不优先 |
| `NoctiLoom` | 夜织 | 暗色创作工具 | 已有同名音乐专辑 | 不优先 |

## 决策

公开品牌采用：

- Display name：`AstraWeft`
- 中文名：`星纬`
- Repository：`astraweft`
- Python distribution：`astraweft`
- Python import package：`astraweft`
- Provider entry point group：`astraweft.providers`
- Workflow MIME：`application/vnd.astraweft.workflow+json`

应用 ID 需要在确定 GitHub 组织或正式域名后冻结，建议格式：

```text
io.github.<organization>.AstraWeft
```

项目目录已在 Phase 0 从 `LingWeave/` 迁移为 `AstraWeft/`。公开包、插件 entry point 和工作流 MIME 从首次发布起只使用新标识；旧名称仅保留在本 ADR 的决策背景与引用中。

## 已满足的决策门禁

正式名称已在以下操作前确认：

- 初始化公开 Git 仓库和远程地址。
- 创建 Python distribution 或发布包。
- 创建应用数据目录和数据库默认位置。
- 固定插件 entry point group、Workflow 格式和 loopback API 标识。
- 申请代码签名、域名、图标和商标。

## 风险与后续核验

- 精确 Web/GitHub/PyPI 未发现冲突不等于名称可注册。
- 正式公开前需进行目标发布地区的商标、域名、GitHub 组织、PyPI 和主流软件商店复核。
- 若用户希望保留中文“灵织”，可将其作为非正式内部代号，但不建议与现有 `LingWeave` 英文公开品牌绑定。

## 参考

- [中建八局一公司：瓴维（Lingweave）智能工具发布](https://www.cscec81.com/198.news.detail.phtml?news_id=8923)
- [LingWeave 多语言学习应用 UX 项目](https://solantvoit.com/portfolio/lingweave)
- [PyPI `astraweft` 查询（当前不存在时返回 404）](https://pypi.org/pypi/astraweft/json)
