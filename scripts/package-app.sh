#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/scripts/build-sidecar.sh"
cd "$ROOT/frontend"
npm run build
npx tauri build
