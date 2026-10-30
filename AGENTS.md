# 字幕工厂发布约束

## 唯一发布界面（simple-edition 简洁版分支）

- 本分支是只保留“视频下载、模型转写、AI 整理、AI 翻译”四项功能的简洁版；发布版只允许使用 `frontend/src/main.tsx → frontend/src/App.tsx` 这条入口链。
- `App.tsx` 必须是单页简洁界面：左侧为“新建字幕”按钮、历史列表和“AI 服务”入口；右侧为“生成字幕”页（链接/本地视频 + 模型/设备/语言 + 整理/翻译开关 + 开始按钮）或项目页（进度、整理/翻译按钮、字幕列表、SRT 导出）。
- 不得把 `main` 分支的项目库/工作区界面、编辑器、样式、质检、术语、批量、剪辑、内容包、OCR、说话人等功能重新引入本分支。
- 发布界面必须同时包含 `subtitle-factory-ui:simple-v1` 和 `subtitle-factory-ui:single-page-v1` 两个标记。
- 禁止恢复或打包 v0.1 旧入口、旧 `AISettingsDialog` 界面或任何预构建的旧前端。

## 构建与清理

- 每次构建完成后必须清理以下可重建产物：`frontend/dist`、`frontend/src-tauri/target`、`frontend/src-tauri/backend-runtime`、`backend/build`、`backend/dist`，以及测试产生的 `.pytest_cache` 和 `__pycache__`。
- 清理只能针对上面明确列出的产物，不得删除项目数据、用户模型、媒体、`backend/.venv`、`frontend/node_modules`、`vendor/ffmpeg` 或根目录最终交付的 App、DMG 与校验文件。
- 发布脚本必须在源码、Vite 产物和最终 App 可执行文件中检查 simple-v1 与 single-page-v1 标记，并确认旧 `AISettingsDialog` 标记没有进入 bundle；任一检查失败都必须停止打包。
