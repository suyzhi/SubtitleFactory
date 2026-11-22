# 字幕工厂 0.6.1 · 简洁版

> 这是 `simple-edition` 分支：在 `main` 模块化重构架构的基础上，仅保留**视频下载、本地模型转写、AI 整理、AI 翻译与 SRT 导出**核心功能，前后端与桌面端已同步精简，无多余残留接口或模块。完整功能版见 `main` 分支。

字幕工厂是一款面向搭载 macOS 14 Sonoma 或更高版本的 Apple Silicon Mac 的字幕工具：粘贴 YouTube 链接或选择本地视频，选好本地转写模型，一键完成转写、AI 整理与翻译，然后导出 SRT。

## 简洁版功能

- **视频下载**：直装版支持粘贴 YouTube 链接自动下载到本机（自动清理 `t=`、`start`、`time_continue` 等播放定位参数）；也可选择或拖入本地视频。
- **本地模型转写**：在 Whisper、Parakeet、Qwen3-ASR、Sherpa-ONNX 等本地模型中选择，并按模型选择 CPU / Apple GPU (MLX) / Core ML 运行设备；模型未下载时会先原子下载并校验。CPU 保持 beam=5 与逐词时间戳，Qwen MLX 默认 20 秒切段，不静默更换模型或回退设备。
- **AI 整理**：用用户配置的 OpenAI 兼容文本服务修正断句和标点，合并过碎的字幕碎片。
- **AI 翻译**：将字幕批量翻译为目标语言（40 条一批附带前后上下文）。
- **SRT 导出**：支持导出原文、译文或双语 SRT。

已彻底移除（含前后端、数据库表与依赖）：项目库/分组/回收站、视频播放器与封面提取、字幕编辑器与撤销历史、波形时间轴、VTT/ASS 与视频压制、离线/AI 质检、术语表与翻译记忆、批量与播放列表、监听文件夹、短视频剪辑、内容包、OCR、说话人识别、云端转写（Fun-Realtime-ASR）、本地自定义模型扫描导入、项目包、备份与诊断中心。

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
2. 选择本地转写模型、运行设备和视频语言；按需打开“AI 整理”“AI 翻译”并选择目标语言。
3. 点击“开始生成字幕”。进度会显示在项目页，转写完成后展示字幕列表。
4. 完成后可在项目页再次运行“AI 整理”或换一种语言“翻译”，点击“导出 SRT”选择原文、译文或双语字幕。
5. 左侧历史列表可重新打开或永久删除以前的项目。

首次使用 AI 整理或翻译前，请点击左下角“AI 服务”填写服务商、Base URL、API Key 与模型。API Key 以明文保存在 App 数据目录下的本机 SQLite 数据库中，不使用 macOS 钥匙串；复制或分享数据目录与迁移前备份文件时请留意。环境变量路径仅用于开发和高级排错，冻结发布版默认不继承。

## 转写模型

| 选择 | 行为 |
|---|---|
| 自动选择 | 默认使用 Whisper Small（若已有匹配的已下载专用模型则优先使用） |
| Whisper | Tiny、Base、Small、Medium、Large V3、Large V3 Turbo，按 CPU / Apple GPU (MLX) 下载固定提交 |
| Distil-Whisper Large V3 | 仅英语；其他语言会提示改用 Whisper Small |
| Qwen3-ASR | 0.6B / 1.7B，在 Apple Silicon 上使用 MLX Metal GPU 本地推理 |
| Sherpa-ONNX 专用模型 | 多语言与方言专用模型，由 App 管理下载到数据目录 |
| Parakeet ONNX | 由模型管理器下载并原子校验到 App 数据目录，支持 CPU / Core ML |
| Parakeet Core ML（直装版） | 从 Hugging Face 官方仓库 `FluidInference/parakeet-tdt-0.6b-v3-coreml`（固定提交 `7dd20fe`，约 461 MB）下载并逐文件校验 SHA-256；由 App 自带的开源 `parakeet-coreml`（FluidAudio）在 Apple Neural Engine 上运行 |

选择 Parakeet 不支持的源语言时，开始任务前会提示切换到 Whisper。

## 数据与隐私

macOS 直装版数据目录：

```text
~/Library/Application Support/com.subtitlefactory.desktop/data/
```

其中包含项目媒体、字幕、导出、模型、日志、本机设置和 SQLite 数据库。音频、字幕和项目数据保存在本机；只有明确启用的下载或 AI 文本整理/翻译会访问相应服务。删除项目会直接永久清理该项目对应的受管文件与数据库记录。

Mac App Store 版启用 App Sandbox，数据位于系统管理的 `~/Library/Containers/com.subtitlefactory.desktop/` 容器中。该发行版只接受用户主动选择的本地视频，不提供 YouTube、Cookie 或外部运行时路径。

详细说明：[隐私说明](docs/PRIVACY.md)

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

### 发布构建

首次构建先从 FFmpeg 官方源码准备可再分发的 LGPL arm64 运行时：

```bash
./scripts/fetch-ffmpeg.sh
```

随后执行完整打包：

```bash
./scripts/package-app.sh
```

发布脚本会在源码、Vite 产物与最终 App 可执行文件中校验 `subtitle-factory-ui:simple-v1` 与 `subtitle-factory-ui:single-page-v1` 标记，验证签名、架构与隔离运行时，并在退出时清理可重建缓存。

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 后端、FFmpeg、yt-dlp、磁盘和模型状态 |
| GET/POST | `/api/projects` | 项目列表 / 新建项目 |
| GET/DELETE | `/api/projects/{id}` | 项目详情 / 永久删除项目及受管文件 |
| POST | `/api/projects/{id}/import-local` | 导入本地视频 |
| POST | `/api/projects/{id}/workflow` | 下载 → 提取音频 → 转写 → 可选整理/翻译一键流程 |
| POST | `/api/projects/{id}/clean` | 单独运行 AI 整理 |
| POST | `/api/projects/{id}/translate` | 单独运行 AI 翻译 |
| GET | `/api/projects/{id}/segments` | 读取项目字幕 |
| POST | `/api/projects/{id}/export` | 导出 SRT（原文、译文或双语） |
| GET | `/api/projects/{id}/export/download` | 下载已导出的 SRT 文件 |
| GET | `/api/transcription/models` | 本地模型就绪与下载状态 |
| POST | `/api/transcription/models/{id}/prepare` | 下载、校验 App 管理模型 |
| GET/PUT | `/api/settings/ai/providers` / `…/{id}` | AI 服务商配置读取与更新 |
| PUT | `/api/settings/ai/assignments` | AI 整理 / 翻译服务商分配 |
| POST | `/api/settings/ai/providers/{id}/test` | AI 服务商连接测试 |
| GET | `/api/projects/{id}/tasks/latest` | 项目最新任务状态 |
| GET/POST | `/api/tasks/{id}` / `…/cancel` | 任务进度查询与取消 |

完整交互文档可在后端开发模式启动后打开 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。

## 发布说明

当前 v0.6.1 根目录 App/DMG 使用本机 ad-hoc 签名，适合本机安装与测试，不代表已经完成 Apple 公证。`字幕工厂-AppStore-QA.app` 只验证沙盒产品路径，同样不可上传。公开直装分发仍需 Developer ID、公证、stapling 与 Gatekeeper 验证；Mac App Store 提交还需要正式 App ID、Mac App Distribution / Installer Distribution 证书、provisioning profile、完整 Xcode 和 App Store Connect 元数据。
