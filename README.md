# 字幕工厂 🎬

YouTube / 本地视频一键转写 + AI 字幕整理 + 翻译桌面软件。
**支持实时增量转写，就像 MemoAI 一样边识别边显示字幕。**

## 下载 App

macOS 安装包请从仓库的 [Releases](https://github.com/suyzhi/YouTube-/releases) 下载 DMG。
首个版本为未公证构建，macOS 如果阻止首次启动，可在“系统设置 → 隐私与安全性”中确认打开。

## 功能

- 📥 以最高可用画质下载 YouTube 视频，或导入本地视频；项目库显示视频封面
- ⚡ 导入或下载后自动提取音频并生成字幕，失败后可从当前阶段恢复
- 🛡️ 新转写先写入隔离暂存区，空结果、失败或取消不会覆盖已有字幕
- 🧠 “自动选择”会优先使用可用的 Apple Silicon Core ML 模型，否则使用 Whisper Small
- 📚 支持拖放和多视频任务队列式处理，导入过程显示进度
- 🔊 使用 faster-whisper 或 Parakeet V3 Core ML 转写音频，生成带时间轴的字幕
- ⏱ **增量转写**：转写过程中字幕逐条出现，不用等全部完成
- 🤖 AI 忠实整理字幕：保留原意，修正明显错词、标点与句界，完整长句不会按字数强拆
- 🌐 AI 翻译字幕：支持中英互译
- ✏️ 手动编辑字幕
- 🎨 自定义字幕样式（字体、原文/译文独立颜色、位置、字号、背景、阴影）
- 📄 导出 SRT / VTT / ASS / 双语字幕
- 🎬 ffmpeg 压制硬字幕视频
- 🖥️ 桌面 GUI 应用 (Tauri) + 浏览器双模式
- ⏸ 转写、整理与翻译任务可暂停、继续或安全终止
- 🔌 App 内管理 AI 服务商、Base URL、模型与 API Key
- 🧭 可点击字幕时间轴与历史项目切换
- 🗂 YouTube 项目支持自定义分组、折叠与快速移动
- 📋 运行日志在面板内独立跟随，可暂停跟随且不会拖动右侧操作区

## 项目结构

```
字幕工厂/
├── start-desktop.sh         # 🆕 一键启动桌面应用
├── start.sh                 # 一键启动后端+浏览器前端
├── README.md
├── backend/                 # Python FastAPI 后端
│   ├── app/
│   │   ├── api/projects.py  # 项目 CRUD + 处理流程 API
│   │   ├── api/tasks.py     # 任务状态 API
│   │   ├── models/database.py # SQLite (is_draft, source_stage)
│   │   ├── services/
│   │   │   ├── downloader.py
│   │   │   ├── audio_extractor.py
│   │   │   ├── transcriber.py     # 🆕 增量转写
│   │   │   ├── parakeet_transcriber.py # Core ML 优先、ONNX 回退
│   │   │   ├── subtitle_cleaner.py
│   │   │   ├── subtitle_translator.py
│   │   │   ├── subtitle_exporter.py
│   │   │   ├── video_thumbnail.py # 本地视频封面
│   │   │   └── video_renderer.py
│   │   └── utils/
│   ├── data/
│   └── .env
├── frontend/                # Tauri v2 + React + TypeScript
│   ├── src/
│   │   ├── App.tsx + App.css
│   │   ├── api/backend.ts
│   │   ├── types/index.ts
│   │   └── components/
│   │       ├── SubtitlePlayer.tsx       # 🆕 字幕 overlay 播放器
│   │       ├── SubtitleStylePanel.tsx   # 🆕 字幕样式面板
│   │       ├── ProcessTimeline.tsx      # 🆕 流程时间线
│   │       ├── ProcessLogViewer.tsx     # 🆕 结构化日志
│   │       ├── ProcessStepCard.tsx      # 🆕 步骤详情
│   │       └── SubtitleStatsPanel.tsx   # 🆕 转写统计
│   └── src-tauri/           # Tauri 桌面壳 (Rust)
```

## 前置要求

| 工具 | 检查 | 安装 |
|------|------|------|
| Python 3.9+ | `python3 --version` | https://www.python.org/ |
| Node.js 18+ | `node --version` | https://nodejs.org/ |
| ffmpeg | `ffmpeg -version` | 仅压制硬字幕视频时需要；音频提取已使用内置 PyAV |
| yt-dlp | `yt-dlp --version` | `brew install yt-dlp` |
| Rust (桌面端) | `rustc --version` | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh` |

## 快速启动

### 方式一：一键启动桌面应用（推荐）

```bash
./start-desktop.sh
```

脚本自动：
1. 检查所有依赖
2. 创建/激活 Python 虚拟环境
3. 安装 Python 依赖
4. 安装前端依赖
5. 启动后端 FastAPI (http://127.0.0.1:8000)
6. 等待后端健康检查通过
7. 启动 Tauri 桌面窗口
8. 退出时自动关闭后端进程

### 方式二：浏览器开发模式

终端 1 — 后端：
```bash
cd backend
cp .env.example .env    # 编辑 .env 填入 LLM_API_KEY
source .venv/bin/activate
pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

终端 2 — 前端：
```bash
cd frontend
npm install
npm run dev
```

然后浏览器打开 http://localhost:5173

### 方式三：分开启动桌面端

```bash
# 终端 1：后端
./backend/run.sh

# 终端 2：桌面端
cd frontend
npx tauri dev
```

## 配置

推荐直接在 App 右上角打开“AI 接入”设置。支持 DeepSeek、OpenAI、OpenRouter、SiliconFlow、Moonshot、通义千问、Gemini OpenAI 兼容接口和自定义服务；API Key 只保存在本机数据库且不会回传到前端。

开发环境也可编辑 `backend/.env` 作为首次初始化默认值：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| LLM_API_KEY | AI API 密钥 | — |
| LLM_BASE_URL | API 地址 | https://api.deepseek.com/v1 |
| LLM_MODEL | 模型名称 | deepseek-chat |
| WHISPER_MODEL | 转写模型 | small |
| PARAKEET_COREML_MODEL_DIR | Parakeet Core ML 模型目录覆盖值 | 自动发现 `~/Library/Application Support/Memo/models/parakeet-tdt-0.6b-v3-coreml` |
| PARAKEET_COREML_CLI | Parakeet Core ML CLI 路径覆盖值 | 自动发现 `~/Library/Application Support/Memo/plugins/parakeet-cli/parakeet` |
| YT_DLP_PATH | yt-dlp 路径 | yt-dlp |
| FFMPEG_PATH | ffmpeg 路径 | ffmpeg |

## 增量转写（MemoAI 式体验）

转写过程采用**两阶段设计**：

### 阶段 1：实时转写
- faster-whisper 每识别出一个 segment，立即写入 SQLite
- Parakeet V3 在 macOS 上优先复用 Memo 的 Core ML 模型，实时显示 CLI 进度，并把 token 时间戳转换为同一套字幕 segment；不会重复下载 ONNX 模型
- 前端每 1.5 秒轮询新字幕，字幕表格逐条出现
- 进度面板显示当前音频时间、已生成字幕数、最新识别文本
- 视频播放到未转写区域时显示「⏳ 该时间点字幕尚未生成」
- 每次转写拥有独立运行记录；结果验证通过前不会删除项目当前字幕

### 阶段 2：字幕后处理
- 所有原始 segments 生成完毕后
- 执行合并短字幕、拆分长字幕、时长修正
- 用 final segments 替换 draft segments
- 前端全量刷新最终字幕

### 状态标记
| source_stage | 说明 |
|-------------|------|
| transcribing | 正在转写中（草稿） |
| postprocessed | 后处理完成（最终） |
| cleaned | AI 整理完成 |
| translated | AI 翻译完成 |
| final | 旧数据兼容 |

## 字幕播放器

视频区域支持：
- **5 种显示模式**：关 / 原文 / 译文 / 双语(原文在上) / 双语(译文在上)
- **实时样式调整**：字体、原文/译文独立颜色与字号、位置、背景、阴影、最大宽度、行距
- **剧场模式**：播放器占满应用窗口但不触发系统全屏，支持 `T` 切换与 `Esc` 退出
- 设置自动保存到浏览器 localStorage
- 编辑字幕后 overlay 立即同步

## API 文档

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 🆕 健康检查 |
| GET | `/api/projects` | 项目列表 |
| POST | `/api/projects` | 创建项目 |
| GET | `/api/projects/{id}` | 项目详情 |
| GET | `/api/projects/{id}/thumbnail` | 项目本地封面 |
| PATCH | `/api/projects/{id}/group` | 设置或清除 YouTube 项目分组 |
| POST | `/api/projects/{id}/download` | 下载 YouTube |
| POST | `/api/projects/{id}/import-local` | 导入本地视频 |
| POST | `/api/projects/{id}/extract-audio` | 提取音频 |
| POST | `/api/projects/{id}/transcribe` | 转写 |
| POST | `/api/projects/{id}/clean` | AI 整理 |
| POST | `/api/projects/{id}/translate` | AI 翻译 |
| POST | `/api/tasks/{task_id}/cancel` | 安全终止后台任务 |
| GET | `/api/projects/{id}/segments?after_idx=N` | 🆕 增量获取字幕 |
| PATCH | `/api/projects/{id}/segments/{idx}` | 编辑字幕 |
| POST | `/api/projects/{id}/export` | 导出 |
| GET | `/api/tasks/{id}` | 任务状态（含 details/logs） |

## 打包桌面应用

```bash
./scripts/package-app.sh
```

脚本会先使用 PyInstaller 生成内置 FastAPI、faster-whisper 与 sherpa-onnx 兼容后端 sidecar，再构建 Tauri。macOS 上若发现 Memo 的 Parakeet Core ML 模型和 CLI，会直接复用它们。生成的 `.dmg` 和 `.app` 位于 `frontend/src-tauri/target/release/bundle/`。

macOS 发布版数据保存在 `~/Library/Application Support/com.subtitlefactory.desktop/data/`，与只读 App 包分离。

**注意**：首次打包需要下载 Rust 依赖，保证网络畅通。

## 常见问题

| 问题 | 解决 |
|------|------|
| yt-dlp 下载失败 | `pip install -U yt-dlp` 或改用本地视频导入 |
| ffmpeg 找不到 | `brew install ffmpeg` |
| Parakeet 模型下载慢 | macOS 会优先复用 Memo 的 Core ML 模型；只有找不到该模型时才下载约 465 MiB 的 ONNX 兼容模型 |
| AI API 报错 | 检查 .env 中的 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL |
| 后端连接失败 | 运行 `curl http://127.0.0.1:8000/api/health` 检查 |
| 桌面窗口打不开 | 确认已安装 Rust: `rustc --version` |
| Tauri 编译慢 | 首次编译需 1-5 分钟，后续增量编译很快 |

## 桌面端已知限制

1. **首次编译需要 Rust**：`npx tauri dev` 或 `npx tauri build` 会自动编译 Rust 代码，首次需 1-5 分钟
2. **视频播放**：通过后端 HTTP 服务播放，不需要本地文件直接访问权限
3. **本地文件导入**：支持 MP4、MKV、MOV、WebM 和 AVI；浏览器开发模式仍通过本地 HTTP 流式上传
4. **首次转写模型准备**：Parakeet 优先自动发现 Memo Core ML；找不到时才下载并缓存 ONNX 兼容模型
5. **硬字幕压制**：当前仍调用系统 ffmpeg/libass；播放器实时叠字、转写和字幕导出不受影响
6. **macOS 签名**：v0.1.0 为未公证构建，正式分发前仍建议配置 Apple Developer ID 与 notarization

## 转写失败与恢复

- “模型没有生成有效字幕”会被判定为失败，不会再显示成功。
- 当前正式字幕始终保留到新结果完整生成并通过后处理。
- 临时网络或服务错误只自动重试一次，避免无限循环。
- 最终失败后可在右侧恢复卡片确认切换到本机已就绪的备用模型。
- 任务状态保存在 SQLite；应用异常退出后会显示为“已中断，可重试”。

## 发布构建

```bash
./scripts/package-app.sh
```

脚本会重新生成 Python sidecar、构建前端并运行 `tauri build`。macOS 产物位于
`frontend/src-tauri/target/release/bundle/`。发布 DMG 前应完成测试、实际启动冒烟验证，并生成 SHA-256 校验文件。
