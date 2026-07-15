# Local AI Workflow Manager 架构设计文档

## 1. 项目定位

Local AI Workflow Manager 是一个本地优先（Local First）的 AI
工作流管理工具。

目标：

-   管理火山、快手、OpenAI 等云端 AI API
-   为 ComfyUI 提供统一调用能力
-   管理 API Key、模型参数、任务状态、调用日志
-   支持开源部署和插件扩展

定位类似：

-   ComfyUI（工作流）
-   LM Studio（本地管理）
-   Open WebUI（AI入口）

而不是传统 SaaS 后台。

------------------------------------------------------------------------

# 2. 总体架构

    Desktop GUI
    (PySide6)
          |
          |
    Application Core
          |
    ------------------------------------------------
    |              |               |                |
    Provider     Task          Workflow          Log
    Manager      Manager       Manager           System
          |
    ------------------------------------------------
    |
    AI Provider Plugins
    |
    |-- VolcEngine
    |-- Kling
    |-- OpenAI
    |-- Future Providers
    |
    |
    ComfyUI Integration

------------------------------------------------------------------------

# 3. 技术选型

## GUI

PySide6

原因：

-   跨平台
-   适合桌面工具
-   开源生态成熟

## 核心服务

Python

负责：

-   API调用
-   配置管理
-   任务调度
-   插件加载

## 数据存储

SQLite

原因：

-   零依赖
-   单文件
-   方便开源部署

未来支持：

SQLite → PostgreSQL

------------------------------------------------------------------------

# 4. 核心模块设计

## 4.1 Provider Manager

负责管理 AI 服务商。

功能：

-   添加 Provider
-   保存 API Key
-   测试连接
-   启用/禁用

支持：

    VolcEngineProvider
    KlingProvider
    OpenAIProvider

新增 Provider 不修改核心代码。

------------------------------------------------------------------------

## 4.2 Model Manager

管理模型信息。

例如：

    Provider:
    火山


    Model:
    Seedream

    Type:
    Image


    Parameters:
    prompt
    size
    seed

模型参数采用 Schema 描述，不硬编码。

------------------------------------------------------------------------

## 4.3 Task Manager

处理异步任务。

状态：

    CREATED

    RUNNING

    SUCCESS

    FAILED

用于：

-   视频生成
-   长耗时任务
-   状态轮询
-   重试

------------------------------------------------------------------------

## 4.4 Workflow Manager

管理 AI 工作流。

包括：

-   ComfyUI workflow
-   参数模板
-   工作流版本

示例：

    角色生成

    ↓

    图片增强

    ↓

    视频生成

    ↓

    字幕合成

------------------------------------------------------------------------

## 4.5 Log System

记录：

-   请求参数
-   返回结果
-   调用时间
-   Provider
-   模型
-   错误信息
-   成本

用于：

-   调试
-   成本分析
-   问题排查

------------------------------------------------------------------------

# 5. GUI 页面设计

## Dashboard

展示：

-   今日调用次数
-   成功率
-   API成本
-   当前任务

## Provider管理

管理：

-   API Key
-   Endpoint
-   服务状态

## Model管理

管理：

-   模型列表
-   参数Schema

## Playground

类似 Postman：

选择模型 → 输入参数 → 测试调用

## Task中心

查看：

-   当前任务
-   历史任务
-   失败任务

## Logs

查看调用记录。

------------------------------------------------------------------------

# 6. ComfyUI 集成方案

ComfyUI作为视觉工作流引擎。

连接方式：

    ComfyUI

    ↓

    Local AI Workflow Manager

    ↓

    Cloud API Provider

支持：

-   HTTP调用
-   Custom Node

ComfyUI不负责：

-   API Key管理
-   成本统计
-   Provider切换

------------------------------------------------------------------------

# 7. 插件系统

Provider采用插件模式。

目录：

    providers/

    ├── base.py

    ├── volcengine/

    ├── kling/

    └── openai/

新增 Provider：

只需实现接口。

------------------------------------------------------------------------

# 8. 数据设计

核心表：

## providers

保存服务商。

字段：

-   name
-   type
-   endpoint
-   credential

## models

保存模型。

字段：

-   provider
-   model_name
-   schema

## tasks

保存任务。

字段：

-   task_id
-   provider_task_id
-   status
-   result

## request_logs

保存请求。

字段：

-   provider
-   model
-   request
-   response
-   latency

------------------------------------------------------------------------

# 9. 部署目标

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

用户无需安装数据库和中间件。

------------------------------------------------------------------------

# 10. 开发路线

## Phase 1

基础框架：

-   PySide6窗口
-   SQLite
-   配置系统
-   Provider接口

## Phase 2

管理能力：

-   Provider管理
-   Key管理
-   模型管理

## Phase 3

接入：

-   火山API
-   OpenAI API

## Phase 4

接入：

-   快手可灵
-   异步任务

## Phase 5

ComfyUI深度集成：

-   Custom Node
-   Workflow管理

------------------------------------------------------------------------

# 最终目标

打造一个：

> 本地 AI 工作流操作系统

连接：

-   ComfyUI
-   云端AI模型
-   本地模型
-   AI Agent

成为 AI 创作者的一站式工作流管理工具。
