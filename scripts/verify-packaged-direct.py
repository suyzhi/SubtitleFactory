#!/usr/bin/env python3
"""Exercise the direct App and its DMG copy through their real sidecars."""

from __future__ import annotations

import argparse
import importlib.util
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMON_SCRIPT = ROOT / "scripts/verify-packaged-app-store.py"
VENDOR_FFMPEG = ROOT / "vendor/ffmpeg/darwin-arm64/ffmpeg-darwin-arm64"
EXPECTED_MODEL_COUNT = 29

COMMON_SPEC = importlib.util.spec_from_file_location(
    "verify_packaged_app_common", COMMON_SCRIPT,
)
if COMMON_SPEC is None or COMMON_SPEC.loader is None:
    raise RuntimeError(f"无法加载 packaged App 验收公共逻辑：{COMMON_SCRIPT}")
common = importlib.util.module_from_spec(COMMON_SPEC)
sys.modules[COMMON_SPEC.name] = common
COMMON_SPEC.loader.exec_module(common)


def isolated_environment(data_directory: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SUBTITLE_FACTORY_")
    }
    environment["SUBTITLE_FACTORY_DATA_DIR"] = str(data_directory)
    return environment


def verify_direct_api(
    session: common.BackendSession,
    *,
    expected_version: str,
    data_directory: Path,
    sample: Path,
) -> None:
    common.json_response(session, "/api/health", expected=401, authorized=False)
    health = common.json_response(session, "/api/health", expected=200)
    capabilities = health.get("distribution") or {}
    runtime = health.get("runtime") or {}
    common.require(health.get("version") == expected_version, "直装 sidecar 与 App 版本不一致")
    common.require(capabilities.get("channel") == "direct", "直装 App 没有运行在 direct 通道")
    for key in (
        "youtube",
        "browser_cookies",
        "custom_download_directory",
        "filesystem_automation",
        "external_runtime_paths",
    ):
        common.require(capabilities.get(key) is True, f"直装能力 {key} 没有开启")
    common.require(
        runtime.get("data_directory") == str(data_directory),
        "sidecar 没有使用验收器指定的隔离数据目录",
    )
    for key in ("ffmpeg", "ffprobe", "yt_dlp", "deno", "ejs"):
        item = runtime.get(key) or {}
        common.require(item.get("ok") is True, f"真实包内 {key} 不可用")
        common.require(item.get("status") == "ready", f"真实包内 {key} 状态不是 ready")

    catalog = common.json_response(session, "/api/transcription/models", expected=200)
    models = catalog.get("models") or []
    ids = {item.get("id") for item in models if isinstance(item, dict)}
    common.require(
        len(models) == EXPECTED_MODEL_COUNT,
        f"直装模型数为 {len(models)}，预期 {EXPECTED_MODEL_COUNT}",
    )
    common.require("parakeet-tdt-0.6b-v3-coreml" in ids, "直装模型目录缺少外部 Core ML 入口")
    common.require("custom" not in ids, "空白隔离数据目录不应凭空出现 custom 模型")

    common.json_response(session, "/api/watch-folders", expected=200)
    common.json_response(session, "/api/transcription/models/imported", expected=200)

    youtube_project: str | None = None
    local_project: str | None = None
    try:
        created_youtube = common.json_response(
            session,
            "/api/projects",
            expected=201,
            method="POST",
            payload={
                "title": "Direct packaged YouTube policy QA",
                "source_type": "youtube",
                "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            },
        )
        youtube_project = str(created_youtube.get("project_id") or "")
        common.require(bool(youtube_project), "直装 YouTube 项目没有 ID")

        created_local = common.json_response(
            session,
            "/api/projects",
            expected=201,
            method="POST",
            payload={"title": "Direct packaged import QA", "source_type": "local"},
        )
        local_project = str(created_local.get("project_id") or "")
        common.require(bool(local_project), "直装本地项目没有 ID")
        multipart, content_type = common.multipart_file(sample)
        imported = common.json_response(
            session,
            f"/api/projects/{local_project}/import-local",
            expected=200,
            method="POST",
            body=multipart,
            content_type=content_type,
        )
        video_path = Path(str(imported.get("video_path") or ""))
        project_root = data_directory / "projects" / local_project
        common.require(video_path.parent == project_root, "本地媒体没有复制到隔离项目目录")
        common.require(video_path.is_file(), "直装项目媒体文件不存在")
        common.require(
            imported.get("thumbnail_url") == f"/api/projects/{local_project}/thumbnail",
            "包内 FFmpeg 没有生成直装项目缩略图",
        )
        detail = common.json_response(
            session, f"/api/projects/{local_project}", expected=200,
        )
        common.require(detail.get("video_path") == str(video_path), "项目媒体路径与导入结果不一致")
        common.require(detail.get("source_type") == "local", "导入后项目类型不是 local")
        common.require(detail.get("media_mode") == "local", "导入后媒体模式不是 local")

        common.json_response(
            session,
            f"/api/projects/{local_project}?permanent=true",
            expected=200,
            method="DELETE",
        )
        local_project = None
        common.require(not project_root.exists(), "直装 QA 本地项目没有被精确清理")

        youtube_root = data_directory / "projects" / youtube_project
        common.json_response(
            session,
            f"/api/projects/{youtube_project}?permanent=true",
            expected=200,
            method="DELETE",
        )
        youtube_project = None
        common.require(not youtube_root.exists(), "直装 QA YouTube 项目没有被精确清理")
    finally:
        for project_id in (local_project, youtube_project):
            if project_id:
                try:
                    common.json_response(
                        session,
                        f"/api/projects/{project_id}?permanent=true",
                        expected=200,
                        method="DELETE",
                    )
                except Exception:
                    pass


def verify_app(
    app: Path,
    *,
    sample: Path,
    temporary_root: Path,
    label: str,
) -> None:
    with (app / "Contents/Info.plist").open("rb") as source:
        info = plistlib.load(source)
    bundle_id = str(info.get("CFBundleIdentifier") or "")
    expected_version = str(info.get("CFBundleShortVersionString") or "")
    executable_name = str(info.get("CFBundleExecutable") or "")
    common.require(bundle_id == "com.subtitlefactory.desktop", f"{label} Bundle ID 不正确")
    common.require(bool(expected_version), f"{label} 版本号缺失")
    executable = app / "Contents/MacOS" / executable_name
    common.require(executable.is_file(), f"{label} 主可执行文件缺失")

    data_directory = (temporary_root / "data").resolve()
    data_directory.mkdir(parents=True)
    environment = isolated_environment(data_directory)
    first_process: subprocess.Popen[bytes] | None = None
    first_session: common.BackendSession | None = None
    with (temporary_root / "desktop.log").open("wb") as log:
        try:
            first_process, first_session = common.launch(
                executable,
                log,
                environment=environment,
                timeout=60,
            )
            verify_direct_api(
                first_session,
                expected_version=expected_version,
                data_directory=data_directory,
                sample=sample,
            )
            print(
                f"{label} API 通过：401 鉴权、{EXPECTED_MODEL_COUNT} 个模型、"
                "5 项直装能力、真实 Deno/FFmpeg、本地导入与精确清理"
            )
            common.verify_lifecycle(
                executable,
                bundle_id,
                log,
                first_process,
                first_session,
                environment=environment,
                label=label,
            )
            first_process = None
            first_session = None
        finally:
            common.stop_exact_process(
                first_process,
                first_session.process.pgid if first_session else None,
            )


def attach_read_only(dmg: Path) -> Path:
    result = subprocess.run(
        ["hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(dmg)],
        check=True,
        capture_output=True,
    )
    payload = plistlib.loads(result.stdout)
    mounts = [
        Path(item["mount-point"])
        for item in payload.get("system-entities", [])
        if "mount-point" in item
    ]
    common.require(
        len(mounts) == 1 and str(mounts[0]).startswith("/Volumes/"),
        f"DMG 挂载点无效：{mounts}",
    )
    return mounts[0]


def create_sample(sample: Path) -> None:
    common.require(VENDOR_FFMPEG.is_file(), "受控 FFmpeg 测试运行时缺失")
    subprocess.run(
        [
            str(VENDOR_FFMPEG), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=320x180:d=0.5",
            "-c:v", "mpeg4", "-q:v", "5", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(sample),
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app", nargs="?", type=Path, default=ROOT / "字幕工厂.app")
    parser.add_argument("--dmg", type=Path)
    arguments = parser.parse_args()
    app = arguments.app.resolve()
    common.require(app.is_dir(), f"App 不存在：{app}")
    common.ensure_no_running_subtitle_factory()

    with tempfile.TemporaryDirectory(prefix="subtitle-factory-direct-qa-") as temporary:
        temporary_root = Path(temporary)
        sample = temporary_root / "review-sample.mp4"
        create_sample(sample)
        direct_root = temporary_root / "root-app"
        direct_root.mkdir()
        verify_app(app, sample=sample, temporary_root=direct_root, label="直装 App QA")

        if arguments.dmg:
            dmg = arguments.dmg.resolve()
            common.require(dmg.is_file(), f"DMG 不存在：{dmg}")
            subprocess.run(["hdiutil", "verify", str(dmg)], check=True)
            mount: Path | None = None
            try:
                mount = attach_read_only(dmg)
                mounted_app = mount / app.name
                common.require(mounted_app.is_dir(), f"DMG 中缺少 {app.name}")
                common.require(not mounted_app.is_symlink(), "DMG 中的 App 不应是链接")
                mounted_root = temporary_root / "mounted-app"
                mounted_root.mkdir()
                verify_app(
                    mounted_app,
                    sample=sample,
                    temporary_root=mounted_root,
                    label="挂载 DMG App QA",
                )
            finally:
                if mount is not None and mount.exists():
                    subprocess.run(["hdiutil", "detach", str(mount)], check=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (common.VerificationError, subprocess.CalledProcessError, OSError, ValueError) as error:
        raise SystemExit(f"直装 packaged App QA 失败：{error}") from error
