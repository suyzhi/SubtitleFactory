#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${SUBTITLE_FACTORY_SILERO_VAD_VENDOR_DIR:-$ROOT/vendor/silero-vad}"
TARGET="$DEST/silero_vad.onnx"
EXPECTED_BYTES="643854"
EXPECTED_SHA256="9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"

verify_file() {
  local file="$1"
  [ -f "$file" ] || return 1
  local size
  size="$(stat -f %z "$file" 2>/dev/null || echo 0)"
  [ "$size" = "$EXPECTED_BYTES" ] || return 1
  local sha
  sha="$(shasum -a 256 "$file" | awk '{print $1}')"
  [ "$sha" = "$EXPECTED_SHA256" ]
}

if verify_file "$TARGET"; then
  echo "Silero VAD 运行时资源已存在：$TARGET"
  exit 0
fi

mkdir -p "$DEST"
LOCAL_CACHE="$ROOT/backend/data/models/silero_vad.onnx"
if verify_file "$LOCAL_CACHE"; then
  cp "$LOCAL_CACHE" "$TARGET.part"
  mv "$TARGET.part" "$TARGET"
else
  echo "下载官方 Silero VAD ($EXPECTED_BYTES bytes)..."
  curl --fail --location --retry 3 --output "$TARGET.part" "$URL"
  if ! verify_file "$TARGET.part"; then
    rm -f "$TARGET.part"
    echo "Silero VAD 校验失败（期望 SHA-256: $EXPECTED_SHA256）。" >&2
    exit 1
  fi
  mv "$TARGET.part" "$TARGET"
fi

echo "Silero VAD 运行时资源已就绪：$TARGET"
