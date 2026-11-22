#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${SUBTITLE_FACTORY_QUICKJS_VENDOR_DIR:-$ROOT/vendor/quickjs/darwin-arm64}"
SOURCE_CACHE="$ROOT/vendor/quickjs/source"
VERSION="0.18.0"
BUILD_ROOT="${SUBTITLE_FACTORY_QUICKJS_BUILD_DIR:-${TMPDIR:-/tmp}/subtitle-factory-quickjs-$VERSION}"
ARCHIVE="quickjs-ng-$VERSION.tar.gz"
ARCHIVE_SHA256="28bcd89aa3683fd2a946537e5ce3f11cc46b5cbf3383c7561aaef3df5ff9f1dd"
ARCHIVE_URL="https://github.com/quickjs-ng/quickjs/archive/refs/tags/v$VERSION.tar.gz"
MINIMUM_MACOS_VERSION="${SUBTITLE_FACTORY_MINIMUM_MACOS_VERSION:-14.0}"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "仅支持在 Apple Silicon macOS 上准备发布版 QuickJS-ng。" >&2
  exit 1
fi

verify_qjs() {
  local binary="$1"
  if [ ! -x "$binary" ]; then
    return 1
  fi
  if [ "$(lipo -archs "$binary" 2>/dev/null || true)" != "arm64" ]; then
    echo "QuickJS-ng 架构错误（必须是纯 arm64）：$binary" >&2
    return 1
  fi
  local external
  external="$(otool -L "$binary" | tail -n +2 | awk '{print $1}' | grep -Ev '^(/System/Library/|/usr/lib/)' || true)"
  if [ -n "$external" ]; then
    echo "QuickJS-ng 含非系统动态依赖：$external" >&2
    return 1
  fi
  if ! "$binary" --version 2>&1 | grep -qF "$VERSION"; then
    return 1
  fi
  if [ "$("$binary" -e 'console.log(1 + 2)' 2>/dev/null || true)" != "3" ]; then
    return 1
  fi
  "$ROOT/scripts/verify-macos-deployment-target.sh" "$binary" "$MINIMUM_MACOS_VERSION" >/dev/null
}

if verify_qjs "$DEST/qjs-darwin-arm64" && [ -f "$DEST/darwin-arm64.LICENSE" ]; then
  echo "QuickJS-ng $VERSION 发布运行时已存在：$DEST"
  exit 0
fi

if ! command -v cmake >/dev/null 2>&1; then
  echo "缺少 cmake，无法从源码构建 QuickJS-ng。" >&2
  exit 1
fi

mkdir -p "$SOURCE_CACHE" "$BUILD_ROOT"
if [ ! -f "$SOURCE_CACHE/$ARCHIVE" ]; then
  echo "从 QuickJS-ng 官方下载 $ARCHIVE..."
  curl --fail --location --retry 3 --output "$SOURCE_CACHE/$ARCHIVE.part" "$ARCHIVE_URL"
  mv "$SOURCE_CACHE/$ARCHIVE.part" "$SOURCE_CACHE/$ARCHIVE"
fi

actual_sha256="$(shasum -a 256 "$SOURCE_CACHE/$ARCHIVE" | awk '{print $1}')"
if [ "$actual_sha256" != "$ARCHIVE_SHA256" ]; then
  echo "$ARCHIVE 校验失败：期望 $ARCHIVE_SHA256，实际 $actual_sha256" >&2
  exit 1
fi

rm -rf "$BUILD_ROOT/src" "$BUILD_ROOT/build" "$DEST"
mkdir -p "$BUILD_ROOT/src" "$BUILD_ROOT/build" "$DEST"
tar -xzf "$SOURCE_CACHE/$ARCHIVE" -C "$BUILD_ROOT/src" --strip-components=1

cmake -S "$BUILD_ROOT/src" -B "$BUILD_ROOT/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=/usr/bin/clang \
  -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_OSX_DEPLOYMENT_TARGET="$MINIMUM_MACOS_VERSION" \
  -DQJS_BUILD_EXAMPLES=OFF \
  -DQJS_BUILD_CLI_WITH_STATIC_MIMALLOC=OFF \
  -DBUILD_SHARED_LIBS=OFF

JOBS="$(sysctl -n hw.logicalcpu 2>/dev/null || echo 4)"
cmake --build "$BUILD_ROOT/build" --target qjs_exe -j "$JOBS"

cp "$BUILD_ROOT/build/qjs" "$DEST/qjs-darwin-arm64"
cp "$BUILD_ROOT/src/LICENSE" "$DEST/darwin-arm64.LICENSE"
strip -x "$DEST/qjs-darwin-arm64"
chmod 755 "$DEST/qjs-darwin-arm64"
codesign --force --sign - "$DEST/qjs-darwin-arm64" >/dev/null
rm -rf "$BUILD_ROOT"

verify_qjs "$DEST/qjs-darwin-arm64"
echo "QuickJS-ng $VERSION 发布运行时已准备在：$DEST"
