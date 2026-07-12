"""Generate compact local-video thumbnails using the bundled PyAV runtime."""

import logging
import os
from pathlib import Path
from typing import Optional

import av


logger = logging.getLogger(__name__)

THUMBNAIL_MAX_WIDTH = 480
THUMBNAIL_MAX_HEIGHT = 270


def _representative_frame(video_path: str):
    """Decode a frame near the first second, avoiding a likely-black first frame."""
    with av.open(video_path) as container:
        if not container.streams.video:
            raise ValueError("视频中没有可解码的视频流")
        stream = container.streams.video[0]

        duration_seconds = None
        if stream.duration is not None and stream.time_base is not None:
            duration_seconds = float(stream.duration * stream.time_base)
        elif container.duration is not None:
            duration_seconds = float(container.duration / av.time_base)

        target_seconds = 1.0
        if duration_seconds is not None and duration_seconds < 4.0:
            target_seconds = max(0.0, duration_seconds * 0.25)

        target_pts = None
        if stream.time_base:
            target_pts = (stream.start_time or 0) + int(target_seconds / float(stream.time_base))
            try:
                container.seek(target_pts, stream=stream, backward=True, any_frame=False)
            except Exception:
                # Some valid containers are not seekable. Decoding from their
                # current position still provides a useful fallback frame.
                target_pts = None

        selected = None
        for index, frame in enumerate(container.decode(stream)):
            selected = frame
            if target_pts is None or frame.pts is None or frame.pts >= target_pts:
                break
            if index >= 300:
                break

        if selected is None:
            raise ValueError("视频中没有可解码的画面")
        return selected


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
    return target_width, target_height


def generate_video_thumbnail(video_path: str, project_dir: str) -> Optional[str]:
    """Create ``thumbnail.jpg`` and return its path, or ``None`` on failure.

    All decoding, scaling, and JPEG encoding is handled in-process by PyAV.
    Thumbnail failure is intentionally non-fatal to local video import.
    """
    output_dir = Path(project_dir)
    output_path = output_dir / "thumbnail.jpg"
    temporary_path = output_dir / ".thumbnail.tmp.jpg"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = _representative_frame(video_path)
        width, height = _thumbnail_dimensions(frame.width, frame.height)
        thumbnail = frame.reformat(width=width, height=height, format="yuvj420p")
        thumbnail.pts = 0

        with av.open(str(temporary_path), mode="w", format="image2") as output:
            stream = output.add_stream("mjpeg", rate=1)
            stream.width = width
            stream.height = height
            stream.pix_fmt = "yuvj420p"
            for packet in stream.encode(thumbnail):
                output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)

        if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            raise RuntimeError("封面编码未产生有效文件")
        os.replace(temporary_path, output_path)
        return str(output_path.resolve())
    except Exception as exc:
        logger.warning("[Thumbnail] 本地视频封面提取失败: %s", exc, exc_info=True)
        temporary_path.unlink(missing_ok=True)
        return None
