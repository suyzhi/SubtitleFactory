#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "v0.2.0 Release 只能在 Apple Silicon macOS 上构建。" >&2
  exit 1
fi

"$ROOT/scripts/verify-release-runtime.sh"
"$ROOT/scripts/build-sidecar.sh"
cd "$ROOT/frontend"
npm run lint
npm run build
npx tauri build --target aarch64-apple-darwin --bundles app,dmg

BUNDLE_DIR="$ROOT/frontend/src-tauri/target/aarch64-apple-darwin/release/bundle"
APP_PATH="$BUNDLE_DIR/macos/字幕工厂.app"
DMG_PATH="$(find "$BUNDLE_DIR/dmg" -maxdepth 1 -type f -name '*.dmg' -print -quit)"

if [ ! -d "$APP_PATH" ] || [ -z "$DMG_PATH" ]; then
  echo "Tauri 构建未生成预期的 App 或 DMG。" >&2
  exit 1
fi

PACKAGED_RUNTIME="$APP_PATH/Contents/Resources/backend-runtime/bin"
"$ROOT/scripts/verify-release-runtime.sh" "$PACKAGED_RUNTIME"
codesign --verify --deep --strict "$APP_PATH"
(
  cd "$(dirname "$DMG_PATH")"
  shasum -a 256 "$(basename "$DMG_PATH")" > "$(basename "$DMG_PATH").sha256"
)

echo "Release App: $APP_PATH"
echo "Release DMG: $DMG_PATH"
echo "SHA-256: $DMG_PATH.sha256"
