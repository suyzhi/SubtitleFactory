#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="0.4.1"
UI_MARKER="subtitle-factory-ui:professional-v2"
UI_LAYOUT_MARKER="subtitle-factory-ui:library-workspace-v2"
OLD_UI_MARKER="ai-settings-dialog"

# A direct release must not inherit an App Store channel from the caller's
# shell; both the Rust launcher and initial WebView render use these values.
export SUBTITLE_FACTORY_DISTRIBUTION_CHANNEL="direct"
export VITE_DISTRIBUTION_CHANNEL="direct"

cleanup_build_outputs() {
  for generated_dir in \
    "$ROOT/frontend/dist" \
    "$ROOT/frontend/src-tauri/target" \
    "$ROOT/frontend/src-tauri/backend-runtime" \
    "$ROOT/backend/build" \
    "$ROOT/backend/dist" \
    "$ROOT/.pytest_cache" \
    "$ROOT/backend/.pytest_cache"; do
    if [ -d "$generated_dir" ]; then
      find "$generated_dir" -depth -delete
    fi
  done
  while IFS= read -r -d '' cache_dir; do
    find "$cache_dir" -depth -delete
  done < <(find "$ROOT/backend" "$ROOT/frontend" \
    \( -path "$ROOT/backend/.venv" -o -path "$ROOT/frontend/node_modules" \) -prune -o \
    -type d -name __pycache__ -print0)
}

trap cleanup_build_outputs EXIT

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "v${VERSION} Release 只能在 Apple Silicon macOS 上构建。" >&2
  exit 1
fi

"$ROOT/backend/.venv/bin/python" "$ROOT/scripts/check-versions.py"
"$ROOT/backend/.venv/bin/python" "$ROOT/scripts/verify-model-sources.py"
"$ROOT/backend/.venv/bin/python" -m pytest -q "$ROOT/backend/tests"

cd "$ROOT/frontend"
npm run test
npm run lint

"$ROOT/scripts/verify-release-runtime.sh"
"$ROOT/scripts/build-sidecar.sh"
cargo test --manifest-path "$ROOT/frontend/src-tauri/Cargo.toml" --locked

if ! rg -F "$UI_MARKER" "$ROOT/frontend/src/App.tsx" >/dev/null \
  || ! rg -F "$UI_LAYOUT_MARKER" "$ROOT/frontend/src/App.tsx" >/dev/null \
  || ! rg -F "library-home" "$ROOT/frontend/src/App.tsx" >/dev/null \
  || ! rg -F "workspace-active" "$ROOT/frontend/src/App.tsx" >/dev/null \
  || ! rg -F "import App from './App.tsx'" "$ROOT/frontend/src/main.tsx" >/dev/null; then
  echo "新版项目库 / 独立项目工作区源码标记或入口缺失。" >&2
  exit 1
fi
if rg -n "AISettingsDialog" "$ROOT/frontend/src/main.tsx" "$ROOT/frontend/src/App.tsx" >/dev/null; then
  echo "发布入口错误地依赖旧 AISettingsDialog。" >&2
  exit 1
fi

npm run build
if ! rg -F "$UI_MARKER" "$ROOT/frontend/dist" >/dev/null; then
  echo "Vite 产物缺少 professional-v2 标记。" >&2
  exit 1
fi
if ! rg -F "$UI_LAYOUT_MARKER" "$ROOT/frontend/dist" >/dev/null; then
  echo "Vite 产物缺少 library-workspace-v2 标记。" >&2
  exit 1
fi
if rg -F "$OLD_UI_MARKER" "$ROOT/frontend/dist" >/dev/null; then
  echo "Vite 产物包含旧 UI 标记：$OLD_UI_MARKER" >&2
  exit 1
fi

npx tauri build --target aarch64-apple-darwin --bundles app,dmg

BUNDLE_DIR="$ROOT/frontend/src-tauri/target/aarch64-apple-darwin/release/bundle"
APP_PATH="$BUNDLE_DIR/macos/字幕工厂.app"
DMG_PATH="$(find "$BUNDLE_DIR/dmg" -maxdepth 1 -type f -name '*.dmg' -print -quit)"

if [ ! -d "$APP_PATH" ] || [ -z "$DMG_PATH" ]; then
  echo "Tauri 构建未生成预期的 App 或 DMG。" >&2
  exit 1
