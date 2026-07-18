#!/usr/bin/env python3
"""Fail CI when frontend, backend, Cargo, and Tauri versions diverge."""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
versions = {
    "backend": re.search(r'VERSION\s*=\s*"([^"]+)"', (ROOT / "backend/app/version.py").read_text()).group(1),
    "frontend": json.loads((ROOT / "frontend/package.json").read_text())["version"],
    "tauri": json.loads((ROOT / "frontend/src-tauri/tauri.conf.json").read_text())["version"],
    "cargo": re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "frontend/src-tauri/Cargo.toml").read_text(), re.MULTILINE).group(1),
}
if len(set(versions.values())) != 1:
    raise SystemExit("版本号不一致: " + ", ".join(f"{name}={value}" for name, value in versions.items()))
print(f"版本号已同步: {next(iter(versions.values()))}")
