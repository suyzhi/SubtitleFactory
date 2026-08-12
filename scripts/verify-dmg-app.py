#!/usr/bin/env python3
"""Mount a release DMG and prove its App matches the delivered App byte-for-byte."""

from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_VERIFIER = ROOT / "scripts/verify-macos-deployment-target.sh"
UI_MARKERS = (
    b"subtitle-factory-ui:professional-v2",
    b"subtitle-factory-ui:library-workspace-v2",
)
OLD_UI_MARKER = b"ai-settings-dialog"


@dataclass(frozen=True)
class Entry:
    kind: str
    mode: int
    size: int = 0
    digest_or_target: str = ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> dict[str, Entry]:
    manifest: dict[str, Entry] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISLNK(metadata.st_mode):
                manifest[relative] = Entry("symlink", mode, digest_or_target=os.readlink(path))
            elif stat.S_ISDIR(metadata.st_mode):
                manifest[relative] = Entry("directory", mode)
                pending.append(path)
            elif stat.S_ISREG(metadata.st_mode):
                manifest[relative] = Entry("file", mode, metadata.st_size, sha256(path))
            else:
                manifest[relative] = Entry("unsupported", mode)
    return manifest


def run(*arguments: str | Path, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(argument) for argument in arguments],
        check=True,
        text=True,
        capture_output=capture,
    )


def attach_read_only(dmg_path: Path) -> Path:
    result = subprocess.run(
        ["hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(dmg_path)],
        check=True,
        capture_output=True,
    )
    data = plistlib.loads(result.stdout)
    mount_points = [
        Path(item["mount-point"])
        for item in data.get("system-entities", [])
        if "mount-point" in item
    ]
    if len(mount_points) != 1 or not str(mount_points[0]).startswith("/Volumes/"):
        raise RuntimeError(f"DMG 挂载点无效：{mount_points}")
    return mount_points[0]


def verify_bundle(app_path: Path, minimum_macos_version: str) -> None:
    run("codesign", "--verify", "--deep", "--strict", app_path)
    run(DEPLOYMENT_VERIFIER, app_path, minimum_macos_version)
    with (app_path / "Contents/Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    if info.get("LSMinimumSystemVersion") != minimum_macos_version:
        raise RuntimeError("DMG 内 App 的 LSMinimumSystemVersion 不正确")
    executable = app_path / "Contents/MacOS/app"
    file_description = run("file", executable, capture=True).stdout
    if "arm64" not in file_description:
        raise RuntimeError("DMG 内主程序不是 arm64")
    executable_bytes = executable.read_bytes()
    missing = [marker.decode() for marker in UI_MARKERS if marker not in executable_bytes]
    if missing:
        raise RuntimeError("DMG 内 App 缺少新版 UI 标记：" + ", ".join(missing))
    if OLD_UI_MARKER in executable_bytes:
        raise RuntimeError("DMG 内 App 包含旧 UI 标记")


def verify_dmg(dmg_path: Path, expected_app: Path, minimum_macos_version: str) -> None:
    run("hdiutil", "verify", dmg_path)
    mount_path: Path | None = None
    try:
        mount_path = attach_read_only(dmg_path)
        mounted_app = mount_path / expected_app.name
        if not mounted_app.is_dir() or mounted_app.is_symlink():
            raise RuntimeError(f"DMG 中缺少 {expected_app.name}")
        applications_link = mount_path / "Applications"
        if not applications_link.is_symlink() or os.readlink(applications_link) != "/Applications":
            raise RuntimeError("DMG 缺少指向 /Applications 的安装快捷方式")
        if not (mount_path / ".DS_Store").is_file() or not (mount_path / ".VolumeIcon.icns").is_file():
            raise RuntimeError("DMG 缺少 Finder 布局或卷图标")

        expected_manifest = build_manifest(expected_app)
        mounted_manifest = build_manifest(mounted_app)
        if expected_manifest != mounted_manifest:
            all_paths = sorted(set(expected_manifest) | set(mounted_manifest))
            differences = [
                path
                for path in all_paths
                if expected_manifest.get(path) != mounted_manifest.get(path)
            ]
            preview = ", ".join(differences[:10])
            raise RuntimeError(f"DMG 内外 App 不一致（{len(differences)} 项）：{preview}")

        verify_bundle(mounted_app, minimum_macos_version)
        file_count = sum(entry.kind == "file" for entry in mounted_manifest.values())
        link_count = sum(entry.kind == "symlink" for entry in mounted_manifest.values())
        byte_count = sum(entry.size for entry in mounted_manifest.values() if entry.kind == "file")
        print(
            "DMG 内外 App 逐文件一致："
            f"{file_count} 个文件、{link_count} 个链接、{byte_count} bytes。"
        )
    finally:
        if mount_path is not None and mount_path.exists():
            run("hdiutil", "detach", mount_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dmg", type=Path)
    parser.add_argument("app", type=Path)
    parser.add_argument("--minimum-macos-version", default="14.0")
    arguments = parser.parse_args()
    verify_dmg(
        arguments.dmg.resolve(),
        arguments.app.resolve(),
        arguments.minimum_macos_version,
    )


if __name__ == "__main__":
    main()
