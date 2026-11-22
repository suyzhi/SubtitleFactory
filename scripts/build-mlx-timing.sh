#!/usr/bin/env bash
# Build the tiny native arm64 C library that replaces numba/llvmlite/scipy for
# mlx_whisper.timing word-timestamp alignment.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_DIR="${1:-$ROOT/backend/runtime/lib}"
MINIMUM_MACOS_VERSION="${2:-14.0}"
SOURCE="$ROOT/backend/runtime/mlx_timing.c"
OUTPUT="$OUTPUT_DIR/libmlx_timing.dylib"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "仅支持在 Apple Silicon macOS 上构建 libmlx_timing.dylib。" >&2
  exit 1
fi
if [ ! -f "$SOURCE" ]; then
  echo "缺少源码：$SOURCE" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
/usr/bin/clang -O3 -std=c99 -arch arm64 \
  "-mmacosx-version-min=${MINIMUM_MACOS_VERSION}" \
  -shared -fPIC \
  -install_name "@rpath/libmlx_timing.dylib" \
  -o "$OUTPUT" "$SOURCE"
strip -x "$OUTPUT"
chmod 755 "$OUTPUT"
codesign --force --sign - "$OUTPUT" >/dev/null 2>&1 || true

ARCHS="$(lipo -archs "$OUTPUT" 2>/dev/null || true)"
if [ "$ARCHS" != "arm64" ]; then
  echo "libmlx_timing.dylib 架构错误：${ARCHS:-未知}（必须是纯 arm64）。" >&2
  exit 1
fi
echo "已构建原生 MLX timing 库：$OUTPUT"
