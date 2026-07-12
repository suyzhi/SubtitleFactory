"""YouTube download service using the bundled yt-dlp Python API."""

import logging
import os
from pathlib import Path
from typing import Optional

import yt_dlp

from ..utils.config import DOWNLOADS_DIR
from ..utils.task_manager import task_manager

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".avif", ".jpeg", ".jpg", ".png", ".webp"}


def _download_options(
    task_id: str,
    output_template: str,
    quiet: bool = False,
    thumbnail_template: Optional[str] = None,
) -> dict:
    def progress_hook(data: dict):
        task_manager.wait_if_paused(task_id)
        status = data.get("status")
        if status == "downloading":
            downloaded = float(data.get("downloaded_bytes") or 0)
            total = float(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            percent = downloaded / total * 100 if total else 0
            task_manager.update_task(
                task_id, step="downloading", progress=min(95, 10 + percent * .85),
                message=f"正在下载视频 {percent:.0f}%" if total else "正在下载视频...",
                details={"downloaded_bytes": int(downloaded), "total_bytes": int(total)},
            )

    options = {
        # yt-dlp's recommended unrestricted selector: prefer the highest-quality
        # video stream and best audio stream, with a combined-format fallback.
        # Deliberately do not add a height/resolution filter here.
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "final_ext": "mp4",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": quiet,
        "no_warnings": quiet,
        "progress_hooks": [] if quiet else [progress_hook],
        # merge_output_format only applies when separate streams are merged.
        # Remux a single-file fallback as well, without re-encoding its quality.
        "postprocessors": [{
            "key": "FFmpegVideoRemuxer",
            "preferedformat": "mp4",
        }],
    }
    if thumbnail_template:
        options["writethumbnail"] = True
        options["outtmpl"] = {
            "default": output_template,
            "thumbnail": thumbnail_template,
        }
    return options


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
    return next((
        path for path in candidates
        if path and Path(path).suffix.lower() in _IMAGE_EXTENSIONS and os.path.isfile(path)
    ), None)


def _find_final_video(info: dict, ydl: yt_dlp.YoutubeDL) -> Optional[str]:
    """Resolve yt-dlp's final post-processed MP4, never a temporary stream."""
    candidates = [info.get("filepath"), info.get("_filename")]
    prepared = ydl.prepare_filename(info)
    if prepared:
        candidates.extend([prepared, f"{os.path.splitext(prepared)[0]}.mp4"])
    return next((
        path for path in candidates
        if path and Path(path).suffix.lower() == ".mp4" and os.path.isfile(path)
    ), None)


def download_video(task_id: str, url: str, project_id: str) -> str:
    task_manager.update_task(task_id, step="downloading", progress=2, message="正在解析视频信息...")
    project_dl_dir = os.path.join(DOWNLOADS_DIR, project_id)
    os.makedirs(project_dl_dir, exist_ok=True)
    output_template = os.path.join(project_dl_dir, "%(title)s.%(ext)s")
    thumbnail_template = os.path.join(project_dl_dir, "thumbnail.%(ext)s")

    # Avoid returning a stale cover when a project downloads a different URL.
    for old_thumbnail in Path(project_dl_dir).glob("thumbnail.*"):
        if old_thumbnail.suffix.lower() in _IMAGE_EXTENSIONS and old_thumbnail.is_file():
            old_thumbnail.unlink()

    try:
        with yt_dlp.YoutubeDL(_download_options(
            task_id, output_template, thumbnail_template=thumbnail_template,
        )) as ydl:
            info = ydl.extract_info(url, download=True)
            task_manager.checkpoint(task_id)
            video_path = _find_final_video(info, ydl)
        thumbnail_path = _find_thumbnail(info, project_dl_dir)
        if not video_path:
            raise RuntimeError("下载结束后未找到合并完成的 MP4 视频文件")
        title = info.get("title") or os.path.basename(video_path)
        thumbnail_url = info.get("thumbnail")
        if not isinstance(thumbnail_url, str) or not thumbnail_url.startswith(("http://", "https://")):
            thumbnail_url = None
        task_manager.update_task(
            task_id, step="downloaded", progress=100, message="视频下载完成",
            details={
                "video_path": video_path,
                "title": title,
                "file_size": os.path.getsize(video_path),
                "thumbnail_url": thumbnail_url,
                "thumbnail_path": thumbnail_path,
            },
        )
        return video_path
    except yt_dlp.utils.DownloadError as exc:
        # A progress-hook cancellation can be wrapped as DownloadError by
        # yt-dlp; restore the cooperative TaskCancelled control flow.
        task_manager.checkpoint(task_id)
        raise RuntimeError(f"视频下载失败：{str(exc)[:500]}") from exc


def get_video_info(url: str) -> dict:
    try:
        with yt_dlp.YoutubeDL(_download_options("info", "%(title)s.%(ext)s", quiet=True)) as ydl:
            info = ydl.extract_info(url, download=False)
        return {
            "title": info.get("title", ""),
            "duration": float(info.get("duration") or 0),
            "id": info.get("id", ""),
            "thumbnail_url": info.get("thumbnail") or None,
        }
    except Exception as exc:
        logger.warning("[Download] 获取视频信息失败: %s", exc)
        return {"title": "", "duration": 0, "id": "", "thumbnail_url": None}
