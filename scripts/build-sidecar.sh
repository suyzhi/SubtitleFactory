#!/usr/bin/env bash
set -euo pipefail

# Desktop build shells may inherit Python paths from Codex/Hermes or another
# developer tool.  They must never leak into dependency resolution or the
# PyInstaller module graph for the release sidecar.
unset PYTHONPATH PYTHONHOME

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT/backend/.venv/bin/python"
VENDOR_RUNTIME="${SUBTITLE_FACTORY_FFMPEG_VENDOR_DIR:-$ROOT/vendor/ffmpeg/darwin-arm64}"
DISTRIBUTION_CHANNEL="${SUBTITLE_FACTORY_DISTRIBUTION_CHANNEL:-direct}"
DENO_SOURCE=""
if [ "$DISTRIBUTION_CHANNEL" != "app_store" ]; then
  DENO_SOURCE="${SUBTITLE_FACTORY_DENO_BIN:-$(command -v deno || true)}"
fi

if [ ! -x "$PYTHON" ]; then
  echo "缺少 backend/.venv，请先运行 ./start-desktop.sh 安装依赖。" >&2
  exit 1
fi
if [ "$DISTRIBUTION_CHANNEL" != "app_store" ] \
  && { [ -z "$DENO_SOURCE" ] || [ ! -x "$DENO_SOURCE" ]; }; then
  echo "缺少 Deno JavaScript 运行时，无法构建可靠的 YouTube 下载器。请先安装 Deno。" >&2
  exit 1
fi

"$ROOT/scripts/verify-release-runtime.sh" "$VENDOR_RUNTIME"

"$PYTHON" -m pip install -q --require-hashes -r "$ROOT/backend/requirements-release.lock"
TRIPLE="$(rustc -vV | awk '/host:/ {print $2}')"
OUTPUT_DIR="$ROOT/frontend/src-tauri/backend-runtime"
BUILD_DIR="$ROOT/backend/build/sidecar"
DIST_DIR="$ROOT/backend/dist/sidecar"
if [ -d "$OUTPUT_DIR" ]; then
  find "$OUTPUT_DIR" -depth -delete
fi
mkdir -p "$OUTPUT_DIR"

cd "$ROOT/backend"
PYINSTALLER_ARGS=(
  --noconfirm --clean --onedir \
  --name subtitle-backend \
  --distpath "$DIST_DIR" \
  --workpath "$BUILD_DIR" \
  --specpath "$BUILD_DIR" \
  --collect-all faster_whisper \
  --collect-all ctranslate2 \
  --collect-all mlx \
  --collect-all mlx_whisper \
  --collect-all mlx_qwen3_asr \
  --collect-all tiktoken \
  --collect-all sherpa_onnx \
  --collect-all av \
  --collect-all uvicorn \
  --collect-all pysubs2 \
  --collect-all PIL \
  --exclude-module torch \
  --hidden-import app.main
)
if [ "$DISTRIBUTION_CHANNEL" != "app_store" ]; then
  PYINSTALLER_ARGS+=(--hidden-import app.services.downloader --collect-all yt_dlp)
else
  PYINSTALLER_ARGS+=(--exclude-module app.services.downloader --exclude-module yt_dlp)
fi
"$PYTHON" -m PyInstaller "${PYINSTALLER_ARGS[@]}" sidecar_main.py

SHERPA_LIB_DIR="$DIST_DIR/subtitle-backend/_internal/sherpa_onnx/lib"
SHERPA_ORT_ALIAS="$SHERPA_LIB_DIR/libonnxruntime.dylib"
SHERPA_ORT_VERSIONED="$SHERPA_LIB_DIR/libonnxruntime.1.24.4.dylib"
if [ ! -f "$SHERPA_ORT_ALIAS" ] || [ ! -f "$SHERPA_ORT_VERSIONED" ]; then
  echo "Sherpa ONNX Runtime 文件布局与固定的 1.13.3 运行包不一致。" >&2
  exit 1
fi

