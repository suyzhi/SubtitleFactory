#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="${1:-${SUBTITLE_FACTORY_FFMPEG_VENDOR_DIR:-$ROOT/vendor/ffmpeg/darwin-arm64}}"

find_binary() {
  local short_name="$1"
  local release_name="$2"
  if [ -f "$RUNTIME_DIR/$short_name" ]; then
    printf '%s\n' "$RUNTIME_DIR/$short_name"
  else
    printf '%s\n' "$RUNTIME_DIR/$release_name"
  fi
}

FFMPEG="$(find_binary ffmpeg ffmpeg-darwin-arm64)"
FFPROBE="$(find_binary ffprobe ffprobe-darwin-arm64)"

verify_binary() {
  local binary="$1"
  local label="$2"
  if [ ! -f "$binary" ]; then
    echo "缺少 $label：$binary。先运行 ./scripts/fetch-ffmpeg.sh；禁止生成 Release。" >&2
    exit 1
  fi
  if [ ! -x "$binary" ]; then
    echo "$label 不可执行：$binary；禁止生成 Release。" >&2
    exit 1
  fi
  local archs
  archs="$(lipo -archs "$binary" 2>/dev/null || true)"
  if [ "$archs" != "arm64" ]; then
    echo "$label 架构错误：${archs:-未知}（必须是纯 arm64）；禁止生成 Release。" >&2
    exit 1
  fi
  if ! "$binary" -hide_banner -version >/dev/null 2>&1; then
    echo "$label 无法运行：$binary；禁止生成 Release。" >&2
    exit 1
  fi
  if "$binary" -hide_banner -version 2>&1 | grep -q -- '--enable-nonfree'; then
    echo "$label 启用了不可再分发的 --enable-nonfree；禁止生成 Release。" >&2
    exit 1
  fi
  local external
  external="$(otool -L "$binary" | tail -n +2 | awk '{print $1}' | grep -Ev '^(/System/Library/|/usr/lib/)' || true)"
  if [ -n "$external" ]; then
    echo "$label 含非系统动态依赖，无法用于干净环境：" >&2
    echo "$external" >&2
    exit 1
  fi
}

verify_binary "$FFMPEG" "FFmpeg"
verify_binary "$FFPROBE" "FFprobe"

echo "FFmpeg 运行时检查通过：arm64、可执行、无 Homebrew 动态依赖。"
