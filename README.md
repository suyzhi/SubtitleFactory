# 字幕工厂 1.0

字幕工厂是一款面向 Apple Silicon Mac 的本地字幕工作台：导入本地视频或粘贴 YouTube 链接后，自动完成下载、音频提取和转写，再按需进行 AI 整理、翻译与导出。

## v1.0 重点

- 安全的本地工作台：动态后端端口、会话令牌、项目级签名媒体 URL 与 macOS Keychain 密钥存储。
- 专业编辑器：波形时间轴、时间码编辑、拆分/合并、批量替换、持久化撤销/重做与可恢复草稿。
- 离线质检、项目/全局术语表、翻译记忆、说话人识别与 macOS Vision 硬字幕 OCR。
- 多音轨和范围转写、SRT/VTT/ASS 导入、批量项目、监听文件夹、样式模板及 `.sfproject` 项目包。
- 数据库顺序迁移、迁移前备份、每日/每周自动备份与脱敏诊断包。
- 发布版内置 arm64 FFmpeg/FFprobe，YouTube 高清画面与音频合并不依赖 Homebrew。
- “自动选择”安全默认 Whisper Small；Memo Core ML 仅作为检测到后才显示的可选外部加速器。
- Parakeet ONNX 下载到 App 自己的数据目录，不写入源码或 App 包。
- 新增项目回收站、恢复、永久删除与运行中删除确认。
- 新增完整设置中心、运行时诊断、可扩展语言选择器和模型路径校验。
- 主界面重构为项目库、播放器、紧凑流程栏、工作标签页与按需检查器。
- Web 主题通过 Tauri `setTheme()` 同步 macOS 原生标题栏，并支持减少动态效果。

## 直接启动

开发模式一键启动：

```bash
./start-desktop.sh
```

已构建的 v1.0 App 位于：

```text
frontend/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/字幕工厂.app
```

可在 Finder 中双击，或从仓库根目录运行：

```bash
open "frontend/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/字幕工厂.app"
```

DMG 位于：

```text
frontend/src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/
```

## 使用流程

1. 点击顶栏“导入”选择本地视频，或点击“链接”粘贴 YouTube URL。
   粘贴播放列表链接时会先显示条目预览，可选择批量转写、AI 整理和 AI 翻译；创建后在首页“播放列表批量任务”中折叠归组。
2. App 默认自动完成下载、音频提取和转写。
3. 点击紧凑流程栏中的步骤，只会打开对应检查器；AI 整理与翻译仍需明确确认。
4. 在“字幕 / 样式 / 导出 / 日志”标签页继续编辑并导出。
5. 项目右键菜单提供打开、重命名、移动分组和移入回收站。

YouTube 链接中的 `t=110s`、`start`、`time_continue` 等播放定位参数会被移除，始终下载完整视频。下载失败会保留原项目，可直接重新下载。

## 设置中心

- 通用：默认流程、自动保存与启动行为。
- 转写：默认模型、源语言、模型状态、准备/修复与本地路径。
- AI 服务：服务商、Base URL、模型、API Key 与连接测试。
- 翻译：默认目标语言、双语顺序和常用语言。
- 下载与存储：画质、容器、FFmpeg、yt-dlp、输出目录与磁盘空间。
- 外观与动画：主题、界面密度与动画开关。
- 快捷键与关于：版本、数据目录和诊断信息。

API Key 在发布版中保存到 macOS Keychain；数据库只保留配置状态。密钥、完整本机路径、媒体和字幕正文不会进入脱敏诊断包。环境变量路径仅用于开发和高级排错，冻结发布版默认不继承。

## 转写模型

| 选择 | 行为 |
|---|---|
| 自动选择 | 默认使用 Whisper Small，不依赖 Memo |
| Whisper | 按需下载到 App 数据目录中的 `models/whisper/` |
| Parakeet ONNX | 由模型管理器下载并原子校验到 App 数据目录 |
| Parakeet Core ML | 仅在检测到完整外部模型和兼容 CLI 时显示为可用 |
| 自定义模型 | 通过原生文件选择器设置；失效时自动回退 Whisper Small |

选择 Parakeet 不支持的源语言时，开始任务前会提示切换到 Whisper。

## 数据与隐私

macOS 发布版数据目录：

```text
~/Library/Application Support/com.subtitlefactory.desktop/data/
```

其中包含项目媒体、字幕、导出、模型、日志、本机设置和 SQLite 数据库。移入回收站不会删除这些文件；只有永久删除或清空回收站才会清理对应项目数据。

详细文档：[隐私说明](docs/PRIVACY.md) · [云端增强授权](docs/CLOUD_AUTHORIZATION.md) · [编辑器快捷键](docs/SHORTCUTS.md) · [备份与故障恢复](docs/RECOVERY.md)

## 开发环境

要求：

- Apple Silicon Mac
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

1. 检查 FFmpeg/FFprobe 存在、可执行、纯 arm64、未启用 `--enable-nonfree`，且没有 Homebrew 动态依赖。
2. 用 PyInstaller 构建 FastAPI、yt-dlp、faster-whisper 与 sherpa-onnx 后端。
3. 运行前端 lint、类型检查和生产构建。
4. 生成 arm64 `.app` 与 `.dmg`。
5. 验证 App 签名和包内运行时，并生成 DMG SHA-256 文件。

任一运行时检查失败都会阻止生成 Release。内置 FFmpeg 8.1.2 从 [FFmpeg 官方源码](https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz) 构建；许可证和构建信息会放入 App 的 `THIRD_PARTY_LICENSES/ffmpeg/`。

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 后端、FFmpeg、yt-dlp、磁盘、输出目录和模型状态 |
| GET | `/api/projects` | 正常项目列表 |
| GET | `/api/projects?deleted=true` | 回收站列表 |
| POST | `/api/projects/{id}/trash` | 移入回收站；活动任务需 `terminate=true` 确认 |
| POST | `/api/projects/{id}/restore` | 恢复项目 |
| DELETE | `/api/projects/{id}?permanent=true` | 永久删除项目及其数据 |
| DELETE | `/api/projects/trash?confirm=true` | 清空回收站 |
| GET/PUT | `/api/settings/app` | 读取或部分更新 App 运行设置 |
| POST | `/api/settings/app/validate-path` | 校验模型、CLI、FFmpeg 和目录路径 |
| GET | `/api/transcription/models` | 模型来源、就绪与下载状态 |
| POST | `/api/transcription/models/{id}/prepare` | 下载、校验或修复 App 管理模型 |
| POST | `/api/batches/playlist/preview` | 只读取 YouTube 播放列表元数据和条目顺序 |
| POST | `/api/batches/playlist` | 创建或增量同步播放列表批次并启动所选流水线 |
| GET | `/api/batches?kind=youtube_playlist` | 获取首页播放列表批次、子项目和逐阶段状态 |

现有转写、整理、翻译、字幕编辑和导出接口保持兼容，完整交互文档可在后端启动后打开 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。

## 快捷键

| 快捷键 | 操作 |
|---|---|
| `Space` | 播放 / 暂停 |
| `T` | 剧院模式 |
| `Esc` | 关闭弹窗、抽屉或退出剧院模式 |
| `Return` | 保存当前字幕编辑 |

## 发布说明

本地开发构建使用 ad-hoc 签名，不作为公开分发件。公开 v1.0 必须通过 `scripts/package-app.sh` 的 Developer ID、Hardened Runtime、notarization、stapling 和 Gatekeeper 验证；缺少签名凭据时脚本只生成内部测试产物，不能视为正式发布完成。
