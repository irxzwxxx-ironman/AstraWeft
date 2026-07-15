# ADR-002：采用 PySide6 与 qasync

- 状态：Accepted
- 日期：2026-07-15

## 背景

产品要求跨平台桌面应用、现代深色视觉、大列表、文件与系统集成。Provider 调用、轮询和下载天然异步，任何阻塞 Qt 主线程的 SDK 都会造成冻结。

## 决策

- GUI 使用 PySide6。
- Qt 与 Python 异步任务使用 qasync 统一事件循环。
- HTTP 使用异步 transport；网络期间不持有数据库事务。
- CPU 重任务进入受控线程池或独立进程，不在 GUI 主线程执行。
- GUI 使用 View + ViewModel + Qt item model；不持有 ORM Session。
- Dark Cyber AI 视觉通过 Design Token 和复用组件实现，不依赖页面级散落 QSS。

## 后果

- 与 Python AI 生态和跨平台打包路径一致。
- 第三方同步 SDK 必须包装在受控执行边界或替换为异步 HTTP。
- 需要专门测试 Qt 生命周期、取消、关闭、高 DPI、键盘和无障碍。
- PySide6/QT 许可证、二进制体积和平台插件需要纳入发布审计。

## 守卫

Presentation 不直接调用同步网络 SDK。PR 必须证明 loading、empty、error、键盘与取消状态；GUI 冒烟测试在 macOS 和 Windows CI 中逐步启用。

## 替代方案

- Electron/Tauri：会改变 v2 技术路线与 Python 集成成本，拒绝。
- 原生平台 UI：三平台重复实现，拒绝。
- Qt 线程到处使用：生命周期和取消复杂，采用集中异步模型替代。
