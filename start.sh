#!/bin/bash
# Run the personal local server and browser UI. Ctrl+C stops both services.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -x "$DIR/backend/.venv/bin/python" ] || [ ! -d "$DIR/frontend/node_modules" ]; then
    echo "请先安装 backend/.venv 和 frontend/node_modules 中的项目依赖。"
    exit 1
fi
for port in 8000 5173; do
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "端口 $port 已被占用，请先停止原有服务。"
        exit 1
    fi
done
BACKEND_PID=""
FRONTEND_PID=""
cleanup() {
    trap - EXIT INT TERM
    [ -z "$FRONTEND_PID" ] || kill "$FRONTEND_PID" 2>/dev/null || true
    [ -z "$BACKEND_PID" ] || kill "$BACKEND_PID" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM
cd "$DIR/backend"
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
cd "$DIR/frontend"
VITE_API_BASE_URL=same-origin node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort &
FRONTEND_PID=$!
for attempt in {1..60}; do
    kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null || exit 1
    if curl -fsS --max-time 2 http://127.0.0.1:8000/openapi.json >/dev/null 2>&1 && curl -fsS --max-time 2 http://127.0.0.1:5173 >/dev/null 2>&1; then
        echo "字幕工厂已就绪：http://127.0.0.1:5173"
        echo "按 Ctrl+C 停止前后端。"
        while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do sleep 1; done
        exit 1
    fi
    sleep 1
done
echo "服务启动超时，请检查上方日志。"
exit 1
