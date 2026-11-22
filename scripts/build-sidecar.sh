#!/usr/bin/env bash
set -euo pipefail

# Desktop build shells may inherit Python paths from Codex/Hermes or another
# developer tool.  They must never leak into dependency resolution or the
# PyInstaller module graph for the release sidecar.
unset PYTHONPATH PYTHONHOME

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT/backend/.venv/bin/python"
VENDOR_RUNTIME="${SUBTITLE_FACTORY_FFMPEG_VENDOR_DIR:-$ROOT/vendor/ffmpeg/darwin-arm64}"
VENDOR_QUICKJS="${SUBTITLE_FACTORY_QUICKJS_VENDOR_DIR:-$ROOT/vendor/quickjs/darwin-arm64}"
VENDOR_SILERO_VAD="${SUBTITLE_FACTORY_SILERO_VAD_VENDOR_DIR:-$ROOT/vendor/silero-vad}"
DISTRIBUTION_CHANNEL="${SUBTITLE_FACTORY_DISTRIBUTION_CHANNEL:-direct}"
if [ ! -x "$PYTHON" ]; then
  echo "缺少 backend/.venv，请先运行 ./start-desktop.sh 安装依赖。" >&2
  exit 1
fi
MINIMUM_MACOS_VERSION="$(
  "$PYTHON" -c \
    'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["bundle"]["macOS"]["minimumSystemVersion"])' \
    "$ROOT/frontend/src-tauri/tauri.conf.json"
)"

"$ROOT/scripts/verify-release-runtime.sh" "$VENDOR_RUNTIME"
"$ROOT/scripts/fetch-silero-vad.sh"
if [ "$DISTRIBUTION_CHANNEL" != "app_store" ]; then
  SUBTITLE_FACTORY_MINIMUM_MACOS_VERSION="$MINIMUM_MACOS_VERSION" \
    "$ROOT/scripts/fetch-quickjs.sh"
fi

"$PYTHON" -m pip install -q --require-hashes -r "$ROOT/backend/requirements-release.lock"
SITE_PACKAGES="$("$PYTHON" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
MLX_WHEEL_METADATA="$SITE_PACKAGES/mlx-0.32.0.dist-info/WHEEL"
MLX_METAL_WHEEL_METADATA="$SITE_PACKAGES/mlx_metal-0.32.0.dist-info/WHEEL"
if ! grep -F 'Tag: cp311-cp311-macosx_14_0_arm64' "$MLX_WHEEL_METADATA" >/dev/null 2>&1 \
  || ! grep -F 'Tag: py3-none-macosx_14_0_arm64' "$MLX_METAL_WHEEL_METADATA" >/dev/null 2>&1; then
  "$PYTHON" -m pip install -q --require-hashes --force-reinstall --no-deps \
    -r "$ROOT/backend/requirements-macos14.lock"
fi
if ! grep -F 'Tag: cp311-cp311-macosx_14_0_arm64' "$MLX_WHEEL_METADATA" >/dev/null \
  || ! grep -F 'Tag: py3-none-macosx_14_0_arm64' "$MLX_METAL_WHEEL_METADATA" >/dev/null; then
  echo "MLX 没有使用锁定的 macOS 14 官方 wheel。" >&2
  exit 1
fi
"$ROOT/scripts/verify-macos-deployment-target.sh" "$SITE_PACKAGES/mlx" "$MINIMUM_MACOS_VERSION"
OUTPUT_DIR="$ROOT/frontend/src-tauri/backend-runtime"
BUILD_DIR="$ROOT/backend/build/sidecar"
DIST_DIR="$ROOT/backend/dist/sidecar"
if [ -d "$OUTPUT_DIR" ]; then
  find "$OUTPUT_DIR" -depth -delete
fi
mkdir -p "$OUTPUT_DIR"

