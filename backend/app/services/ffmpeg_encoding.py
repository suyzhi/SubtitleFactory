"""Select video encoder arguments supported by the resolved FFmpeg binary."""

from __future__ import annotations

import subprocess
from pathlib import Path


def select_h264_encoder_args(ffmpeg_path: str | Path) -> tuple[list[str], str]:
    """Return an encoder configuration supported by the actual binary.

    The bundled macOS FFmpeg favors VideoToolbox and does not include libx264,
    so x264-only flags such as ``-preset fast`` must not be unconditional.
    """
    try:
        result = subprocess.run(
            [str(ffmpeg_path), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        encoders = f"{result.stdout}\n{result.stderr}"
    except (OSError, subprocess.TimeoutExpired):
        encoders = ""

    if "libx264" in encoders:
        return ["-c:v", "libx264", "-preset", "fast", "-crf", "22"], "libx264"
    if "h264_videotoolbox" in encoders:
        return ["-c:v", "h264_videotoolbox", "-b:v", "6M"], "h264_videotoolbox"
    if "h264_nvenc" in encoders:
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "22"], "h264_nvenc"
    # MPEG-4 Part 2 is present in most minimal builds and keeps exports usable
    # when no H.264 encoder is available.
    return ["-c:v", "mpeg4", "-q:v", "4"], "mpeg4"
