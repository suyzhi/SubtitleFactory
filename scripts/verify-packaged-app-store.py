#!/usr/bin/env python3
"""Exercise the packaged App Store QA App through its real localhost sidecar."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


ROOT = Path(__file__).resolve().parents[1]
VENDOR_FFMPEG = ROOT / "vendor/ffmpeg/darwin-arm64/ffmpeg-darwin-arm64"
EXPECTED_MODEL_COUNT = 28


class VerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    pgid: int
    command: str


@dataclass(frozen=True)
class BackendSession:
    process: ProcessInfo
    port: int
    token: str
    home: Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def process_table() -> list[ProcessInfo]:
    output = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,command="],
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    ).stdout
    result: list[ProcessInfo] = []
    for line in output.splitlines():
        match = re.match(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*)$", line)
        if match:
            result.append(
                ProcessInfo(
                    pid=int(match.group(1)),
                    ppid=int(match.group(2)),
                    pgid=int(match.group(3)),
                    command=match.group(4),
                )
            )
    return result


def ensure_no_running_subtitle_factory() -> None:
    matches = [
        item for item in process_table()
        if item.command.endswith(".app/Contents/MacOS/app")
        and ("字幕工厂" in item.command or "SubtitleFactory" in item.command)
    ]
    if matches:
        raise VerificationError(
            "请先退出正在运行的字幕工厂，再执行 packaged App QA；"
            "验收器不会关闭用户已打开的 App"
        )


def environment_value(process_text: str, name: str) -> str:
    match = re.search(rf"(?:^|\s){re.escape(name)}=([^\s]+)", process_text)
    return match.group(1) if match else ""


def group_exists(group: int) -> bool:
    try:
        os.killpg(group, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def wait_for_group_exit(group: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not group_exists(group):
            return
        time.sleep(0.1)
    raise VerificationError(f"sidecar 进程组 {group} 未在 {timeout:g} 秒内退出")


def stop_exact_process(process: subprocess.Popen[bytes] | None, group: int | None) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if group is not None and group_exists(group):
        os.killpg(group, signal.SIGTERM)
        try:
            wait_for_group_exit(group, timeout=5)
        except VerificationError:
            os.killpg(group, signal.SIGKILL)
            wait_for_group_exit(group, timeout=5)


def request(
    session: BackendSession,
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
    authorized: bool = True,
) -> tuple[int, bytes]:
    headers = {}
    if authorized:
        headers["Authorization"] = f"Bearer {session.token}"
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        content_type = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    item = urllib.request.Request(
        f"http://127.0.0.1:{session.port}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(item, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def json_response(
    session: BackendSession,
    path: str,
    *,
    expected: int,
    method: str = "GET",
    payload: dict | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
    authorized: bool = True,
) -> dict:
    status, raw = request(
        session,
        path,
        method=method,
        payload=payload,
        body=body,
        content_type=content_type,
        authorized=authorized,
    )
    require(status == expected, f"{method} {path} 返回 {status}，预期 {expected}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise VerificationError(f"{method} {path} 未返回 JSON") from error
    require(isinstance(value, dict), f"{method} {path} 返回的不是 JSON 对象")
    return value


def wait_for_backend(main_pid: int, timeout: float = 30.0) -> BackendSession:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidates = [
            item for item in process_table()
            if item.ppid == main_pid and "backend-runtime/subtitle-backend" in item.command
        ]
        if candidates:
            backend = candidates[0]
            require(backend.pgid == backend.pid, "sidecar 没有运行在独立进程组")
            process_text = subprocess.run(
                ["ps", "eww", "-p", str(backend.pid), "-o", "command="],
                check=True,
                capture_output=True,
                text=True,
                errors="replace",
            ).stdout
            port = environment_value(process_text, "SUBTITLE_FACTORY_PORT")
            token = environment_value(process_text, "SUBTITLE_FACTORY_API_TOKEN")
            home = environment_value(process_text, "HOME")
            if port.isdigit() and token and home:
                session = BackendSession(backend, int(port), token, Path(home))
                try:
                    status, _ = request(session, "/api/health")
                except (OSError, urllib.error.URLError):
                    status = 0
                if status == 200:
                    return session
        time.sleep(0.1)
    raise VerificationError("packaged App sidecar 未在 30 秒内就绪")


def launch(
    executable: Path,
    log: BinaryIO,
    *,
    environment: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[subprocess.Popen[bytes], BackendSession]:
    process = subprocess.Popen(
        [str(executable)],
        stdout=log,
        stderr=subprocess.STDOUT,
        env=environment,
    )
    try:
        return process, wait_for_backend(process.pid, timeout=timeout)
    except Exception:
        stop_exact_process(process, None)
        raise


def multipart_file(path: Path) -> tuple[bytes, str]:
    boundary = f"subtitle-factory-{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="review-sample.mp4"\r\n'
        "Content-Type: video/mp4\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def verify_api(
    session: BackendSession,
    bundle_id: str,
    expected_version: str,
    sample: Path,
) -> None:
    expected_home = Path.home() / "Library/Containers" / bundle_id / "Data"
    require(session.home == expected_home, "App Store sidecar HOME 不在 App Sandbox 容器内")
    managed_data = expected_home / "Library/Application Support" / bundle_id / "data"

    json_response(session, "/api/health", expected=401, authorized=False)
    health = json_response(session, "/api/health", expected=200)
    capabilities = health.get("distribution") or {}
    runtime = health.get("runtime") or {}
    require(health.get("version") == expected_version, "packaged sidecar 与 App 版本不一致")
    require(capabilities.get("channel") == "app_store", "packaged sidecar 不是 app_store 通道")
    for key in ("youtube", "browser_cookies", "filesystem_automation", "external_runtime_paths"):
        require(capabilities.get(key) is False, f"App Store 能力 {key} 没有关闭")
    for key in ("yt_dlp", "deno", "ejs"):
        require((runtime.get(key) or {}).get("status") == "disabled", f"{key} 没有禁用")
    require(
        runtime.get("data_directory") == f"~/Library/Application Support/{bundle_id}/data",
        "健康接口没有返回脱敏的沙箱数据目录",
    )

    catalog = json_response(session, "/api/transcription/models", expected=200)
    models = catalog.get("models") or []
    ids = {item.get("id") for item in models if isinstance(item, dict)}
    require(len(models) == EXPECTED_MODEL_COUNT, f"App Store 模型数为 {len(models)}，预期 {EXPECTED_MODEL_COUNT}")
    require("custom" not in ids, "App Store 模型目录包含 custom")
    require("parakeet-tdt-0.6b-v3-coreml" not in ids, "App Store 模型目录包含外部 Core ML")
    require(not any(str(item).startswith("local:") for item in ids), "App Store 模型目录包含外部模型")

    blocked = [
        ("/api/projects", "POST", {
            "title": "blocked-review", "source_type": "youtube",
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        }),
        ("/api/watch-folders", "GET", None),
        ("/api/settings/app/validate-path", "POST", {
            "kind": "download_directory", "path": "/tmp",
        }),
        ("/api/transcription/models/imported", "GET", None),
    ]
    for path, method, payload in blocked:
        result = json_response(session, path, expected=403, method=method, payload=payload)
        error = result.get("error") or {}
        require(error.get("code") == "DISTRIBUTION_FEATURE_UNAVAILABLE", f"{path} 没有稳定的发行限制错误")
        require(error.get("recoverable") is False, f"{path} 错误地声称可以重试")

    project_id: str | None = None
    try:
        created = json_response(
            session,
            "/api/projects",
            expected=201,
            method="POST",
            payload={"title": "App Store packaged import QA", "source_type": "local"},
        )
        project_id = str(created.get("project_id") or "")
        require(bool(project_id), "本地 QA 项目没有 ID")
        multipart, content_type = multipart_file(sample)
        imported = json_response(
            session,
            f"/api/projects/{project_id}/import-local",
            expected=200,
            method="POST",
            body=multipart,
            content_type=content_type,
        )
        video_path = Path(str(imported.get("video_path") or ""))
        project_root = managed_data / "projects" / project_id
        require(video_path.parent == project_root, "本地媒体没有复制到沙箱项目目录")
        require(video_path.is_file(), "沙箱项目媒体文件不存在")
        require(
            imported.get("thumbnail_url") == f"/api/projects/{project_id}/thumbnail",
            "包内 FFmpeg 没有生成项目缩略图",
        )
        detail = json_response(session, f"/api/projects/{project_id}", expected=200)
        require(detail.get("id") == project_id, "读取到错误的本地项目")
        require(detail.get("source_type") == "local", "本地项目 source_type 错误")
        require(detail.get("media_mode") == "local", "本地项目 media_mode 错误")
        require(detail.get("video_path") == str(video_path), "项目媒体路径与导入结果不一致")

        json_response(
            session,
            f"/api/projects/{project_id}?permanent=true",
            expected=200,
            method="DELETE",
        )
        project_id = None
        require(not project_root.exists(), "QA 项目目录没有被精确清理")
    finally:
        if project_id:
            try:
                json_response(
                    session,
                    f"/api/projects/{project_id}?permanent=true",
                    expected=200,
                    method="DELETE",
                )
            except Exception:
                pass


def verify_lifecycle(
    executable: Path,
    bundle_id: str,
    log: BinaryIO,
    first_process: subprocess.Popen[bytes],
    first_session: BackendSession,
    *,
    environment: dict[str, str] | None = None,
    label: str = "App Store QA",
) -> None:
    first_process.kill()
    first_process.wait(timeout=5)
    wait_for_group_exit(first_session.process.pgid)
    print(f"{label} 强制退出清理通过")

    second_process, second_session = launch(
        executable,
        log,
        environment=environment,
        timeout=60,
    )
    try:
        subprocess.run(
            ["osascript", "-e", f'tell application id "{bundle_id}" to quit'],
            check=True,
            capture_output=True,
            text=True,
            errors="replace",
        )
        second_process.wait(timeout=30)
        wait_for_group_exit(second_session.process.pgid)
        pid_file = (
            second_session.home / "Library/Application Support" / bundle_id / "backend.pid"
        )
        require(not pid_file.exists(), "正常退出后仍遗留 backend.pid")
    finally:
        stop_exact_process(second_process, second_session.process.pgid)
    print(f"{label} 正常退出清理通过")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "app",
        nargs="?",
        type=Path,
        default=ROOT / "字幕工厂-AppStore-QA.app",
    )
    args = parser.parse_args()
    app = args.app.resolve()
    require(app.is_dir(), f"App 不存在：{app}")
    info_path = app / "Contents/Info.plist"
    with info_path.open("rb") as source:
        info = plistlib.load(source)
    bundle_id = str(info.get("CFBundleIdentifier") or "")
    expected_version = str(info.get("CFBundleShortVersionString") or "")
    executable_name = str(info.get("CFBundleExecutable") or "")
    require(bundle_id == "com.subtitlefactory.desktop", "QA App Bundle ID 不正确")
    require(bool(expected_version), "QA App 版本号缺失")
    executable = app / "Contents/MacOS" / executable_name
    require(executable.is_file(), "QA App 主可执行文件缺失")
    require(VENDOR_FFMPEG.is_file(), "受控 FFmpeg 测试运行时缺失")
    ensure_no_running_subtitle_factory()

    first_process: subprocess.Popen[bytes] | None = None
    first_session: BackendSession | None = None
    with tempfile.TemporaryDirectory(prefix="subtitle-factory-appstore-qa-") as temporary:
        test_dir = Path(temporary)
        sample = test_dir / "review-sample.mp4"
        subprocess.run(
            [
                str(VENDOR_FFMPEG), "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=320x180:d=0.5",
                "-c:v", "mpeg4", "-q:v", "5", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(sample),
            ],
            check=True,
        )
        with (test_dir / "desktop.log").open("wb") as log:
            try:
                first_process, first_session = launch(executable, log)
                verify_api(first_session, bundle_id, expected_version, sample)
                print(
                    f"App Store QA API 通过：401 鉴权、{EXPECTED_MODEL_COUNT} 个模型、"
                    "4 类发行限制、本地导入与精确清理"
                )
                verify_lifecycle(
                    executable, bundle_id, log, first_process, first_session
                )
                first_process = None
                first_session = None
            finally:
                stop_exact_process(
                    first_process,
                    first_session.process.pgid if first_session else None,
                )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"App Store packaged QA 失败：{error}") from error