cd "$ROOT/backend"
PYINSTALLER_ARGS=(
  --noconfirm --clean --onedir
  --name subtitle-backend
  --distpath "$DIST_DIR"
  --workpath "$BUILD_DIR"
  --specpath "$BUILD_DIR"
  # Collect only what is required (avoid --collect-all duplicating .py source
  # trees into _internal alongside PYZ.pyz).
  --collect-submodules faster_whisper
  --collect-binaries ctranslate2
  --collect-submodules ctranslate2
  --collect-binaries mlx
  --collect-data mlx
  --collect-submodules mlx
  --collect-data mlx_whisper
  --collect-submodules mlx_whisper
  --collect-data mlx_qwen3_asr
  --collect-submodules mlx_qwen3_asr
  --collect-binaries tiktoken
  --collect-submodules tiktoken
  --collect-submodules tiktoken_ext
  --collect-binaries sherpa_onnx
  --collect-data sherpa_onnx
  --collect-submodules sherpa_onnx
  --collect-submodules uvicorn
  --collect-submodules pysubs2
  --collect-binaries PIL
  --collect-submodules PIL
  # Slim exclusions: replaced by native libmlx_timing.dylib, bundled FFmpeg,
  # sherpa-onnx bundled ORT + Silero VAD, and clean dev-tool filtering.
  --exclude-module torch
  --exclude-module torchaudio
  --exclude-module torchvision
  --exclude-module numba
  --exclude-module llvmlite
  --exclude-module scipy
  --exclude-module onnxruntime
  --exclude-module av
  --exclude-module hf_xet
  --exclude-module mypy
  --exclude-module mypyc
  --exclude-module ast_serialize
  --exclude-module pytest
  --exclude-module _pytest
  --exclude-module pluggy
  --exclude-module iniconfig
  --exclude-module pygments
  --exclude-module setuptools
  --exclude-module pkg_resources
  --exclude-module distutils
  --exclude-module PyInstaller
  --exclude-module pyinstaller_hooks_contrib
  --exclude-module pip
  --exclude-module wheel
  --exclude-module ruff
  --exclude-module jinja2
  --exclude-module markupsafe
  --exclude-module tkinter
  --exclude-module _tkinter
  --exclude-module IPython
  --exclude-module matplotlib
  --exclude-module pydoc_data
  --hidden-import app.main
)
if [ "$DISTRIBUTION_CHANNEL" != "app_store" ]; then
  PYINSTALLER_ARGS+=(
    --hidden-import app.services.downloader
    --collect-submodules yt_dlp
    --collect-data yt_dlp
    --collect-submodules yt_dlp_ejs
    --collect-data yt_dlp_ejs
  )
else
  PYINSTALLER_ARGS+=(
    --exclude-module app.services.downloader
    --exclude-module yt_dlp
    --exclude-module yt_dlp_ejs
  )
fi
"$PYTHON" -m PyInstaller "${PYINSTALLER_ARGS[@]}" sidecar_main.py

INTERNAL_DIR="$DIST_DIR/subtitle-backend/_internal"

# Remove C++ SDK headers/CMake files shipped inside mlx's wheel (~4 MiB).
for removable_dir in \
  "$INTERNAL_DIR/mlx/include" \
  "$INTERNAL_DIR/mlx/lib/cmake" \
  "$INTERNAL_DIR/mlx/share"; do
  if [ -d "$removable_dir" ]; then
    find "$removable_dir" -depth -delete
  fi
done

# Remove mypyc compiled artifacts if any slipped through hooks.
find "$INTERNAL_DIR" -maxdepth 1 -name '*__mypyc*.so' -delete

# Guard against excluded heavyweight or dev packages leaking back into the bundle.
for forbidden_dir in \
  "$INTERNAL_DIR/llvmlite" \
  "$INTERNAL_DIR/numba" \
  "$INTERNAL_DIR/scipy" \
  "$INTERNAL_DIR/onnxruntime" \
  "$INTERNAL_DIR/av" \
  "$INTERNAL_DIR/hf_xet" \
  "$INTERNAL_DIR/mypy" \
  "$INTERNAL_DIR/ast_serialize" \
  "$INTERNAL_DIR/torch"; do
  if [ -e "$forbidden_dir" ]; then
    echo "瘦身校验失败：已排除依赖意外进入 sidecar：$forbidden_dir" >&2
    exit 1
  fi
done

SHERPA_LIB_DIR="$INTERNAL_DIR/sherpa_onnx/lib"
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
    "$INTERNAL_DIR"/sherpa_onnx*-*.dist-info/RECORD) ;;
    *)
      echo "发现代码引用未版本化的 Sherpa ONNX Runtime：$reference" >&2
      exit 1
      ;;
  esac
