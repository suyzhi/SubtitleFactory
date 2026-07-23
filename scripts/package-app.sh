#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "v1.0.0 Release 只能在 Apple Silicon macOS 上构建。" >&2
  exit 1
fi

"$ROOT/scripts/verify-release-runtime.sh"
"$ROOT/backend/.venv/bin/python" "$ROOT/scripts/check-versions.py"
"$ROOT/backend/.venv/bin/python" "$ROOT/scripts/generate-sbom.py"
"$ROOT/scripts/build-sidecar.sh"
"$ROOT/backend/.venv/bin/python" -m pytest -q "$ROOT/backend/tests"
cd "$ROOT/frontend"
npm run lint
npm test
npm run build
cargo check --locked --manifest-path src-tauri/Cargo.toml
npx tauri build --target aarch64-apple-darwin --bundles app,dmg

BUNDLE_DIR="$ROOT/frontend/src-tauri/target/aarch64-apple-darwin/release/bundle"
APP_PATH="$BUNDLE_DIR/macos/字幕工厂.app"
DMG_PATH="$BUNDLE_DIR/dmg/字幕工厂_1.0.0_aarch64.dmg"

if [ ! -d "$APP_PATH" ]; then
  echo "Tauri 构建未生成预期的 App。" >&2
  exit 1
fi

PACKAGED_RUNTIME="$APP_PATH/Contents/Resources/backend-runtime/bin"
"$ROOT/scripts/verify-release-runtime.sh" "$PACKAGED_RUNTIME"
if [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
  codesign --force --deep --options runtime --timestamp --sign "$APPLE_SIGNING_IDENTITY" "$APP_PATH"
else
  # Rust produces a linker-signed executable, but that signature does not seal
  # the resources added by Tauri. Apply an ad-hoc bundle signature so local
  # development packages can still be verified and launched without a
  # Developer ID. Public releases continue to use the identity above.
  codesign --force --deep --options runtime --sign - "$APP_PATH"
fi
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
if [ -n "${APPLE_NOTARY_PROFILE:-}" ]; then
  if [ -z "${APPLE_SIGNING_IDENTITY:-}" ]; then
    echo "公证需要同时配置 APPLE_SIGNING_IDENTITY。" >&2
    exit 1
  fi
  APP_ZIP="$BUNDLE_DIR/字幕工厂-notarization.zip"
  ditto -c -k --keepParent "$APP_PATH" "$APP_ZIP"
  xcrun notarytool submit "$APP_ZIP" --keychain-profile "$APPLE_NOTARY_PROFILE" --wait
  xcrun stapler staple "$APP_PATH"
fi

DMG_STAGE="$BUNDLE_DIR/dmg-stage"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE" "$(dirname "$DMG_PATH")"
ditto "$APP_PATH" "$DMG_STAGE/字幕工厂.app"
ln -s /Applications "$DMG_STAGE/Applications"
rm -f "$DMG_PATH"
hdiutil create -volname "字幕工厂" -srcfolder "$DMG_STAGE" -ov -format UDZO "$DMG_PATH"
rm -rf "$DMG_STAGE"

if [ -n "${APPLE_NOTARY_PROFILE:-}" ]; then
  xcrun notarytool submit "$DMG_PATH" --keychain-profile "$APPLE_NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG_PATH"
  spctl --assess --type execute --verbose=2 "$APP_PATH"
  xcrun stapler validate "$APP_PATH"
  xcrun stapler validate "$DMG_PATH"
fi
(
  cd "$(dirname "$DMG_PATH")"
  shasum -a 256 "$(basename "$DMG_PATH")" > "$(basename "$DMG_PATH").sha256"
)

echo "Release App: $APP_PATH"
echo "Release DMG: $DMG_PATH"
echo "SHA-256: $DMG_PATH.sha256"
