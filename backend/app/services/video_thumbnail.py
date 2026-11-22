"""Generate compact local-video thumbnails using the bundled FFmpeg runtime."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from .media_tools import parse_duration, probe_media, require_ffmpeg

logger = logging.getLogger(__name__)

THUMBNAIL_MAX_WIDTH = 480
THUMBNAIL_MAX_HEIGHT = 270


def _thumbnail_dimensions(width: int, height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("无效的视频画面尺寸")
    scale = min(
        THUMBNAIL_MAX_WIDTH / width,
        THUMBNAIL_MAX_HEIGHT / height,
        1.0,
    )
    target_width = max(2, int(width * scale))
    target_height = max(2, int(height * scale))
    # yuvj420p requires even dimensions.
    target_width -= target_width % 2
    target_height -= target_height % 2
    return max(2, target_width), max(2, target_height)


def _extract_jpeg_frame(
    ffmpeg: Path,
    video_path: str,
    temporary_path: Path,
    *,
    seek_seconds: float | None,
    width: int | None,
    height: int | None,
) -> bool:
    if width and height:
        target_width, target_height = _thumbnail_dimensions(width, height)
        scale_filter = f"scale={target_width}:{target_height}"
    else:
        scale_filter = (
            f"scale='min({THUMBNAIL_MAX_WIDTH},iw)':'min({THUMBNAIL_MAX_HEIGHT},ih)'"
            ":force_original_aspect_ratio=decrease,"
            "scale='max(2,trunc(iw/2)*2)':'max(2,trunc(ih/2)*2)'"
        )
    cmd = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
    ]
    if seek_seconds is not None and seek_seconds > 0:
        cmd.extend(["-ss", f"{seek_seconds:.3f}"])
    cmd.extend([
        "-i",
        video_path,
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-vf",
        scale_filter,
        "-pix_fmt",
        "yuvj420p",
        "-q:v",
        "4",
        "-f",
        "image2",
        str(temporary_path),
    ])
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    return completed.returncode == 0 and temporary_path.is_file() and temporary_path.stat().st_size > 0


def generate_video_thumbnail(video_path: str, project_dir: str) -> Optional[str]:
    """Create ``thumbnail.jpg`` and return its path, or ``None`` on failure.

    Uses the bundled FFmpeg/FFprobe binaries so PyAV does not need to be bundled.
    Thumbnail failure is intentionally non-fatal to local video import.
    """
    output_dir = Path(project_dir)
    output_path = output_dir / "thumbnail.jpg"
    temporary_path = output_dir / ".thumbnail.tmp.jpg"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        media_info = probe_media(video_path)
        video_streams = [
            stream
            for stream in (media_info.get("streams") or [])
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ]
        if not video_streams:
            raise ValueError("视频中没有可解码的视频流")

        stream = video_streams[0]
        duration_seconds = parse_duration(stream.get("duration"))
        if duration_seconds is None:
            duration_seconds = parse_duration((media_info.get("format") or {}).get("duration"))

        target_seconds = 1.0
        if duration_seconds is not None and duration_seconds < 4.0:
            target_seconds = max(0.0, duration_seconds * 0.25)

        raw_w = stream.get("width")
        raw_h = stream.get("height")
        width = int(raw_w) if isinstance(raw_w, (int, float)) and raw_w > 0 else None
        height = int(raw_h) if isinstance(raw_h, (int, float)) and raw_h > 0 else None

        ffmpeg = require_ffmpeg()
        ok = _extract_jpeg_frame(
            ffmpeg,
            video_path,
            temporary_path,
            seek_seconds=target_seconds,
            width=width,
            height=height,
        )
        if not ok and target_seconds > 0:
            # Non-seekable or very short clips: fall back to decoding the first frame.
            temporary_path.unlink(missing_ok=True)
            ok = _extract_jpeg_frame(
                ffmpeg,
                video_path,
                temporary_path,
                seek_seconds=None,
                width=width,
                height=height,
            )

        if not ok:
            raise RuntimeError("封面编码未产生有效文件")
        os.replace(temporary_path, output_path)
        return str(output_path.resolve())
    except Exception as exc:
        logger.warning("[Thumbnail] 本地视频封面提取失败: %s", exc, exc_info=True)
        temporary_path.unlink(missing_ok=True)
        return None