done < <(rg -a -l -F 'libonnxruntime.dylib' "$INTERNAL_DIR" || true)
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

# Build tiny native libmlx_timing.dylib (~33 KB) & stage shared Silero VAD (629 KB).
"$ROOT/scripts/build-mlx-timing.sh" "$OUTPUT_DIR/lib" "$MINIMUM_MACOS_VERSION"
mkdir -p "$OUTPUT_DIR/assets"
cp "$VENDOR_SILERO_VAD/silero_vad.onnx" "$OUTPUT_DIR/assets/silero_vad.onnx"

mkdir -p "$OUTPUT_DIR/bin" "$OUTPUT_DIR/THIRD_PARTY_LICENSES/ffmpeg"
cp "$VENDOR_RUNTIME/ffmpeg-darwin-arm64" "$OUTPUT_DIR/bin/ffmpeg"
cp "$VENDOR_RUNTIME/ffprobe-darwin-arm64" "$OUTPUT_DIR/bin/ffprobe"
cp "$VENDOR_RUNTIME/darwin-arm64.LICENSE" "$OUTPUT_DIR/THIRD_PARTY_LICENSES/ffmpeg/LICENSE"
cp "$VENDOR_RUNTIME/darwin-arm64.README" "$OUTPUT_DIR/THIRD_PARTY_LICENSES/ffmpeg/README"
if [ "$DISTRIBUTION_CHANNEL" != "app_store" ]; then
  cp "$VENDOR_QUICKJS/qjs-darwin-arm64" "$OUTPUT_DIR/bin/qjs"
  chmod 755 "$OUTPUT_DIR/bin/qjs"
  codesign --force --sign - "$OUTPUT_DIR/bin/qjs" >/dev/null
  xattr -cr "$OUTPUT_DIR/bin/qjs"
  mkdir -p "$OUTPUT_DIR/THIRD_PARTY_LICENSES/quickjs"
  cp "$VENDOR_QUICKJS/darwin-arm64.LICENSE" "$OUTPUT_DIR/THIRD_PARTY_LICENSES/quickjs/LICENSE"
fi
swiftc -target "arm64-apple-macos${MINIMUM_MACOS_VERSION}" \
  "$ROOT/backend/runtime/vision_ocr.swift" -O -o "$OUTPUT_DIR/bin/vision-ocr"
"$ROOT/scripts/build-parakeet-coreml.sh" "$OUTPUT_DIR/bin"
mkdir -p "$OUTPUT_DIR/THIRD_PARTY_LICENSES/fluidaudio"
# SwiftPM checkouts are read-only; a read-only file makes the bundler's `xattr -crs` fail.
install -m 644 "$ROOT/backend/build/parakeet-coreml/checkouts/FluidAudio/LICENSE" \
  "$OUTPUT_DIR/THIRD_PARTY_LICENSES/fluidaudio/LICENSE"
cp "$ROOT/backend/runtime/parakeet-coreml/NOTICE.md" \
  "$OUTPUT_DIR/THIRD_PARTY_LICENSES/fluidaudio/NOTICE.md"
chmod +x "$OUTPUT_DIR/bin/ffmpeg" "$OUTPUT_DIR/bin/ffprobe"
chmod 755 "$OUTPUT_DIR/bin/vision-ocr"
"$ROOT/scripts/verify-release-runtime.sh" "$OUTPUT_DIR/bin"

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
  if [ -e "$OUTPUT_DIR/bin/deno" ]; then
    echo "瘦身后的直装运行包不应再内置 134MB Deno（应使用 QuickJS-ng）。" >&2
    exit 1
  fi
  "$OUTPUT_DIR/bin/qjs" --version
elif [ -e "$OUTPUT_DIR/bin/deno" ] || [ -e "$OUTPUT_DIR/bin/qjs" ]; then
  echo "App Store 运行包不得包含 Deno 或 QuickJS。" >&2
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
"$ROOT/scripts/verify-macos-deployment-target.sh" "$OUTPUT_DIR" "$MINIMUM_MACOS_VERSION"
echo "已生成瘦身后的快速启动后端: $OUTPUT_DIR"
