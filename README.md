# 字幕工厂 0.6.1 · 简洁版

> 这是 `simple-edition` 分支：只保留**视频下载、模型转写、AI 整理、AI 翻译**四项功能，界面为单页设计。完整功能版见 `main` 分支。

字幕工厂是一款面向搭载 macOS 14 Sonoma 或更高版本的 Apple Silicon Mac 的字幕工具：粘贴 YouTube 链接或选择本地视频，选好转写模型，一键完成转写、AI 整理与翻译，然后导出 SRT。

## 简洁版功能

- **视频下载**：粘贴 YouTube 链接自动下载到本机；也可以选择或拖入本地视频。
- **模型转写**：在 Whisper、Parakeet、Qwen3-ASR 等本地模型中选择，并选择 CPU / Apple GPU / Core ML 运行设备；模型未下载时会先自动下载。
- **AI 整理**：用设置的 AI 服务修正断句和标点，合并过碎的字幕。
- **AI 翻译**：把字幕翻译为目标语言。
- **导出**：原文、译文或双语 SRT。

已移除：项目库/分组/回收站界面、字幕编辑器、波形时间轴、样式与视频压制、质检、术语表、批量与播放列表、监听文件夹、短视频剪辑、内容包、OCR、说话人识别、项目包、备份与诊断界面。

## 直接启动

开发模式一键启动：

```bash
./start.sh
```

打开 http://127.0.0.1:5173/ 使用；后端为 http://127.0.0.1:8000/。启动脚本遇到占用端口会退出，不会杀死既有进程。

