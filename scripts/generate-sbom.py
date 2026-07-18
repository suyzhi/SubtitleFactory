#!/usr/bin/env python3
"""Generate a compact CycloneDX-compatible component inventory and license list."""

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts"
OUTPUT.mkdir(exist_ok=True)
components = []

lock = (ROOT / "backend/requirements-release.lock").read_text()
for name, version in re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", lock, re.MULTILINE):
    components.append({"type": "library", "name": name, "version": version, "purl": f"pkg:pypi/{name}@{version}"})

npm = json.loads((ROOT / "frontend/package-lock.json").read_text())
for path, item in npm.get("packages", {}).items():
    if not path.startswith("node_modules/") or not item.get("version"): continue
    name = path.removeprefix("node_modules/")
    component = {"type": "library", "name": name, "version": item["version"], "purl": f"pkg:npm/{name}@{item['version']}"}
    if item.get("license"): component["licenses"] = [{"license": {"id": item["license"]}}]
    components.append(component)

try:
    cargo = json.loads(subprocess.check_output(
        ["cargo", "metadata", "--format-version", "1", "--locked"],
        cwd=ROOT / "frontend/src-tauri", text=True,
    ))
    for item in cargo.get("packages", []):
        component = {"type": "library", "name": item["name"], "version": item["version"], "purl": f"pkg:cargo/{item['name']}@{item['version']}"}
        if item.get("license"): component["licenses"] = [{"license": {"expression": item["license"]}}]
        components.append(component)
except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
    pass

unique = {(item["purl"]): item for item in components}
components = sorted(unique.values(), key=lambda item: item["purl"])
sbom = {
    "bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
    "metadata": {"component": {"type": "application", "name": "subtitle-factory", "version": "1.0.0"}},
    "components": components,
}
(OUTPUT / "sbom.cdx.json").write_text(json.dumps(sbom, ensure_ascii=False, indent=2) + "\n")
lines = ["# Third-party dependency licenses", ""]
for item in components:
    licenses = item.get("licenses") or []
    label = ", ".join((entry["license"].get("id") or entry["license"].get("expression") or "Unknown") for entry in licenses) or "See upstream package metadata"
    lines.append(f"- `{item['purl']}` — {label}")
(OUTPUT / "THIRD_PARTY_LICENSES.md").write_text("\n".join(lines) + "\n")
print(f"Generated {len(components)} components in {OUTPUT}")
