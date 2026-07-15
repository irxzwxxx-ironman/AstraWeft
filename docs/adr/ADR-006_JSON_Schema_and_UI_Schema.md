# ADR-006：模型参数采用 JSON Schema 与独立 UI Schema

- 状态：Accepted
- 日期：2026-07-15

## 背景

Provider 模型参数变化频繁，GUI 需要动态表单。自定义 `type: select` DSL 会让验证、插件和 UI 各自解释同一结构，也缺少成熟工具和组合能力。

## 决策

- 数据结构和验证使用 JSON Schema Draft 2020-12。
- 字段顺序、分组、控件建议、帮助文本和高级折叠使用独立 UI Schema。
- Domain/Application 以 JSON Schema 为事实验证；UI Schema 不能放宽数据约束。
- Core 禁止不受控远程 `$ref`，Schema 导入设置大小、深度和组合复杂度限制。
- Provider 同步字段与用户覆盖字段分离，防止同步覆盖显示名、标签和默认参数。

## 后果

- Schema 可验证、可交换并适配未来 API/表单工具。
- `oneOf`、文件、媒体和 Provider 特殊控件需要定义稳定 UI 扩展。
- Schema 版本变化需要兼容性检查和工作流快照，不能只替换当前模型定义。

## 守卫

所有内置插件 Schema 通过 meta-schema、示例数据和 GUI 渲染 contract test。不得在页面按模型 ID 硬编码字段。

## 替代方案

- 自定义 DSL：短期简单但长期重复造验证器，拒绝。
- 只使用 Pydantic model：不适合作为跨插件、跨语言和持久化工作流格式，拒绝。
