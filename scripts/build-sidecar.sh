#!/usr/bin/env bash
set -euo pipefail

# Desktop build shells may inherit Python paths from Codex/Hermes or another
# developer tool.  They must never leak into dependency resolution or the
# PyInstaller module graph for the release sidecar.
unset PYTHONPATH PYTHONHOME

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT/backend/.venv/bin/python"
VENDOR_RUNTIME="${SUBTITLE_FACTORY_FFMPEG_VENDOR_DIR:-$ROOT/vendor/ffmpeg/darwin-arm64}"
DENO_SOURCE="${SUBTITLE_FACTORY_DENO_BIN:-$(command -v deno || true)}"

if [ ! -x "$PYTHON" ]; then
  echo "缺少 backend/.venv，请先运行 ./start-desktop.sh 安装依赖。" >&2
  exit 1
fi
if [ -z "$DENO_SOURCE" ] || [ ! -x "$DENO_SOURCE" ]; then
  echo "缺少 Deno JavaScript 运行时，无法构建可靠的 YouTube 下载器。请先安装 Deno。" >&2
  exit 1
fi

"$ROOT/scripts/verify-release-runtime.sh" "$VENDOR_RUNTIME"

"$PYTHON" -m pip install -q --require-hashes -r "$ROOT/backend/requirements-release.lock"
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
  --collect-all mlx \
  --collect-all mlx_whisper \
  --collect-all tiktoken \
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
mkdir -p "$OUTPUT_DIR/bin" "$OUTPUT_DIR/THIRD_PARTY_LICENSES/ffmpeg"
cp "$VENDOR_RUNTIME/ffmpeg-darwin-arm64" "$OUTPUT_DIR/bin/ffmpeg"
cp "$VENDOR_RUNTIME/ffprobe-darwin-arm64" "$OUTPUT_DIR/bin/ffprobe"
DENO_REAL="$("$PYTHON" -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$DENO_SOURCE")"
DENO_ROOT="$(cd "$(dirname "$DENO_REAL")/.." && pwd)"
DENO_STAGE="$(mktemp /tmp/subtitle-factory-deno.XXXXXX)"
cp "$DENO_REAL" "$DENO_STAGE"
chmod +x "$DENO_STAGE"
codesign --remove-signature "$DENO_STAGE" 2>/dev/null || true
codesign --force --sign - "$DENO_STAGE"
xattr -cr "$DENO_STAGE"
mv "$DENO_STAGE" "$OUTPUT_DIR/bin/deno"
cp "$VENDOR_RUNTIME/darwin-arm64.LICENSE" "$OUTPUT_DIR/THIRD_PARTY_LICENSES/ffmpeg/LICENSE"
cp "$VENDOR_RUNTIME/darwin-arm64.README" "$OUTPUT_DIR/THIRD_PARTY_LICENSES/ffmpeg/README"
if [ -f "$DENO_ROOT/LICENSE.md" ]; then
  mkdir -p "$OUTPUT_DIR/THIRD_PARTY_LICENSES/deno"
  cp "$DENO_ROOT/LICENSE.md" "$OUTPUT_DIR/THIRD_PARTY_LICENSES/deno/LICENSE.md"
fi
swiftc "$ROOT/backend/runtime/vision_ocr.swift" -O -o "$OUTPUT_DIR/bin/vision-ocr"
chmod +x "$OUTPUT_DIR/bin/ffmpeg" "$OUTPUT_DIR/bin/ffprobe"
chmod 755 "$OUTPUT_DIR/bin/deno"
"$ROOT/scripts/verify-release-runtime.sh" "$OUTPUT_DIR/bin"
"$OUTPUT_DIR/bin/deno" --version
echo "已生成快速启动后端: $OUTPUT_DIR"
