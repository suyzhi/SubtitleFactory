# Changelog

## 0.6.1 — 2026-09-27

- 修复 YouTube 下载失败（“YouTube 拒绝了媒体流访问”，HTTP 403）。YouTube 现在要求默认客户端的媒体链接携带 PO Token；下载改为优先使用无需 PO Token 的内嵌播放器客户端，仍被拒绝时自动换用其他客户端重试。全程匿名，不读取浏览器 Cookie。0.6.0 打包时遗漏了这一修复。
- Windows 10/11 兼容（`windows` 分支）：把 Windows 支持从 0.5.x 基线移植到 0.6.1，并覆盖此后新增的模块。
  - 启动脚本改为 UTF-8 带 BOM 保存：此前为无 BOM 的 UTF-8，Windows PowerShell 5.1 会按本地代码页解码，中文与 emoji 直接导致**语法错误、脚本无法运行**（`The string is missing the terminator`）。
  - 抽出 `scripts/windows/common.ps1`：Python 解释器改为 `py -3.11` → `py -3` → `python` 逐级真实执行校验（避开 Microsoft Store 的 python 别名），依赖在 `requirements.txt` 更新后重新同步（此前 venv 一旦存在就永不重装），并补充 FFmpeg/Deno 安装提示。
  - 修复 `start.ps1` 就绪轮询：`Start-Sleep` 原先只在请求异常分支里执行，非 200 响应会让 60 次重试瞬间跑完并误报超时；前端退出改用 `taskkill /T` 结束子树（避免残留 esbuild）；端口探测在 `NetTCPIP` 不可用时回退 `netstat`。
  - `start-desktop.ps1`：补齐 `backend/.env` 创建、Tauri `resources` 占位目录（缺失会让 cargo/tauri 在编译期失败）、退出时的构建产物清理，并改用 `npx.cmd` 绕过 `npx.ps1` 的执行策略限制。
  - Tauri：`bundle.targets` 拆分为平台配置（新增 `tauri.windows.conf.json`，Windows 产出 NSIS 安装包），后端进程以 `CREATE_NO_WINDOW` 启动避免弹黑窗，“在资源管理器中显示”不再因 explorer 退出码 1 误报失败。
  - 后端：字幕字体回退改为确定性选择；`/api/health` 增加 `platform` 与 `runtime.ocr` 能力上报，Windows 上界面不再引导用户点击必然失败的 OCR 流程。
  - **修复冻结后端在 Windows 上启动即卡死**：父进程看门狗用线程阻塞在 stdin 管道读取，而主线程同时加载 C 扩展 DLL，二者在 Windows 上互锁——后端进程常驻但永不监听端口，桌面窗口一直停在启动画面。改为用 `PeekNamedPipe` 轮询（不阻塞、不进入 loader lock），实测启动时间从「永不就绪」恢复到 5 秒内绑定端口。
  - **补齐缺失依赖**：`tiktoken` 与 `scipy` 此前只出现在 macOS 的 `requirements-release.lock`，按 `requirements.txt` 安装会缺失，冻结后端的运行时自检直接失败；两者已加入跨平台依赖清单（与 release lock 同版本）。
  - **新增 Windows 原生发布链**：`scripts/fetch-ffmpeg-windows.ps1`（下载 FFmpeg 9 / Deno 并缓存）、`scripts/build-sidecar.ps1`（PyInstaller 冻结后端 + 装配 `backend-runtime`，构建后真跑 `--verify-runtime` 自检）、`scripts/package-app.ps1`（发布界面标记校验 → 前端构建 → sidecar → `tauri build --bundles nsis`）。
  - Tauri Windows 配置补齐：`mainBinaryName` 让可执行文件为 `SubtitleFactory.exe` 而非 `app.exe`，窗口设置原生标题（macOS 专有的 `titleBarStyle`/`hiddenTitle` 在 Windows 上会让标题栏与任务栏标题为空），`bundle.resources` 只打包 `backend-runtime`。
  - 产出并实机验证 `字幕工厂_0.6.1_x64-setup.exe`（根目录，附 SHA-256）：安装到 `%LOCALAPPDATA%\字幕工厂`，启动后 5 秒内后端就绪、界面完成鉴权并拉取 `/api/health` 与 `/api/tasks`。

## 0.6.0 — 2026-09-27

- AI 思考模式改为 AI 服务卡片上的独立开关，默认关闭。DeepSeek 当前模型名 `deepseek-flash` 此前未被识别，思考一直开启，导致整理与翻译输出被截断、反复拆批（134 条字幕约 10 秒变为 2 分 20 秒）；现在每次请求都明确发送开关，开启时自动增加输出额度。DeepSeek 默认模型改为 `deepseek-flash`，数据库迁移至 v13。
- 界面：视觉设计层与动效系统、主题切换揭示动画、统一浮层对话框；重构“任务与工具”面板、步骤条与任务中心；导出页改为格式卡片；新用户引导、空项目库导入区与搜索状态；设置中心字号层级重排并改为首次打开时加载。
- 编辑器：实用时间轴、紧凑字幕行与统一线条图标，补齐文档承诺的编辑器功能。
- 项目库：卡片/列表切换不再卡顿，批次默认折叠、状态中文化，首次载入显示骨架屏。
- Parakeet：App 内下载官方 Core ML 模型并自带开源转写组件。
- 样式按界面区域重组为 12 个文件，删除失效规则与被覆盖声明，86 个界面状态逐元素比对零差异。
- 后端批量化数据库写入，优化音频与术语处理路径。
- 打包：修复 Xcode 27 下过程宏被 strip 导致的构建失败及只读许可证文件导致的签名清理失败。
- 版本号回到 0.x 序列，接续 0.5.0。

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
