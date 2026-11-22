"""Media integrity validation (ffprobe), staging file lookup, promotion, and cleanup."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from ...utils.config import DOWNLOADS_DIR
from ..download_errors import DownloadServiceError

logger = logging.getLogger("app.services.downloader")

_IMAGE_EXTENSIONS = {".avif", ".jpeg", ".jpg", ".png", ".webp"}


def _facade(name: str, default: Any) -> Any:
    pkg = sys.modules.get(__package__ or "")
    return getattr(pkg, name, default) if pkg is not None else default


def _find_thumbnail(info: dict, project_dl_dir: str) -> Optional[str]:
    """Return the downloaded thumbnail path, preferring yt-dlp metadata."""
    candidates = [
        item.get("filepath")
        for item in reversed(info.get("thumbnails") or [])
        if isinstance(item, dict)
    ]
    candidates.extend(
        str(path)
        for path in sorted(
            Path(project_dl_dir).glob("thumbnail.*"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    )
    return next(
        (
            path
            for path in candidates
            if path and Path(path).suffix.lower() in _IMAGE_EXTENSIONS and os.path.isfile(path)
        ),
        None,
    )


def _find_final_video(
    info: dict,
    ydl: Any,
    extension: str = "mp4",
) -> Optional[str]:
    """Resolve yt-dlp's final post-processed file, never a temporary stream."""
    candidates = [info.get("filepath"), info.get("_filename")]
    prepared = ydl.prepare_filename(info)
    if prepared:
        candidates.extend([prepared, f"{os.path.splitext(prepared)[0]}.{extension}"])
    return next(
        (
            path
            for path in candidates
            if path and Path(path).suffix.lower() == f".{extension}" and os.path.isfile(path)
        ),
        None,
    )


def _find_downloaded_audio(info: dict, ydl: Any) -> Optional[str]:
    candidates = [info.get("filepath"), info.get("_filename")]
    candidates.extend(
        item.get("filepath")
        for item in info.get("requested_downloads") or []
        if isinstance(item, dict)
    )
    prepared = ydl.prepare_filename(info)
    if prepared:
        candidates.append(prepared)
    return next(
        (
            path
            for path in candidates
            if path
            and os.path.isfile(path)
            and Path(path).suffix.lower() not in _IMAGE_EXTENSIONS | {".part", ".ytdl"}
        ),
        None,
    )


def _cleanup_task_staging(staging_dir: Path) -> dict[str, Any]:
    result = {
        "staging_path": staging_dir.name,
        "removed": False,
        "error": "",
    }
    try:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        result["removed"] = not staging_dir.exists()
    except OSError as exc:
        result["error"] = type(exc).__name__
        logger.warning("[Download] 清理任务暂存目录失败 (%s): %s", staging_dir.name, exc)
    return result


