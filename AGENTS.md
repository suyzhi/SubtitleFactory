# 字幕工厂发布约束

## 唯一发布界面

- 发布版只允许使用 `frontend/src/main.tsx → frontend/src/App.tsx` 这条入口链。
- `App.tsx` 必须使用 `codex/web-media-v1` 的 Apple 风格新版：启动后先显示独立“项目库”首页，进入项目后再显示带返回项目库按钮、工作区导航、中央播放器/编辑器、按需检查器和 `SettingsCenter` 的项目工作区。
- 禁止把 `main` 分支原有的“启动后直接显示项目侧栏 + 中央播放器 + 常驻右侧栏”的三栏界面当成新版发布；该界面就是 2026-07-22 归档中标记的“当前 UI 误交付”。
- 发布界面必须同时包含 `subtitle-factory-ui:professional-v2` 和 `subtitle-factory-ui:library-workspace-v2` 两个标记；仅有 professional 标记不足以证明使用了正确 UI。
- 禁止恢复或打包 v0.1 旧入口、旧 `AISettingsDialog` 界面或任何预构建的旧前端。旧 UI 文件可以保留作为历史参考，但不得进入模块依赖图或最终 bundle。

## 构建与清理

- 每次构建完成后必须清理以下可重建产物：`frontend/dist`、`frontend/src-tauri/target`、`frontend/src-tauri/backend-runtime`、`backend/build`、`backend/dist`，以及测试产生的 `.pytest_cache` 和 `__pycache__`。
- 清理只能针对上面明确列出的产物，不得删除项目数据、用户模型、媒体、`backend/.venv`、`frontend/node_modules`、`vendor/ffmpeg` 或根目录最终交付的 App、DMG 与校验文件。
- 发布脚本必须在源码、Vite 产物和最终 App 可执行文件中检查 professional-v2 与 library-workspace-v2 标记，并确认旧 `AISettingsDialog` 标记没有进入 bundle；任一检查失败都必须停止打包。
