"""Read-only media timing metadata used by frame-accurate player controls."""

from __future__ import annotations

import math
from pathlib import Path

import av
from av.error import FFmpegError

FALLBACK_FRAME_RATE = 30.0


def get_playback_info(video_path: str | Path) -> dict:
    path = Path(video_path)
    if not path.is_file():
        raise ValueError("视频文件不存在")
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise ValueError("视频中没有可用的视频流")
            stream = container.streams.video[0]
            rate = None
            try:
                rate = (
                    float(stream.average_rate)
                    if stream.average_rate is not None else None
                )
            except (TypeError, ValueError, ZeroDivisionError):
                rate = None
            if not rate or not math.isfinite(rate) or not 1 <= rate <= 240:
                rate = None
            reliable = rate is not None
            frame_rate = rate or FALLBACK_FRAME_RATE

            duration = None
            if stream.duration is not None and stream.time_base is not None:
                duration = float(stream.duration * stream.time_base)
            elif container.duration is not None:
                duration = float(container.duration / av.time_base)
            if duration is not None and (
                not math.isfinite(duration) or duration < 0
            ):
                duration = None
    except (FFmpegError, OSError) as exc:
        raise ValueError(f"无法读取视频播放信息：{exc}") from exc
    return {
        "frame_rate": round(frame_rate, 6),
        "frame_duration": round(1.0 / frame_rate, 9),
        "duration": round(duration, 6) if duration is not None else None,
        "frame_rate_reliable": reliable,
        "frame_rate_source": "average_rate" if reliable else "fallback_30fps",
    }