def _probe_media(
    media_path: Path,
    *,
    ffprobe_path: Path,
    expected_container: str | None,
    expected_duration: float,
    require_video: bool,
) -> dict[str, Any]:
    if not media_path.is_file() or media_path.stat().st_size <= 0:
        raise DownloadServiceError(
            "下载得到的媒体文件为空",
            "DOWNLOAD_FAILED",
            actions=["retry"],
            suggestion="请检查磁盘空间并重新下载",
            details={"failure_stage": "media_validation"},
        )
    try:
        completed = subprocess.run(
            [
                str(ffprobe_path),
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration,size:stream=codec_type,codec_name",
                "-of",
                "json",
                str(media_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DownloadServiceError(
            "无法使用 FFprobe 验证下载文件",
            "DOWNLOAD_RUNTIME_MISSING",
            actions=["open_settings", "retry"],
            suggestion="请重新安装完整 App，或检查 FFprobe 运行状态",
            details={"failure_stage": "media_validation", "runtime_component": "ffprobe"},
        ) from exc
    if completed.returncode != 0:
        raise DownloadServiceError(
            "下载文件未通过媒体完整性验证",
            "DOWNLOAD_FAILED",
            actions=["retry"],
            suggestion="文件可能不完整；App 已保留旧媒体，请重新下载",
            details={"failure_stage": "media_validation", "diagnostic_category": "ffprobe_rejected"},
        )

    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError as exc:
        raise DownloadServiceError(
            "FFprobe 返回了无法解析的媒体信息",
            "DOWNLOAD_FAILED",
            actions=["retry"],
            suggestion="请重新下载；若持续出现，请复制诊断信息",
            details={"failure_stage": "media_validation"},
        ) from exc
    streams = payload.get("streams") or []
    video_streams = [item for item in streams if item.get("codec_type") == "video"]
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    if require_video and (not video_streams or not audio_streams):
        raise DownloadServiceError(
            "下载文件缺少预期的视频流或音频流",
            "DOWNLOAD_FAILED",
            actions=["retry"],
            suggestion="请重新下载；MP4 封装持续失败时请改用 MKV",
            details={"failure_stage": "media_validation"},
        )
    if not require_video and not audio_streams:
        raise DownloadServiceError(
            "下载文件不包含可用音轨",
            "DOWNLOAD_FAILED",
            actions=["retry"],
            suggestion="请确认源视频包含音频后重新下载",
            details={"failure_stage": "media_validation"},
        )

    media_format = payload.get("format") or {}
    duration = float(media_format.get("duration") or 0)
    if duration <= 0:
        raise DownloadServiceError(
            "下载文件没有有效时长",
            "DOWNLOAD_FAILED",
            actions=["retry"],
            suggestion="请重新下载；App 已保留原有媒体",
            details={"failure_stage": "media_validation"},
        )
    if expected_duration > 0 and abs(duration - expected_duration) > max(10, expected_duration * 0.1):
        raise DownloadServiceError(
            "下载文件时长与 YouTube 元数据明显不一致",
            "DOWNLOAD_FAILED",
            actions=["retry"],
            suggestion="文件可能不完整；请重新下载",
            details={"failure_stage": "media_validation"},
        )

    format_name = str(media_format.get("format_name") or "")
    container_aliases = {
        "mp4": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
        "mkv": {"matroska", "webm"},
        "webm": {"matroska", "webm"},
    }
    detected_formats = set(format_name.split(","))
    if expected_container and not (
        detected_formats & container_aliases.get(expected_container, {expected_container})
    ):
        raise DownloadServiceError(
            "下载文件容器与用户选择不一致",
            "MERGE_FAILED",
            actions=["retry", "open_settings"],
            suggestion="请改用 MKV 容器以保留最高原始画质",
            details={"failure_stage": "media_validation"},
        )
    return {
        "duration": duration,
        "container": expected_container or format_name,
        "format_name": format_name,
        "video_codec": video_streams[0].get("codec_name") if video_streams else "",
        "audio_codec": audio_streams[0].get("codec_name") if audio_streams else "",
        "file_size": media_path.stat().st_size,
    }


def _selected_format_id(info: dict) -> str:
    requested = [
        str(item.get("format_id"))
        for item in info.get("requested_formats") or []
        if isinstance(item, dict) and item.get("format_id")
    ]
    return "+".join(requested) or str(info.get("format_id") or "")


def _promote_candidate(candidate: Path, project_dir: Path, stem: str) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    destination = project_dir / f"{stem}{candidate.suffix.lower()}"
    os.replace(candidate, destination)
    return destination


def remove_managed_download_file(
    path: str | os.PathLike[str] | None,
    *,
    project_id: str,
    download_dir: str | os.PathLike[str] | None,
) -> bool:
    """Delete only an App-managed file inside the selected project's folder."""
    if not path:
        return False
    candidate = Path(path).expanduser().resolve(strict=False)
    default_dir = _facade("DOWNLOADS_DIR", DOWNLOADS_DIR)
    project_root = (
        Path(download_dir or default_dir).expanduser().resolve(strict=False) / project_id
    )
    try:
        candidate.relative_to(project_root)
    except ValueError:
        return False
    if candidate.is_file():
        candidate.unlink()
        return True
    return False
