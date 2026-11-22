# 字幕工厂发布约束

## 唯一发布界面（simple-edition 精简版分支）

- 本分支保留 `main` 分支的模块化“项目库首页 + 中央视频播放器 + 字幕编辑/时间轴 + 样式与视频压制导出 + 批量/监听/播放列表 + 全部 29 个转写模型 + 设置中心”，同时移除非核心扩展模块。
- 发布版只允许使用 `frontend/src/main.tsx → frontend/src/App.tsx` 这条入口链；启动后先显示“项目库”首页，进入项目后显示带返回项目库按钮、工作区导航（编辑 / 样式 / 导出）、中央视频播放器、字幕编辑表/波形时间轴、任务与流程抽屉以及 `SettingsCenter` 的项目工作区。
- 本分支禁止重新引入以下已移除模块：
  - 内容中心（短视频切片推荐/渲染、社媒内容发布包、`ContentCenter`、`content_provider_id`）
  - 智能工具（macOS Vision 硬字幕 OCR、说话人标签与分离、`SmartToolsPanel`）
  - 质检与术语表（离线/AI 质检、术语库、翻译记忆、`QualityPanel`、`GlossaryPanel`）
  - 项目包与维护面板（`.sfproject` 导入导出、前端数据库备份恢复与诊断包导出 API/界面）
- 发布界面必须同时包含 `subtitle-factory-ui:simple-v2` 和 `subtitle-factory-ui:library-workspace-simple-v2` 两个标记。
- 禁止恢复或打包 v0.1 旧入口、旧 `AISettingsDialog` 界面或任何预构建的旧前端。

## 构建与清理

- 每次构建完成后必须清理以下可重建产物：`frontend/dist`、`frontend/src-tauri/target`、`frontend/src-tauri/backend-runtime`、`backend/build`、`backend/dist`，以及测试产生的 `.pytest_cache` 和 `__pycache__`。
- 清理只能针对上面明确列出的产物，不得删除项目数据、用户模型、媒体、`backend/.venv`、`frontend/node_modules`、`vendor/ffmpeg` 或根目录最终交付的 App、DMG 与校验文件。
- 发布脚本必须在源码、Vite 产物和最终 App 可执行文件中检查 simple-v2 与 library-workspace-simple-v2 标记，并确认旧 `AISettingsDialog` 标记没有进入 bundle；任一检查失败都必须停止打包。