# sherpa-onnx 1.13.3's wheel ships these as two byte-identical Mach-O files,
# even though every binary links the versioned install name. PyInstaller signs
# each copy separately, so normalize temporary copies before proving identity.
ORT_COMPARE_DIR="$(mktemp -d /tmp/subtitle-factory-ort-compare.XXXXXX)"
cleanup_ort_compare() {
  if [ -d "$ORT_COMPARE_DIR" ]; then
    find "$ORT_COMPARE_DIR" -depth -delete
  fi
}
trap cleanup_ort_compare EXIT
cp "$SHERPA_ORT_ALIAS" "$ORT_COMPARE_DIR/unversioned.dylib"
cp "$SHERPA_ORT_VERSIONED" "$ORT_COMPARE_DIR/versioned.dylib"
codesign --remove-signature "$ORT_COMPARE_DIR/unversioned.dylib" 2>/dev/null || true
codesign --remove-signature "$ORT_COMPARE_DIR/versioned.dylib" 2>/dev/null || true
if ! cmp -s "$ORT_COMPARE_DIR/unversioned.dylib" "$ORT_COMPARE_DIR/versioned.dylib"; then
  echo "Sherpa 的两个 ONNX Runtime 文件不再相同，拒绝合并。" >&2
  exit 1
fi
while IFS= read -r reference; do
  case "$reference" in
    "$DIST_DIR/subtitle-backend/_internal"/sherpa_onnx*-*.dist-info/RECORD) ;;
    *)
      echo "发现代码引用未版本化的 Sherpa ONNX Runtime：$reference" >&2
      exit 1
      ;;
  esac
done < <(rg -a -l -F 'libonnxruntime.dylib' \
  "$DIST_DIR/subtitle-backend/_internal" || true)
SHERPA_DUPLICATE_BYTES="$(stat -f %z "$SHERPA_ORT_ALIAS")"
cleanup_ort_compare
trap - EXIT
find "$SHERPA_ORT_ALIAS" -delete
ln -s "$(basename "$SHERPA_ORT_VERSIONED")" "$SHERPA_ORT_ALIAS"
echo "Sherpa ONNX Runtime 合并通过：避免复制 ${SHERPA_DUPLICATE_BYTES} bytes。"

cp -R "$DIST_DIR/subtitle-backend/." "$OUTPUT_DIR/"
chmod +x "$OUTPUT_DIR/subtitle-backend"
OUTPUT_ORT_ALIAS="$OUTPUT_DIR/_internal/sherpa_onnx/lib/libonnxruntime.dylib"
if [ ! -L "$OUTPUT_ORT_ALIAS" ] \
  || [ "$(readlink "$OUTPUT_ORT_ALIAS")" != "libonnxruntime.1.24.4.dylib" ]; then
  echo "Sherpa ONNX Runtime 的安全相对链接没有进入 sidecar 运行包。" >&2
  exit 1
fi
SMOKE_HOME="$(mktemp -d /tmp/subtitle-factory-runtime-smoke.XXXXXX)"
cleanup_smoke_home() {
  if [ -d "$SMOKE_HOME" ]; then
    find "$SMOKE_HOME" -depth -delete
  fi
}
trap cleanup_smoke_home EXIT
if ! HOME="$SMOKE_HOME" XDG_CACHE_HOME="$SMOKE_HOME/cache" HF_HOME="$SMOKE_HOME/huggingface" \
  "$OUTPUT_DIR/subtitle-backend" --verify-runtime; then
  echo "冻结后端的原生运行时自检失败。" >&2
  exit 1
