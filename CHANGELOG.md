# Changelog

## 5.0.1 — 2026-09-07

- 下拉框按钮与菜单合并为一个浮层，共用边框、按压动画与主题颜色；点击说明或空白处不会打开菜单。
- 统一模型筛选高度和模型说明、运行设备控件对齐；AI 供应商卡片默认折叠，任务分配增加底部留白。
- 项目库顶部标题对齐并增大品牌字号。
- 删除自动钥匙串访问与 Chrome 登录凭据读取。AI 密钥保存在本机数据库；仅存在钥匙串的旧密钥需重新输入。需要登录权限的视频可在获取媒体后本地导入。

## 0.5.0 UI 更新 — 2026-09-07

- 优化独立项目库的紧凑列表、封面与操作按钮居中、筛选栏对齐和切换控件留白。
- 编辑工作区支持自动、左右、上下布局与可记忆比例，改善字幕阅读、时间码居中、样式预览和导出操作。
- 恢复设置中心和侧栏圆角，为任务面板加入滑入动画，统一任务卡片边缘，并补充整理入口。
- 保留本地播放、CPU 模型与媒体准备缓存；改进候选比较、取消收尾及未完成草稿恢复。
- 同步桌面 App、前后端及发布元数据为 0.5.0。

## 0.4.1 UI 修复与美化 — 2026-08-14

- Styled previously-unstyled elements: player empty state, process-timeline empty state / step number / current badge, log level icons and detail blocks, stats labels, task-drawer failure cards, and playlist item errors.
- Fixed the project-card "•••" menu button drifting below the card: it now sits as a frosted chip in the top-right corner of the cover (hover-revealed in light theme, always visible in dark).
- Polished the player idle view into a centered glass placeholder, tinted error log entries, and made scrollbars theme-aware with a global keyboard focus ring.

## 0.4.1 工程改进 — 2026-08-14

- Split the monolithic `App.tsx` into the `SubtitleTable` component (with colocated CSS), `DeferredPanel`, and shared display utilities, keeping the release entry chain and UI markers intact.
- Introduced a gradual backend quality gate: ruff (E4/E7/E9/F/I/B) and core-layer mypy with zero errors, log rotation for `app.log`, and disabled interactive API docs in frozen releases.
- Added an offline deterministic end-to-end API test covering create → transcribe → edit/lock → SRT export with mocked inference.
- Added the arm64 release-packaging workflow (tag or manual trigger) alongside the quality-gate ruff/mypy steps.

## 0.4.1 — 2026-08-12

- Replaced the legacy three-column launch surface with a dedicated project library and a separate professional project workspace inspired by Final Cut Pro.
- Added a policy-enforced Mac App Store channel with App Sandbox, protected local imports, no third-party media acquisition, and real packaged-App QA.
- Added native macOS save panels for large exports and constrained every delivery to App-managed source files and atomic destination writes.
- Added restart-safe task settlement, durable draft recovery, verified database backups, exclusive restore maintenance, and reversible stale-draft rebasing.
- Bound the frozen Python sidecar and its media helpers to the desktop process so normal quits, external termination, and forced crashes do not leave orphan processes.
- Deferred heavy workspace modules and reduced idle polling while preserving visible task state, keyboard operation, and cloud-upload consent boundaries.
- Added model-free native runtime smoke tests, preserved PyInstaller's safe relative-library link topology, and deduplicated Sherpa's byte-identical ONNX Runtime alias, reducing the App Store QA App by about 144 MiB without removing inference capabilities.
- Added source-controlled Simplified Chinese App Store metadata and fail-fast validation for product copy, privacy declarations, legal ownership, support, review contacts, and account-holder confirmations.
- Set an honest macOS 14 minimum, pinned MLX to its official macOS 14 wheels, compiled Vision OCR for the same target, and made every release scan all bundled Mach-O deployment targets.
- Preserved the frozen runtime's verified relative links in the direct App, repacked the DMG with that exact signed App, and made release packaging compare every mounted file hash, permission, and link target before delivery.
- Removed Deno and FFprobe host dependencies from download unit tests while retaining real bundled-runtime checks in packaged-App acceptance.
- Added isolated end-to-end acceptance for both the direct App and the read-only mounted DMG copy, covering authenticated sidecar APIs, bundled download tools, local media import, exact cleanup, crash exit, and normal Quit.
- Upgraded CI to the official Node 24-based GitHub Actions releases, pinned every action to a full commit SHA, restricted the workflow token to read-only repository contents, and bounded the quality gate runtime.
- Restored keyboard focus to the persistent settings control after closing the dialog, removed YouTube-only controls and copy from local projects, and made subtitles an explicit prerequisite for publication-pack and short-video generation.

## 0.3.2 — 2026-07-26

- Added a verified nine-model catalog with use-case groups, immutable CPU/MLX sources, per-runtime readiness and resumable in-App downloads.
- Added SHA-256 validation, disk preflight, staging installs, safe repair and source-drift release checks.
- Added replay and frame-step player controls with real video frame-rate metadata and 30 FPS fallback.
- Fixed subtitle-control hover contrast and expanded safe subtitle positioning to the full 0–100% range.
- Locked release builds to the Apple professional workspace and added source, bundle and final-App UI marker checks.

## 0.3.1 — 2026-07-13

- Fixed the transcription runtime contract so CPU, Apple GPU, Core ML and external Memo devices render correctly.
- Require an explicit per-model runtime choice on first use instead of silently falling back to CPU.
- Replaced native select and datalist controls with a focus-safe, searchable App combobox.
- Added keyboard navigation, portaled popovers and per-model runtime choices in the workspace and settings center.

## 0.3.0 — 2026-07-12

- Dual-concurrency, retryable AI clean and translation batches with persisted partial results.
- Player-left/subtitles-right workspace, subtitle focus mode and inline target-language control.
- Eight independently configured AI provider cards with separate clean/translation assignments.
- In-place local model discovery for CTranslate2, MLX, Parakeet ONNX and Memo Core ML.
- MLX Whisper and selectable CPU/Core ML Parakeet runtimes on Apple Silicon.

## 0.2.0 — 2026-07-12

### Added

- Apple Silicon FFmpeg/FFprobe release runtime with architecture and dependency gates.
- App-owned Whisper and Parakeet model storage, model validation, repair and safe fallback.
- Project trash, restore, permanent deletion and empty-trash APIs.
- Persistent App settings, local path validation and expanded health diagnostics.
- Searchable, extensible source and target language selection.
- Apple-style workspace, compact workflow bar, contextual inspector and full settings center.
- Native macOS theme synchronization, motion tokens and reduced-motion behavior.

### Changed

- Automatic transcription now defaults to Whisper Small instead of depending on Memo.
- Memo Core ML is an optional detected external accelerator.
- YouTube URLs are canonicalized and yt-dlp always receives the resolved FFmpeg location.
- Failed downloads stay attached to the original project and can be retried.
- Runtime files, logs and models live under the App data directory in release builds.

### Fixed

- Highest-quality YouTube video/audio streams now merge in the packaged App.
- Light mode now updates the native macOS title bar and system controls.
- Invalid custom model and CLI paths fall back safely with a user-visible reason.
