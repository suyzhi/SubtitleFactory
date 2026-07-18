#!/usr/bin/env bash
# 字幕工厂 - 一键启动桌面应用
# 用法: ./start-desktop.sh

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}🎬 字幕工厂 - 一键启动${NC}"
echo "========================"

# ── 检查必要命令 ──
check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        echo -e "${RED}❌ 未找到 $1${NC}"
        case "$1" in
            python3) echo "  请安装 Python 3.9+: https://www.python.org/downloads/" ;;
            node)    echo "  请安装 Node.js 18+: https://nodejs.org/" ;;
            npm)     echo "  请安装 npm (随 Node.js 一起安装)" ;;
            ffmpeg)  echo "  请安装 FFmpeg: brew install ffmpeg" ;;
            yt-dlp)  echo "  请安装 yt-dlp: brew install yt-dlp 或 pip install yt-dlp" ;;
            rustc|cargo) echo "  请安装 Rust: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh" ;;
        esac
        return 1
    fi
    return 0
}

echo -e "${YELLOW}📋 环境检查...${NC}"
check_cmd python3 || MISSING=1
check_cmd node    || MISSING=1
check_cmd npm     || MISSING=1
check_cmd ffmpeg  || MISSING=1
check_cmd yt-dlp  || MISSING=1
check_cmd rustc   || echo -e "${YELLOW}⚠️  rustc 未安装，Tauri 桌面端将无法编译${NC}"
check_cmd cargo   || echo -e "${YELLOW}⚠️  cargo 未安装，Tauri 桌面端将无法编译${NC}"

if [ -n "$MISSING" ]; then
    echo -e "${RED}❌ 存在缺失依赖，请安装后重试${NC}"
    exit 1
fi

# ── 检查 .env ──
if [ ! -f "$DIR/backend/.env" ]; then
    echo -e "${YELLOW}⚠️  未找到 backend/.env，正在从模板创建...${NC}"
    cp "$DIR/backend/.env.example" "$DIR/backend/.env"
    echo -e "${RED}⚠️  请编辑 backend/.env 填入 LLM_API_KEY${NC}"
fi

# ── 检查 Python venv ──
if [ ! -d "$DIR/backend/.venv" ]; then
    echo -e "${YELLOW}📦 创建 Python 虚拟环境...${NC}"
    python3 -m venv "$DIR/backend/.venv"
    source "$DIR/backend/.venv/bin/activate"
else
    source "$DIR/backend/.venv/bin/activate"
fi

# requirements.txt may gain a new packaged runtime (for example sherpa-onnx)
# even when the virtual environment already exists.
echo -e "${YELLOW}📦 检查 Python 依赖...${NC}"
pip install -q -r "$DIR/backend/requirements.txt"

# ── 检查前端依赖 ──
if [ ! -d "$DIR/frontend/node_modules" ]; then
    echo -e "${YELLOW}📦 安装前端依赖...${NC}"
    cd "$DIR/frontend" && npm install -q
    cd "$DIR"
fi

# ── 启动 Tauri 桌面端 ──
echo -e "${GREEN}🚀 启动桌面应用...${NC}"
echo -e "${YELLOW}💡 首次启动需要编译 Rust，可能需要 1-3 分钟${NC}"
cd "$DIR/frontend"
npx tauri dev
