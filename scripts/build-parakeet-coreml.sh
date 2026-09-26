#!/usr/bin/env bash
# Build the open-source Parakeet Core ML runner (FluidAudio) into <output-bin-dir>.
# Default output is backend/runtime/bin, which the development backend treats as
# its bundled-runtime location. Build intermediates stay in backend/build.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_BIN="${1:-$ROOT/backend/runtime/bin}"
PACKAGE="$ROOT/backend/runtime/parakeet-coreml"
SCRATCH="$ROOT/backend/build/parakeet-coreml"

if ! command -v swift >/dev/null 2>&1; then
  echo "缺少 Swift 工具链，无法构建 Parakeet Core ML 转写组件（需要 Xcode 或 Command Line Tools）。" >&2
  exit 1
fi

swift build -c release --arch arm64 --package-path "$PACKAGE" --scratch-path "$SCRATCH"
BINARY="$(swift build -c release --arch arm64 --package-path "$PACKAGE" --scratch-path "$SCRATCH" --show-bin-path)/parakeet-coreml"
mkdir -p "$OUTPUT_BIN"
cp "$BINARY" "$OUTPUT_BIN/parakeet-coreml"
chmod 755 "$OUTPUT_BIN/parakeet-coreml"
codesign --force --sign - "$OUTPUT_BIN/parakeet-coreml" >/dev/null 2>&1 || true

ARCHS="$(lipo -archs "$OUTPUT_BIN/parakeet-coreml" 2>/dev/null || true)"
if [ "$ARCHS" != "arm64" ]; then
  echo "Parakeet Core ML 转写组件架构错误：${ARCHS:-未知}（必须是纯 arm64）。" >&2
  exit 1
fi
if ! "$OUTPUT_BIN/parakeet-coreml" --help | grep -q -- "--output-format"; then
  echo "Parakeet Core ML 转写组件自检失败。" >&2
  exit 1
fi
echo "Parakeet Core ML 转写组件已构建：$OUTPUT_BIN/parakeet-coreml"