fi
cleanup_smoke_home
trap - EXIT
mkdir -p "$OUTPUT_DIR/bin" "$OUTPUT_DIR/THIRD_PARTY_LICENSES/ffmpeg"
cp "$VENDOR_RUNTIME/ffmpeg-darwin-arm64" "$OUTPUT_DIR/bin/ffmpeg"
cp "$VENDOR_RUNTIME/ffprobe-darwin-arm64" "$OUTPUT_DIR/bin/ffprobe"
cp "$VENDOR_RUNTIME/darwin-arm64.LICENSE" "$OUTPUT_DIR/THIRD_PARTY_LICENSES/ffmpeg/LICENSE"
cp "$VENDOR_RUNTIME/darwin-arm64.README" "$OUTPUT_DIR/THIRD_PARTY_LICENSES/ffmpeg/README"
if [ "$DISTRIBUTION_CHANNEL" != "app_store" ]; then
  DENO_REAL="$("$PYTHON" -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$DENO_SOURCE")"
  DENO_ROOT="$(cd "$(dirname "$DENO_REAL")/.." && pwd)"
  DENO_STAGE="$(mktemp /tmp/subtitle-factory-deno.XXXXXX)"
  cp "$DENO_REAL" "$DENO_STAGE"
  chmod +x "$DENO_STAGE"
  codesign --remove-signature "$DENO_STAGE" 2>/dev/null || true
  codesign --force --sign - "$DENO_STAGE"
  xattr -cr "$DENO_STAGE"
  mv "$DENO_STAGE" "$OUTPUT_DIR/bin/deno"
  if [ -f "$DENO_ROOT/LICENSE.md" ]; then
    mkdir -p "$OUTPUT_DIR/THIRD_PARTY_LICENSES/deno"
    cp "$DENO_ROOT/LICENSE.md" "$OUTPUT_DIR/THIRD_PARTY_LICENSES/deno/LICENSE.md"
  fi
fi
swiftc "$ROOT/backend/runtime/vision_ocr.swift" -O -o "$OUTPUT_DIR/bin/vision-ocr"
chmod +x "$OUTPUT_DIR/bin/ffmpeg" "$OUTPUT_DIR/bin/ffprobe"
if [ "$DISTRIBUTION_CHANNEL" != "app_store" ]; then
  chmod 755 "$OUTPUT_DIR/bin/deno"
fi
chmod 755 "$OUTPUT_DIR/bin/vision-ocr"
"$ROOT/scripts/verify-release-runtime.sh" "$OUTPUT_DIR/bin"
VISION_ARCHS="$(lipo -archs "$OUTPUT_DIR/bin/vision-ocr" 2>/dev/null || true)"
if [ "$VISION_ARCHS" != "arm64" ]; then
  echo "Vision OCR helper 架构错误：${VISION_ARCHS:-未知}（必须是纯 arm64）。" >&2
  exit 1
fi
if ! "$OUTPUT_DIR/bin/vision-ocr" "$ROOT/frontend/src-tauri/icons/32x32.png" \
  | "$PYTHON" -c 'import json,sys; value=json.load(sys.stdin); assert isinstance(value,list)'; then
  echo "Vision OCR helper 无法读取测试图片并输出 JSON。" >&2
  exit 1
fi
if [ "$DISTRIBUTION_CHANNEL" != "app_store" ]; then
  "$OUTPUT_DIR/bin/deno" --version
elif [ -e "$OUTPUT_DIR/bin/deno" ]; then
  echo "App Store 运行包不得包含 Deno。" >&2
  exit 1
fi
if [ "$DISTRIBUTION_CHANNEL" = "app_store" ] \
  && find "$OUTPUT_DIR" -iname '*yt_dlp*' -print -quit | grep -q .; then
  echo "App Store 运行包不得包含 yt-dlp。" >&2
  exit 1
fi
ARCHIVE_CONTENTS="$BUILD_DIR/archive-contents.txt"
"$ROOT/backend/.venv/bin/pyi-archive_viewer" -r -b \
  "$OUTPUT_DIR/subtitle-backend" > "$ARCHIVE_CONTENTS"
if [ "$DISTRIBUTION_CHANNEL" = "app_store" ]; then
  if grep -F 'app.services.downloader' "$ARCHIVE_CONTENTS" >/dev/null; then
    echo "App Store 运行包不得包含下载器实现。" >&2
    exit 1
  fi
elif ! grep -F 'app.services.downloader' "$ARCHIVE_CONTENTS" >/dev/null; then
  echo "直装版运行包缺少按需加载的下载器实现。" >&2
  exit 1
fi
echo "已生成快速启动后端: $OUTPUT_DIR"
