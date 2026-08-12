#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-}"
MAXIMUM_MINOS="${2:-}"

if [ -z "$TARGET" ] || [ ! -e "$TARGET" ] || [ -z "$MAXIMUM_MINOS" ]; then
  echo "用法：$0 <App 或运行时路径> <声明的最低 macOS 版本>" >&2
  exit 2
fi
if ! [[ "$MAXIMUM_MINOS" =~ ^[0-9]+([.][0-9]+){0,2}$ ]]; then
  echo "最低 macOS 版本格式无效：$MAXIMUM_MINOS" >&2
  exit 2
fi
if ! command -v vtool >/dev/null 2>&1; then
  echo "缺少 vtool，无法核对 Mach-O 部署目标。" >&2
  exit 1
fi

version_is_at_most() {
  local actual="$1"
  local maximum="$2"
  awk -v actual="$actual" -v maximum="$maximum" 'BEGIN {
    split(actual, left, ".")
    split(maximum, right, ".")
    for (part = 1; part <= 4; part++) {
      left_part = left[part] + 0
      right_part = right[part] + 0
      if (left_part < right_part) exit 0
      if (left_part > right_part) exit 1
    }
    exit 0
  }'
}

mach_count=0
failure_count=0
while IFS= read -r -d '' candidate; do
  if ! file "$candidate" | grep -q 'Mach-O'; then
    continue
  fi
  mach_count=$((mach_count + 1))
  build_info="$(vtool -show-build "$candidate" 2>/dev/null || true)"
  platforms="$(awk '/^[[:space:]]*platform / {print $2}' <<< "$build_info")"
  versions="$(awk '/^[[:space:]]*minos / {print $2}' <<< "$build_info")"
  relative_path="${candidate#$TARGET/}"
  if [ -z "$platforms" ] || [ -z "$versions" ]; then
    echo "无法读取 Mach-O 部署目标：$relative_path" >&2
    failure_count=$((failure_count + 1))
    continue
  fi
  while IFS= read -r platform; do
    if [ "$platform" != "MACOS" ]; then
      echo "包内出现非 macOS Mach-O（${platform}）：${relative_path}" >&2
      failure_count=$((failure_count + 1))
    fi
  done <<< "$platforms"
  while IFS= read -r minos; do
    if ! version_is_at_most "$minos" "$MAXIMUM_MINOS"; then
      echo "Mach-O 要求 macOS ${minos}，高于 App 声明的 ${MAXIMUM_MINOS}：${relative_path}" >&2
      failure_count=$((failure_count + 1))
    fi
  done <<< "$versions"
done < <(find "$TARGET" -type f -print0)

if [ "$mach_count" -eq 0 ]; then
  echo "目标中没有可验证的 Mach-O：$TARGET" >&2
  exit 1
fi
if [ "$failure_count" -ne 0 ]; then
  echo "macOS 部署目标检查失败：$failure_count 个不兼容项。" >&2
  exit 1
fi
echo "macOS 部署目标检查通过：${mach_count} 个 Mach-O 均兼容 macOS ${MAXIMUM_MINOS}。"
