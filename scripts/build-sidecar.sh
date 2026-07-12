#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT/backend/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "缺少 backend/.venv，请先运行 ./start-desktop.sh 安装依赖。" >&2
  exit 1
fi

"$PYTHON" -m pip install -q -r "$ROOT/backend/requirements.txt" "pyinstaller>=6.0"
TRIPLE="$(rustc -vV | awk '/host:/ {print $2}')"
OUTPUT_DIR="$ROOT/frontend/src-tauri/backend-runtime"
BUILD_DIR="$ROOT/backend/build/sidecar"
DIST_DIR="$ROOT/backend/dist/sidecar"
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

cd "$ROOT/backend"
"$PYTHON" -m PyInstaller \
  --noconfirm --clean --onedir \
  --name subtitle-backend \
  --distpath "$DIST_DIR" \
  --workpath "$BUILD_DIR" \
  --specpath "$BUILD_DIR" \
  --collect-all faster_whisper \
  --collect-all ctranslate2 \
  --collect-all sherpa_onnx \
  --collect-all av \
  --collect-all uvicorn \
  --collect-all pysubs2 \
  --collect-all yt_dlp \
  --exclude-module torch \
  --hidden-import app.main \
  sidecar_main.py

cp -R "$DIST_DIR/subtitle-backend/." "$OUTPUT_DIR/"
chmod +x "$OUTPUT_DIR/subtitle-backend"
echo "已生成快速启动后端: $OUTPUT_DIR"
