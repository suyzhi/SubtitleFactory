#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "用法：$0 <PyInstaller 运行时> <App 内运行时>" >&2
  exit 2
fi

SOURCE_RUNTIME="$1"
PACKAGED_RUNTIME="$2"
if [ ! -d "$SOURCE_RUNTIME" ] || [ ! -d "$PACKAGED_RUNTIME" ]; then
  echo "运行时目录不存在，无法恢复 PyInstaller 符号链接。" >&2
  exit 1
fi

SOURCE_RUNTIME="$(cd "$SOURCE_RUNTIME" && pwd -P)"
PACKAGED_RUNTIME="$(cd "$PACKAGED_RUNTIME" && pwd -P)"
RESTORED_LINKS=0
RESTORED_BYTES=0
VERIFIED_LINKS=0

while IFS= read -r -d '' source_link; do
  relative_path="${source_link#"$SOURCE_RUNTIME"/}"
  link_target="$(readlink "$source_link")"
  if [ -z "$link_target" ] || [[ "$link_target" = /* ]]; then
    echo "拒绝恢复空目标或绝对目标的符号链接：$relative_path" >&2
    exit 1
  fi

  packaged_link="$PACKAGED_RUNTIME/$relative_path"
  packaged_target="$(dirname "$packaged_link")/$link_target"
  if [ ! -e "$packaged_target" ]; then
    echo "符号链接目标未进入 App：$relative_path -> $link_target" >&2
    exit 1
  fi
  resolved_target="$(realpath "$packaged_target")"
  case "$resolved_target" in
    "$PACKAGED_RUNTIME"/*) ;;
    *)
      echo "符号链接目标逃逸 App 运行时：$relative_path -> $link_target" >&2
      exit 1
      ;;
  esac

  if [ -L "$packaged_link" ]; then
    if [ "$(readlink "$packaged_link")" != "$link_target" ]; then
      echo "App 内符号链接目标不一致：$relative_path" >&2
      exit 1
    fi
    VERIFIED_LINKS=$((VERIFIED_LINKS + 1))
    continue
  fi
  if [ ! -f "$packaged_link" ] || ! cmp -s "$packaged_link" "$packaged_target"; then
    echo "App 内文件不能安全替换为符号链接：$relative_path" >&2
    exit 1
  fi

  duplicate_bytes="$(stat -f %z "$packaged_link")"
  find "$packaged_link" -delete
  ln -s "$link_target" "$packaged_link"
  RESTORED_LINKS=$((RESTORED_LINKS + 1))
  RESTORED_BYTES=$((RESTORED_BYTES + duplicate_bytes))
done < <(find "$SOURCE_RUNTIME" -type l -print0)

while IFS= read -r -d '' packaged_link; do
  if [ ! -e "$packaged_link" ]; then
    echo "App 内存在断开的运行时符号链接：$packaged_link" >&2
    exit 1
  fi
done < <(find "$PACKAGED_RUNTIME" -type l -print0)

echo "运行时链接拓扑通过：恢复 ${RESTORED_LINKS} 个链接，避免复制 ${RESTORED_BYTES} bytes；已验证 ${VERIFIED_LINKS} 个原生链接。"
