#!/bin/bash
# 字幕工厂 - 后端启动脚本
# Usage: ./run.sh [port]

PORT=${1:-8000}
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🔤 字幕工厂 后端服务"
echo "========================"

# 检查 venv
if [ ! -d "$DIR/.venv" ]; then
    echo "📦 创建虚拟环境..."
    python3 -m venv "$DIR/.venv"
fi

source "$DIR/.venv/bin/activate"

# 检查依赖
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "📦 安装依赖..."
    pip install -r "$DIR/requirements.txt"
fi

echo "🚀 启动后端服务 (端口 $PORT)..."
echo "📖 API 文档: http://127.0.0.1:$PORT/docs"
echo ""

cd "$DIR"
python3 -m uvicorn app.main:app --host 127.0.0.1 --port $PORT --reload
