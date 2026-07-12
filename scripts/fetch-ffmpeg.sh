#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${SUBTITLE_FACTORY_FFMPEG_VENDOR_DIR:-$ROOT/vendor/ffmpeg/darwin-arm64}"
SOURCE_CACHE="$ROOT/vendor/ffmpeg/source"
VERSION="8.1.2"
BUILD_ROOT="${SUBTITLE_FACTORY_FFMPEG_BUILD_DIR:-${TMPDIR:-/tmp}/subtitle-factory-ffmpeg-$VERSION}"
ARCHIVE="ffmpeg-$VERSION.tar.xz"
ARCHIVE_SHA256="464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c"
BASE_URL="https://ffmpeg.org/releases"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "仅支持在 Apple Silicon macOS 上准备发布版 FFmpeg。" >&2
  exit 1
fi

if [ -x "$DEST/ffmpeg-darwin-arm64" ] \
  && "$DEST/ffmpeg-darwin-arm64" -hide_banner -version 2>/dev/null | head -n 1 | grep -q "ffmpeg version $VERSION"; then
  "$ROOT/scripts/verify-release-runtime.sh" "$DEST"
  echo "FFmpeg $VERSION 发布运行时已存在：$DEST"
  exit 0
fi

mkdir -p "$SOURCE_CACHE" "$BUILD_ROOT"
if [ ! -f "$SOURCE_CACHE/$ARCHIVE" ]; then
  echo "从 FFmpeg 官方下载 $ARCHIVE..."
  curl --fail --location --retry 3 --output "$SOURCE_CACHE/$ARCHIVE.part" "$BASE_URL/$ARCHIVE"
  mv "$SOURCE_CACHE/$ARCHIVE.part" "$SOURCE_CACHE/$ARCHIVE"
fi

actual_sha256="$(shasum -a 256 "$SOURCE_CACHE/$ARCHIVE" | awk '{print $1}')"
if [ "$actual_sha256" != "$ARCHIVE_SHA256" ]; then
  echo "$ARCHIVE 校验失败：期望 $ARCHIVE_SHA256，实际 $actual_sha256" >&2
  exit 1
fi

rm -rf "$BUILD_ROOT/ffmpeg-$VERSION" "$BUILD_ROOT/work" "$DEST"
tar -xf "$SOURCE_CACHE/$ARCHIVE" -C "$BUILD_ROOT"
mkdir -p "$BUILD_ROOT/work" "$DEST"

cd "$BUILD_ROOT/work"
"$BUILD_ROOT/ffmpeg-$VERSION/configure" \
  --prefix="$BUILD_ROOT/install" \
  --arch=arm64 \
  --target-os=darwin \
  --cc=/usr/bin/clang \
  --disable-shared \
  --enable-static \
  --disable-autodetect \
  --disable-doc \
  --disable-debug \
  --disable-ffplay \
  --enable-audiotoolbox \
  --enable-videotoolbox \
  --enable-securetransport \
  --extra-cflags=-mmacosx-version-min=12.0 \
  --extra-ldflags=-mmacosx-version-min=12.0

JOBS="$(sysctl -n hw.logicalcpu 2>/dev/null || echo 4)"
make -j "$JOBS" ffmpeg ffprobe

cp ffmpeg "$DEST/ffmpeg-darwin-arm64"
cp ffprobe "$DEST/ffprobe-darwin-arm64"
cp "$BUILD_ROOT/ffmpeg-$VERSION/COPYING.LGPLv2.1" "$DEST/darwin-arm64.LICENSE"
cp "$ROOT/scripts/ffmpeg-runtime-notice.txt" "$DEST/darwin-arm64.README"
strip -x "$DEST/ffmpeg-darwin-arm64" "$DEST/ffprobe-darwin-arm64"
chmod +x "$DEST/ffmpeg-darwin-arm64" "$DEST/ffprobe-darwin-arm64"
codesign --force --sign - "$DEST/ffmpeg-darwin-arm64" "$DEST/ffprobe-darwin-arm64" >/dev/null
"$ROOT/scripts/verify-release-runtime.sh" "$DEST"
echo "FFmpeg 发布运行时已准备在：$DEST"
