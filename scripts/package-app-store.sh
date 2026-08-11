#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="0.4.1"
BUNDLE_ID="com.subtitlefactory.desktop"
TAURI_DIR="$ROOT/frontend/src-tauri"
GENERATED_ENTITLEMENTS="$TAURI_DIR/Entitlements.appstore.plist"
GENERATED_HELPER_ENTITLEMENTS="$TAURI_DIR/HelperEntitlements.appstore.plist"
EMBEDDED_PROFILE="$TAURI_DIR/embedded.provisionprofile"
PROFILE_PLIST=""

cleanup_generated() {
  for generated_file in \
    "$GENERATED_ENTITLEMENTS" \
    "$GENERATED_HELPER_ENTITLEMENTS" \
    "$EMBEDDED_PROFILE" \
    "$PROFILE_PLIST"; do
    if [ -n "$generated_file" ] && [ -e "$generated_file" ]; then
      find "$generated_file" -depth -delete
    fi
  done
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
trap cleanup_generated EXIT

require_value() {
  local name="$1"
  local value="${!name:-}"
  if [ -z "$value" ]; then
    echo "缺少 App Store 构建参数：$name" >&2
    exit 1
  fi
}

if ! xcodebuild -version >/dev/null 2>&1; then
  echo "Mac App Store 构建需要完整 Xcode；当前 xcode-select 不能运行 xcodebuild。" >&2
  exit 1
fi
for variable in \
  APPLE_TEAM_ID \
  APPLE_APP_SIGNING_IDENTITY \
  APPLE_INSTALLER_SIGNING_IDENTITY \
  APPLE_PROVISIONING_PROFILE; do
  require_value "$variable"
done
if ! [[ "$APPLE_TEAM_ID" =~ ^[A-Z0-9]{10}$ ]]; then
  echo "APPLE_TEAM_ID 格式无效。" >&2
  exit 1
fi
if [ ! -f "$APPLE_PROVISIONING_PROFILE" ]; then
  echo "Mac App Store Connect provisioning profile 不存在。" >&2
  exit 1
fi
if ! security find-identity -v -p codesigning \
  | grep -F -- "\"$APPLE_APP_SIGNING_IDENTITY\"" >/dev/null; then
  echo "钥匙串中没有指定的 Mac App Distribution 签名身份。" >&2
  exit 1
fi
if ! security find-identity -v \
  | grep -F -- "\"$APPLE_INSTALLER_SIGNING_IDENTITY\"" >/dev/null; then
  echo "钥匙串中没有指定的 Mac Installer Distribution 签名身份。" >&2
  exit 1
fi

PROFILE_PLIST="$(mktemp /tmp/subtitle-factory-profile.XXXXXX.plist)"
if ! security cms -D -i "$APPLE_PROVISIONING_PROFILE" > "$PROFILE_PLIST"; then
  echo "无法解析 provisioning profile。" >&2
  exit 1
fi
PROFILE_APP_ID="$(/usr/libexec/PlistBuddy -c 'Print :Entitlements:com.apple.application-identifier' "$PROFILE_PLIST" 2>/dev/null || true)"
if [ "$PROFILE_APP_ID" != "$APPLE_TEAM_ID.$BUNDLE_ID" ]; then
  echo "provisioning profile 与 Team ID / Bundle ID 不匹配。" >&2
  exit 1
fi

sed -e "s/__TEAM_ID__/$APPLE_TEAM_ID/g" -e "s/__BUNDLE_ID__/$BUNDLE_ID/g" \
  "$TAURI_DIR/Entitlements.appstore.plist.template" > "$GENERATED_ENTITLEMENTS"
cp "$TAURI_DIR/HelperEntitlements.appstore.plist.template" "$GENERATED_HELPER_ENTITLEMENTS"
cp "$APPLE_PROVISIONING_PROFILE" "$EMBEDDED_PROFILE"
plutil -lint "$GENERATED_ENTITLEMENTS" "$GENERATED_HELPER_ENTITLEMENTS" \
  "$TAURI_DIR/Info.plist" "$TAURI_DIR/PrivacyInfo.xcprivacy"

export SUBTITLE_FACTORY_DISTRIBUTION_CHANNEL="app_store"
export VITE_DISTRIBUTION_CHANNEL="app_store"

"$ROOT/backend/.venv/bin/python" "$ROOT/scripts/verify-model-sources.py"
"$ROOT/backend/.venv/bin/python" -m pytest -q "$ROOT/backend/tests"

cd "$ROOT/frontend"
npm run test
npm run lint

"$ROOT/scripts/verify-release-runtime.sh"
"$ROOT/scripts/build-sidecar.sh"
npm run build

if ! rg -F "subtitle-factory-ui:professional-v2" "$ROOT/frontend/dist" >/dev/null \
  || ! rg -F "subtitle-factory-ui:library-workspace-v2" "$ROOT/frontend/dist" >/dev/null; then
  echo "App Store Vite 产物缺少新版 UI 标记。" >&2
  exit 1
fi
if [ -e "$TAURI_DIR/backend-runtime/bin/deno" ]; then
  echo "App Store sidecar 错误地包含 Deno。" >&2
  exit 1
fi
if find "$TAURI_DIR/backend-runtime" -iname '*yt_dlp*' -print -quit | grep -q .; then
  echo "App Store sidecar 错误地包含 yt-dlp。" >&2
  exit 1
fi

npx tauri build --target aarch64-apple-darwin --bundles app \
  --config src-tauri/tauri.appstore.conf.json

APP_PATH="$TAURI_DIR/target/aarch64-apple-darwin/release/bundle/macos/字幕工厂.app"
RUNTIME_PATH="$APP_PATH/Contents/Resources/backend-runtime"
if [ ! -d "$APP_PATH" ] || [ ! -x "$RUNTIME_PATH/subtitle-backend" ]; then
  echo "Tauri 未生成完整的 App Store App bundle。" >&2
  exit 1
fi
if [ ! -f "$APP_PATH/Contents/Resources/PrivacyInfo.xcprivacy" ]; then
  echo "最终 App 缺少 PrivacyInfo.xcprivacy。" >&2
  exit 1
fi

# Sign nested libraries first, then launchable helpers with sandbox inheritance,
# and finally the containing App with its provisioning profile and capabilities.
while IFS= read -r -d '' candidate; do
  if file "$candidate" | grep -q 'Mach-O'; then
    codesign --force --timestamp --options runtime \
      --sign "$APPLE_APP_SIGNING_IDENTITY" "$candidate"
  fi
done < <(find "$RUNTIME_PATH" -type f -print0)

for helper in \
  "$RUNTIME_PATH/subtitle-backend" \
  "$RUNTIME_PATH/bin/ffmpeg" \
  "$RUNTIME_PATH/bin/ffprobe" \
  "$RUNTIME_PATH/bin/vision-ocr"; do
  if [ ! -x "$helper" ]; then
    echo "最终 App 缺少 helper：$(basename "$helper")" >&2
    exit 1
  fi
  codesign --force --timestamp --options runtime \
    --entitlements "$GENERATED_HELPER_ENTITLEMENTS" \
    --sign "$APPLE_APP_SIGNING_IDENTITY" "$helper"
done

codesign --force --timestamp --options runtime \
  --entitlements "$GENERATED_ENTITLEMENTS" \
  --sign "$APPLE_APP_SIGNING_IDENTITY" "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

verify_hardened_runtime() {
  local signed_target="$1"
  if ! codesign -dvvv "$signed_target" 2>&1 \
    | grep -E 'flags=.*\([^)]*runtime[^)]*\)' >/dev/null; then
    echo "签名未启用 Hardened Runtime：$signed_target" >&2
    exit 1
  fi
}
verify_hardened_runtime "$APP_PATH"
for helper in \
  "$RUNTIME_PATH/subtitle-backend" \
  "$RUNTIME_PATH/bin/ffmpeg" \
  "$RUNTIME_PATH/bin/ffprobe" \
  "$RUNTIME_PATH/bin/vision-ocr"; do
  verify_hardened_runtime "$helper"
done

APP_ENTITLEMENTS="$(codesign -d --entitlements - "$APP_PATH" 2>/dev/null)"
if ! grep -F 'com.apple.security.app-sandbox' <<< "$APP_ENTITLEMENTS" >/dev/null; then
  echo "最终 App 未启用 App Sandbox。" >&2
  exit 1
fi
HELPER_ENTITLEMENTS="$(codesign -d --entitlements - "$RUNTIME_PATH/subtitle-backend" 2>/dev/null)"
if ! grep -F 'com.apple.security.inherit' <<< "$HELPER_ENTITLEMENTS" >/dev/null; then
  echo "最终 sidecar 未继承 App Sandbox。" >&2
  exit 1
fi

PKG_PATH="$ROOT/字幕工厂_${VERSION}_AppStore.pkg"
xcrun productbuild --sign "$APPLE_INSTALLER_SIGNING_IDENTITY" \
  --component "$APP_PATH" /Applications "$PKG_PATH"
pkgutil --check-signature "$PKG_PATH"

FINAL_APP="$ROOT/字幕工厂-AppStore.app"
if [ -e "$FINAL_APP" ]; then
  ARCHIVE_DIR="$ROOT/release-archive/app-store-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$ARCHIVE_DIR"
  mv "$FINAL_APP" "$ARCHIVE_DIR/"
fi
cp -R "$APP_PATH" "$FINAL_APP"
codesign --verify --deep --strict "$FINAL_APP"

echo "App Store App: $FINAL_APP"
echo "App Store PKG: $PKG_PATH"