fi
if [ "$(/usr/libexec/PlistBuddy -c 'Print :LSApplicationCategoryType' "$APP_PATH/Contents/Info.plist" 2>/dev/null || true)" \
  != "public.app-category.video" ]; then
  echo "最终 App 缺少 Video 应用类别。" >&2
  exit 1
fi

PACKAGED_RUNTIME="$APP_PATH/Contents/Resources/backend-runtime/bin"
"$ROOT/scripts/verify-release-runtime.sh" "$PACKAGED_RUNTIME"
if [ ! -x "$PACKAGED_RUNTIME/vision-ocr" ] \
  || [ "$(lipo -archs "$PACKAGED_RUNTIME/vision-ocr" 2>/dev/null || true)" != "arm64" ] \
  || ! "$PACKAGED_RUNTIME/vision-ocr" "$ROOT/frontend/src-tauri/icons/32x32.png" \
    | "$ROOT/backend/.venv/bin/python" -c 'import json,sys; value=json.load(sys.stdin); assert isinstance(value,list)'; then
  echo "最终 App 的 Vision OCR helper 缺失、架构错误或无法运行。" >&2
  exit 1
fi
codesign --verify --deep --strict "$APP_PATH"
APP_EXECUTABLE="$APP_PATH/Contents/MacOS/app"
if ! file "$APP_EXECUTABLE" | rg -F "arm64" >/dev/null; then
  echo "最终 App 可执行文件不是 arm64。" >&2
  exit 1
fi
if ! strings "$APP_EXECUTABLE" | rg -F "$UI_MARKER" >/dev/null; then
  echo "最终 App 可执行文件缺少 professional-v2 标记。" >&2
  exit 1
fi
if ! strings "$APP_EXECUTABLE" | rg -F "$UI_LAYOUT_MARKER" >/dev/null; then
  echo "最终 App 可执行文件缺少 library-workspace-v2 标记。" >&2
  exit 1
fi
if strings "$APP_EXECUTABLE" | rg -F "$OLD_UI_MARKER" >/dev/null; then
  echo "最终 App 可执行文件包含旧 UI 标记。" >&2
  exit 1
fi

ARCHIVE_DIR="$ROOT/release-archive/$(date +%Y%m%d-%H%M%S)"
if [ -d "$ROOT/字幕工厂.app" ] \
  || find "$ROOT" -maxdepth 1 -type f \( -name '字幕工厂*.dmg' -o -name '字幕工厂*.dmg.sha256' \) -print -quit | grep -q .; then
  mkdir -p "$ARCHIVE_DIR"
  if [ -d "$ROOT/字幕工厂.app" ]; then
    mv "$ROOT/字幕工厂.app" "$ARCHIVE_DIR/"
  fi
  while IFS= read -r old_release; do
    mv "$old_release" "$ARCHIVE_DIR/"
  done < <(find "$ROOT" -maxdepth 1 -type f \( -name '字幕工厂*.dmg' -o -name '字幕工厂*.dmg.sha256' \) -print)
fi

FINAL_APP="$ROOT/字幕工厂.app"
FINAL_DMG="$ROOT/字幕工厂_${VERSION}_aarch64.dmg"
FINAL_SHA="$FINAL_DMG.sha256"
cp -R "$APP_PATH" "$FINAL_APP"
cp "$DMG_PATH" "$FINAL_DMG"
(
  cd "$ROOT"
  shasum -a 256 "$(basename "$FINAL_DMG")" > "$(basename "$FINAL_SHA")"
)

codesign --verify --deep --strict "$FINAL_APP"
if ! strings "$FINAL_APP/Contents/MacOS/app" | rg -F "$UI_MARKER" >/dev/null; then
  echo "根目录最终 App 缺少 professional-v2 标记。" >&2
  exit 1
fi
if ! strings "$FINAL_APP/Contents/MacOS/app" | rg -F "$UI_LAYOUT_MARKER" >/dev/null; then
  echo "根目录最终 App 缺少 library-workspace-v2 标记。" >&2
  exit 1
fi

echo "Release App: $FINAL_APP"
echo "Release DMG: $FINAL_DMG"
echo "SHA-256: $FINAL_SHA"
