#!/usr/bin/env bash
set -euo pipefail

DMG_PATH="${1:-}"
APP_PATH="${2:-}"

if [ -z "$DMG_PATH" ] || [ ! -f "$DMG_PATH" ] \
  || [ -z "$APP_PATH" ] || [ ! -d "$APP_PATH" ]; then
  echo "用法：$0 <Tauri 生成的 DMG> <已优化并签名的 App>" >&2
  exit 2
fi
if [[ "$DMG_PATH" != *.dmg ]] || [[ "$APP_PATH" != *.app ]]; then
  echo "输入必须分别是 .dmg 文件和 .app 目录。" >&2
  exit 2
fi
if ! command -v hdiutil >/dev/null 2>&1; then
  echo "缺少 hdiutil，无法重新封装 DMG。" >&2
  exit 1
fi

DMG_PATH="$(cd "$(dirname "$DMG_PATH")" && pwd)/$(basename "$DMG_PATH")"
APP_PATH="$(cd "$(dirname "$APP_PATH")" && pwd)/$(basename "$APP_PATH")"
APP_NAME="$(basename "$APP_PATH")"
DMG_DIR="$(dirname "$DMG_PATH")"
TEMP_DIR="$(mktemp -d "$DMG_DIR/.subtitle-factory-dmg-repack.XXXXXX")"
RW_IMAGE="$TEMP_DIR/source-rw.dmg"
OPTIMIZED_IMAGE="$TEMP_DIR/optimized.dmg"
MOUNT_PATH=""

cleanup() {
  if [ -n "$MOUNT_PATH" ] && [ -d "$MOUNT_PATH" ]; then
    hdiutil detach "$MOUNT_PATH" >/dev/null 2>&1 || true
  fi
  if [ -d "$TEMP_DIR" ]; then
    find "$TEMP_DIR" -depth -delete
  fi
}
trap cleanup EXIT

hdiutil verify "$DMG_PATH" >/dev/null
hdiutil convert "$DMG_PATH" -format UDRW -o "$RW_IMAGE" >/dev/null
ATTACH_PLIST="$(hdiutil attach -readwrite -nobrowse -owners on -plist "$RW_IMAGE")"
MOUNT_PATH="$(
  printf '%s' "$ATTACH_PLIST" \
    | /usr/bin/python3 -c \
      'import plistlib, sys; data = plistlib.loads(sys.stdin.buffer.read()); print(next(item["mount-point"] for item in data["system-entities"] if "mount-point" in item))'
)"
if [[ "$MOUNT_PATH" != /Volumes/* ]] || [ ! -d "$MOUNT_PATH" ]; then
  echo "DMG 挂载点不安全或不存在：$MOUNT_PATH" >&2
  exit 1
fi
MOUNTED_APP="$MOUNT_PATH/$APP_NAME"
if [ ! -d "$MOUNTED_APP" ] || [ -L "$MOUNTED_APP" ]; then
  echo "DMG 中缺少预期的 App：$APP_NAME" >&2
  exit 1
fi
if [ ! -L "$MOUNT_PATH/Applications" ] \
  || [ "$(readlink "$MOUNT_PATH/Applications")" != "/Applications" ]; then
  echo "DMG 缺少指向 /Applications 的安装快捷方式。" >&2
  exit 1
fi
if [ ! -f "$MOUNT_PATH/.DS_Store" ] || [ ! -f "$MOUNT_PATH/.VolumeIcon.icns" ]; then
  echo "DMG 缺少 Tauri 生成的 Finder 布局或卷图标。" >&2
  exit 1
fi

# The source image is a disposable Tauri build product. Replace only its
# identically named App while preserving Finder layout and volume metadata.
find "$MOUNTED_APP" -depth -delete
ditto "$APP_PATH" "$MOUNTED_APP"
chmod -Rf go-w "$MOUNTED_APP"

source_links="$(find "$APP_PATH" -type l | wc -l | tr -d ' ')"
mounted_links="$(find "$MOUNTED_APP" -type l | wc -l | tr -d ' ')"
if [ "$source_links" != "$mounted_links" ]; then
  echo "DMG 内 App 的链接数量与优化 App 不一致。" >&2
  exit 1
fi
while IFS= read -r -d '' source_link; do
  relative_path="${source_link#$APP_PATH/}"
  mounted_link="$MOUNTED_APP/$relative_path"
  if [ ! -L "$mounted_link" ] \
    || [ "$(readlink "$source_link")" != "$(readlink "$mounted_link")" ]; then
    echo "DMG 没有保留 App 链接：$relative_path" >&2
    exit 1
  fi
done < <(find "$APP_PATH" -type l -print0)
codesign --verify --deep --strict "$MOUNTED_APP"

sync
hdiutil detach "$MOUNT_PATH" >/dev/null
MOUNT_PATH=""
hdiutil resize -size min "$RW_IMAGE" >/dev/null
hdiutil convert "$RW_IMAGE" -format UDZO -imagekey zlib-level=9 \
  -o "$OPTIMIZED_IMAGE" >/dev/null
hdiutil internet-enable -yes "$OPTIMIZED_IMAGE" >/dev/null 2>&1 || true
hdiutil verify "$OPTIMIZED_IMAGE" >/dev/null
mv "$OPTIMIZED_IMAGE" "$DMG_PATH"

echo "DMG 已改为承载优化 App：${source_links} 个链接保持不变。"