v0.6.1 已提供 Apple Silicon App、DMG、App ZIP 与 SHA-256 校验文件，最低支持 macOS 14.0。下载见 [GitHub 发布页](https://github.com/suyzhi/SubtitleFactory/releases/tag/v0.6.1)。

v0.6.1 App 位于：

```text
字幕工厂.app
```

可在 Finder 中双击，或从仓库根目录运行：

```bash
open "字幕工厂.app"
```

DMG 与校验文件位于仓库根目录：

```text
字幕工厂_0.6.1_aarch64.dmg
字幕工厂_0.6.1_aarch64.dmg.sha256
```

## 使用流程

1. 在“生成字幕”页粘贴 YouTube 链接，或点击“选择本地视频…”（也可拖入视频）。
2. 选择转写模型、运行设备和视频语言；按需打开“AI 整理”“AI 翻译”并选择目标语言。
3. 点击“开始生成字幕”。进度会显示在项目页，字幕在转写过程中逐条出现。
4. 完成后可在项目页再次运行“AI 整理”或换一种语言“翻译”，点击“导出 SRT”选择原文、译文或双语字幕。
5. 左侧历史列表可重新打开或删除以前的视频。

首次使用 AI 整理或翻译前，请点击左下角“AI 服务”填写服务商、API Key 与模型。API Key 在发布版中保存到 macOS Keychain。

## 转写模型

| 选择 | 行为 |
|---|---|
| 自动选择 | 默认使用 Whisper Small，不依赖 Memo |
| Whisper | Tiny、Base、Small、Medium、Large V3、Large V3 Turbo，按 CPU/Apple GPU 下载固定提交 |
| Distil-Whisper Large V3 | 仅英语；其他语言会提示改用 Whisper Small |
| Parakeet ONNX | 由模型管理器下载并原子校验到 App 数据目录 |
| Parakeet Core ML | 从 Hugging Face 官方仓库 `FluidInference/parakeet-tdt-0.6b-v3-coreml`（固定提交 `7dd20fe`，约 461 MB）下载并逐文件校验 SHA-256；由 App 自带的开源 `parakeet-coreml`（FluidAudio）在 Apple Neural Engine 上运行。检测到 Memo 时可作为回退 |
| 自定义模型 | 通过原生文件选择器设置；失效时自动回退 Whisper Small |

选择 Parakeet 不支持的源语言时，开始任务前会提示切换到 Whisper。

## 数据与隐私

macOS 直装版数据目录：

```text
~/Library/Application Support/com.subtitlefactory.desktop/data/
```

其中包含项目媒体、字幕、导出、模型、日志、本机设置和 SQLite 数据库。音频、字幕和项目数据保存在本机；只有明确启用的下载或云端处理会访问相应服务。移入回收站不会删除这些文件；只有永久删除或清空回收站才会清理对应项目数据。

Mac App Store 版启用 App Sandbox，数据位于系统管理的 `~/Library/Containers/com.subtitlefactory.desktop/` 容器中。该发行版只接受用户主动选择的本地视频，不提供 YouTube、Cookie、持久监听文件夹或外部运行时路径。

详细文档：[隐私说明](docs/PRIVACY.md) · [云端增强授权](docs/CLOUD_AUTHORIZATION.md) · [备份与故障恢复](docs/RECOVERY.md) · [App Store 交接单](docs/APP_STORE_METADATA.md) · [App Store 交付基线](docs/APP_STORE_READINESS.md)

## 开发环境

要求：

- 搭载 macOS 14 Sonoma 或更高版本的 Apple Silicon Mac
- Python 3.10+
- Node.js 18+
- Rust 1.77.2+
- Xcode Command Line Tools

浏览器开发模式：

```bash
./start.sh
```

桌面开发模式也可分开运行：

```bash
./backend/run.sh

cd frontend
npx tauri dev
```

### 质量门

提交前请运行与 CI 一致的检查（CI 在 push / pull_request 上自动执行）：

```bash
# 后端：测试、lint 与核心层类型检查（配置见 backend/pyproject.toml）
cd backend
./.venv/bin/python -m pytest -q tests
./.venv/bin/ruff check app tests sidecar_main.py
./.venv/bin/mypy app/api app/models app/utils app/security.py app/main.py app/version.py

# 前端：类型、lint、测试与生产构建
cd ../frontend
npx tsc -b
npm run lint
npm test
npm run build
```

`mypy` 采用渐进式门禁：`app/api`、`app/models`、`app/utils`、`app/security.py` 与 `app/main.py` 严格要求零错误；`app/services` 层暂时静默跟进，补齐注解后再逐模块解除豁免。

### 发布流水线

`.github/workflows/release.yml` 在 `v*` 标签推送或手动触发时，于 Apple Silicon (arm64) macOS runner 上执行完整打包验收（含 FFmpeg 源码构建、sidecar 冻结、DMG 内外哈希对比与真实启动验收），并上传 DMG 与校验文件；runner 标签可通过 `workflow_dispatch` 按可用区调整。

## 发布构建

首次构建先从 FFmpeg 官方源码准备可再分发的 LGPL arm64 运行时：

```bash
./scripts/fetch-ffmpeg.sh
```

随后执行完整打包：

```bash
./scripts/package-app.sh
```

发布脚本会：

1. 验证 Hugging Face/GitHub 模型来源、固定提交、文件大小和哈希元数据。
2. 运行后端测试、前端测试和 lint。
3. 检查 FFmpeg/FFprobe 与 arm64 运行时，固定安装官方 macOS 14 MLX 轮子，构建后端 sidecar，并实际加载 MLX、Sherpa、ONNX Runtime、PyAV 等冻结原生依赖。
4. 只执行一次正式前端构建，再生成 arm64 `.app` 与 `.dmg`。
5. 恢复并验证冻结运行时的安全相对链接，对最终 App 重新签名，再用这同一份 App 替换 DMG 中 Tauri 的临时副本，避免直装版与 DMG 版发生内容漂移。
6. 扫描 App 内每个 Mach-O 的部署目标，拒绝高于产品所声明 macOS 14 最低版本的原生依赖，并检查正确新版 UI 标记、签名和包内运行时。
7. 只读挂载最终 DMG，递归比较内外 App 的文件哈希、权限和链接目标，并验证 Finder 布局、签名、架构、UI 标记与 DMG SHA-256。
8. 使用隔离临时项目库分别启动直装 App 和只读挂载 DMG 中的 App，检查真实 sidecar、会话鉴权、发行能力、模型目录、Deno/FFmpeg、本地视频导入以及强制/正常退出清理。
9. 退出时清理可重建的前端、Rust、sidecar 和测试缓存。

任一运行时检查失败都会阻止生成 Release。内置 FFmpeg 8.1.2 从 [FFmpeg 官方源码](https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz) 构建；许可证和构建信息会放入 App 的 `THIRD_PARTY_LICENSES/ffmpeg/`。

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 后端、FFmpeg、yt-dlp、磁盘和模型状态 |
| GET/POST | `/api/projects` | 历史列表 / 新建项目 |
| DELETE | `/api/projects/{id}?permanent=true` | 删除项目及其数据 |
| POST | `/api/projects/{id}/import-local` | 导入本地视频 |
| POST | `/api/projects/{id}/workflow` | 下载 → 转写 → 整理 → 翻译一键流程 |
| POST | `/api/projects/{id}/clean` | 单独运行 AI 整理 |
| POST | `/api/projects/{id}/translate` | 单独运行 AI 翻译 |
| GET | `/api/projects/{id}/segments` | 读取字幕 |
| POST | `/api/projects/{id}/export` | 导出 SRT / VTT / ASS（原文、译文或双语） |
| GET | `/api/transcription/models` | 模型就绪与下载状态 |
| POST | `/api/transcription/models/{id}/prepare` | 下载、校验 App 管理模型 |
| GET/PUT | `/api/settings/ai/providers…` | AI 服务商配置与测试 |
| GET/POST | `/api/tasks/{id}` / `…/cancel` | 任务进度与停止 |

完整交互文档可在后端启动后打开 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。

## 发布说明

当前 v0.6.1 根目录 App/DMG 使用本机 ad-hoc 签名，适合本机安装与测试，不代表已经完成 Apple 公证。`字幕工厂-AppStore-QA.app` 只验证沙盒产品路径，同样不可上传。公开直装分发仍需 Developer ID、公证、stapling 与 Gatekeeper 验证；Mac App Store 提交还需要正式 App ID、Mac App Distribution / Installer Distribution 证书、provisioning profile、完整 Xcode 和 App Store Connect 元数据。
