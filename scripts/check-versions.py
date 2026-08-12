#!/usr/bin/env python3
"""Fail CI when frontend, backend, Cargo, and Tauri versions diverge."""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
tauri_config = json.loads((ROOT / "frontend/src-tauri/tauri.conf.json").read_text())
app_store_metadata = json.loads((ROOT / "app-store/metadata.zh-Hans.json").read_text())
versions = {
    "backend": re.search(r'VERSION\s*=\s*"([^"]+)"', (ROOT / "backend/app/version.py").read_text()).group(1),
    "frontend": json.loads((ROOT / "frontend/package.json").read_text())["version"],
    "frontend_lock": json.loads((ROOT / "frontend/package-lock.json").read_text())["version"],
    "tauri": tauri_config["version"],
    "cargo": re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "frontend/src-tauri/Cargo.toml").read_text(), re.MULTILINE).group(1),
    "package_script": re.search(
        r'^VERSION="([^"]+)"',
        (ROOT / "scripts/package-app.sh").read_text(),
        re.MULTILINE,
    ).group(1),
    "app_store_package_script": re.search(
        r'^VERSION="([^"]+)"',
        (ROOT / "scripts/package-app-store.sh").read_text(),
        re.MULTILINE,
    ).group(1),
    "readme": re.search(
        r'^# 字幕工厂 ([^\s]+)',
        (ROOT / "README.md").read_text(),
        re.MULTILINE,
    ).group(1),
    "changelog": re.search(
        r'^## ([^\s]+)',
        (ROOT / "CHANGELOG.md").read_text(),
        re.MULTILINE,
    ).group(1),
    "sbom": json.loads((ROOT / "artifacts/sbom.cdx.json").read_text())[
        "metadata"
    ]["component"]["version"],
    "app_store_metadata": app_store_metadata["app"]["version"],
}
if len(set(versions.values())) != 1:
    raise SystemExit("版本号不一致: " + ", ".join(f"{name}={value}" for name, value in versions.items()))
if tauri_config.get("bundle", {}).get("category") != "Video":
    raise SystemExit("Tauri bundle.category 必须是 Video")
if app_store_metadata["app"]["bundle_id"] != tauri_config["identifier"]:
    raise SystemExit("App Store 元数据 Bundle ID 与 Tauri identifier 不一致")
minimum_macos_versions = {
    "tauri": tauri_config.get("bundle", {}).get("macOS", {}).get("minimumSystemVersion"),
    "app_store_metadata": app_store_metadata["app"].get("minimum_macos_version"),
}
if set(minimum_macos_versions.values()) != {"14.0"}:
    raise SystemExit(
        "最低 macOS 版本必须统一为 14.0: "
        + ", ".join(f"{name}={value}" for name, value in minimum_macos_versions.items())
    )
print(f"版本号已同步: {next(iter(versions.values()))}")
