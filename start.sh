#!/bin/bash
# 字幕工厂 - 一键启动（后端 + 前端开发模式）
# 需要先安装依赖

DIR="$(cd "$(dirname "$0")" && pwd)"
DEV_TOKEN="subtitle-factory-local-development"
export SUBTITLE_FACTORY_API_TOKEN="$DEV_TOKEN"
export VITE_API_TOKEN="$DEV_TOKEN"

echo "🎬 字幕工厂 - 一键启动"
echo "======================="

# 启动后端
echo "📦 启动后端服务..."
cd "$DIR/backend"
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "⚠️  请编辑 backend/.env 填入 LLM_API_KEY"
fi

if [ ! -d ".venv" ]; then
    echo "📦 创建虚拟环境..."
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt 2>/dev/null

# 后台启动后端
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload &
BACKEND_PID=$!
echo "✅ 后端已启动 (PID: $BACKEND_PID)"
echo "📖 API 文档: http://127.0.0.1:8000/docs"

# 等待后端启动
sleep 2

# 启动前端
echo ""
echo "📦 启动前端开发服务器..."
cd "$DIR/frontend"
npm install -q 2>/dev/null
npm run dev &
FRONTEND_PID=$!
echo "✅ 前端已启动 (PID: $FRONTEND_PID)"

echo ""
echo "======================="
echo "🌐 前端:   http://localhost:5173"
echo "📖 API:    http://127.0.0.1:8000/docs"
echo ""
echo "按 Ctrl+C 停止所有服务"

# 等待任一进程退出
wait $BACKEND_PID $FRONTEND_PID
