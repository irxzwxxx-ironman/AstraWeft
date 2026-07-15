# Local AI Workflow Manager

# 架构设计文档（开源级桌面 AI 工具版）

## 1. 项目定位

Local AI Workflow Manager 是一个 Local First 的桌面 AI 工作流管理工具。

目标：

-   管理火山、快手、OpenAI 等 AI Provider
-   为 ComfyUI 提供统一调用能力
-   管理 API Key、模型参数、任务状态、调用日志
-   管理 AI 工作流
-   支持未来开源和插件扩展

产品定位类似：

-   ComfyUI：AI 工作流编排
-   LM Studio：本地模型管理
-   Open WebUI：AI 统一入口

目标不是 SaaS 后台，而是一个本地 AI 创作基础工具。

------------------------------------------------------------------------

# 2. 核心设计原则

## Local First

默认：

-   单机运行
-   无需云服务器
-   无需复杂中间件
-   一键部署

第一版本：

-   SQLite
-   本地文件存储
-   内置任务管理

未来可扩展：

-   PostgreSQL
-   Redis
-   多 Worker

------------------------------------------------------------------------

# 3. 总体架构

Desktop GUI

↓

Application Core

↓

------------------------------------------------------------------------

Provider System

Task Manager

Workflow Manager

Log System

Configuration Manager

↓

------------------------------------------------------------------------

AI Providers

-   VolcEngine
-   Kling
-   OpenAI
-   Future Providers

↓

ComfyUI Integration

------------------------------------------------------------------------

# 4. 技术方案

## GUI

PySide6

要求：

-   桌面应用
-   自定义主题
-   组件化设计
-   跨平台

## UI设计方向

整体采用：

Dark Cyber AI 风格

参考：

-   ComfyUI
-   LM Studio
-   Cursor
-   Linear

设计要求：

-   黑色暗色背景
-   深灰卡片
-   科技蓝/紫色点缀
-   现代 AI 工具风格
-   避免传统 Windows 软件风格

界面：

    Sidebar

    Dashboard
    Providers
    Models
    Playground
    Tasks
    Logs
    Workflows
    Settings


    Main Content Area

------------------------------------------------------------------------

# 5. 核心模块

## Provider Manager

负责：

-   AI 服务商管理
-   API Key管理
-   Endpoint管理
-   Provider状态检测

支持插件：

    providers/

    volcengine/

    kling/

    openai/

新增 Provider 不修改核心代码。

------------------------------------------------------------------------

## Model Manager

管理：

-   模型名称
-   类型(image/video/audio)
-   参数Schema
-   Endpoint

模型参数采用动态Schema。

例如：

``` json
{
 "prompt":{
  "type":"string"
 },

 "size":{
  "type":"select"
 }
}
```

GUI根据Schema自动生成参数表单。

------------------------------------------------------------------------

## Task Manager

负责：

-   异步任务
-   状态管理
-   重试
-   任务历史

状态：

    CREATED

    RUNNING

    SUCCESS

    FAILED

------------------------------------------------------------------------

## Workflow Manager

负责：

-   ComfyUI Workflow管理
-   工作流版本管理
-   参数模板管理

例如：

    角色生成

    ↓

    图片增强

    ↓

    视频生成

    ↓

    后期处理

------------------------------------------------------------------------

## Log System

记录：

-   请求参数
-   返回结果
-   Provider
-   Model
-   时间
-   耗时
-   错误信息
-   成本

支持：

-   搜索
-   筛选
-   JSON查看

------------------------------------------------------------------------

# 6. GUI页面规划

## Dashboard

展示：

-   API调用数量
-   成功率
-   成本统计
-   Provider状态
-   ComfyUI状态

采用：

卡片 + 图表

------------------------------------------------------------------------

## Provider管理

卡片式展示：

例如：

    VolcEngine

    Connected

    Models:
    Seedream
    Seedance

    Today:
    128 requests

支持：

-   添加
-   编辑
-   删除
-   测试连接

------------------------------------------------------------------------

## Model管理

展示：

-   模型卡片
-   Provider
-   参数Schema

------------------------------------------------------------------------

## Playground

类似：

Postman + AI Playground

功能：

-   选择Provider
-   选择模型
-   动态参数输入
-   查看请求响应

------------------------------------------------------------------------

## Task中心

展示：

-   当前任务
-   历史任务
-   失败任务

------------------------------------------------------------------------

## Logs

开发者控制台：

-   请求记录
-   错误分析
-   调试信息

------------------------------------------------------------------------

# 7. ComfyUI集成

ComfyUI负责：

-   工作流编排
-   图片处理
-   本地模型

Local AI Workflow Manager负责：

-   云API管理
-   Key管理
-   Provider切换
-   日志统计

调用关系：

    ComfyUI

    ↓

    Local AI Workflow Manager

    ↓

    Cloud AI Provider

未来支持：

Custom Node：

    AI Gateway Image Node

    AI Gateway Video Node

------------------------------------------------------------------------

# 8. 插件系统

所有外部能力插件化。

Provider接口：

    BaseProvider

    generate_image()

    generate_video()

    get_task_status()

新增能力：

只需增加插件。

------------------------------------------------------------------------

# 9. 数据设计

核心数据：

## providers

服务商配置

## credentials

API Key信息

要求：

-   加密存储
-   脱敏显示

## models

模型定义

## tasks

任务状态

## request_logs

调用记录

## workflows

工作流模板

------------------------------------------------------------------------

# 10. 部署目标

开发：

    python main.py

发布：

Windows:

    .exe

Mac:

    .app

Linux:

    binary

目标：

普通用户无需安装额外数据库和中间件。

------------------------------------------------------------------------

# 11. 开发路线

## Phase 1

基础框架：

-   PySide6窗口
-   Dark Theme系统
-   SQLite
-   配置管理
-   Provider接口

## Phase 2

管理功能：

-   Provider管理
-   Key管理
-   Model管理
-   Playground

## Phase 3

AI接入：

-   火山Provider
-   OpenAI Provider
-   快手Provider

## Phase 4

高级能力：

-   Task管理
-   Workflow管理
-   ComfyUI Integration

## Phase 5

开源完善：

-   插件机制
-   文档
-   安装包
-   示例

------------------------------------------------------------------------

# Codex开发模式要求

阅读本架构文档后：

不要直接开发Demo。

请按照成熟产品研发流程：

1.  分析现有代码和架构
2.  输出实施计划
3.  检查设计缺陷
4.  分模块开发
5.  每阶段测试和代码审查

目标：

打造一个可长期维护、可开源发布的桌面 AI 工作流管理平台。
