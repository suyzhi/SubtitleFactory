"""Read-only media timing metadata used by frame-accurate player controls."""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from .media_tools import parse_duration, parse_rational, probe_media

FALLBACK_FRAME_RATE = 30.0


def get_playback_info(video_path: str | Path) -> dict:
    path = Path(video_path).resolve()
    if not path.is_file():
        raise ValueError("视频文件不存在")
    stat = path.stat()
    return dict(
        _cached_playback_info(
            str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        )
    )


@lru_cache(maxsize=128)
def _cached_playback_info(path: str, *identity: int) -> dict:
    try:
        info = probe_media(path)
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(f"无法读取视频播放信息：{exc}") from exc

    video_streams = [
        stream
        for stream in (info.get("streams") or [])
        if isinstance(stream, dict) and stream.get("codec_type") == "video"
    ]
    if not video_streams:
        raise ValueError("视频中没有可用的视频流")

    stream = video_streams[0]
    rate = parse_rational(stream.get("avg_frame_rate"))
    if rate is None or not math.isfinite(rate) or not 1 <= rate <= 240:
        rate = None
    reliable = rate is not None
    frame_rate = rate or FALLBACK_FRAME_RATE

    duration = parse_duration(stream.get("duration"))
    if duration is None:
        duration = parse_duration((info.get("format") or {}).get("duration"))

    return {
        "frame_rate": round(frame_rate, 6),
        "frame_duration": round(1.0 / frame_rate, 9),
        "duration": round(duration, 6) if duration is not None else None,
        "frame_rate_reliable": reliable,
        "frame_rate_source": "average_rate" if reliable else "fallback_30fps",
    }
