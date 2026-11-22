"""Small cached audio-track previews for track selection."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from ..utils.config import PROJECTS_DIR
from .media_tools import probe_media, require_ffmpeg


def generate_track_preview(
    project_id: str,
    video_path: str,
    track_index: int,
    start: float,
    duration: float = 15,
) -> Path:
    source = Path(video_path).resolve(strict=True)
    stat = source.stat()
    key = hashlib.sha256(
        f"{source}|{stat.st_size}|{stat.st_mtime_ns}|{track_index}|{start:.1f}|{duration:.1f}".encode()
    ).hexdigest()[:20]
    folder = Path(PROJECTS_DIR) / project_id / "previews"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"track-{key}.wav"
    if target.is_file() and target.stat().st_size > 44:
        return target

    media_info = probe_media(source)
    audio_streams = [
        stream
        for stream in (media_info.get("streams") or [])
        if isinstance(stream, dict) and stream.get("codec_type") == "audio"
    ]
    if track_index < 0 or track_index >= len(audio_streams):
        raise ValueError("所选音轨不存在")

    temporary = target.with_suffix(".tmp")
    ffmpeg = require_ffmpeg()
    cmd = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
    ]
    if start > 0:
        cmd.extend(["-ss", f"{start:.6f}"])
    cmd.extend([
        "-i",
        str(source),
        "-t",
        f"{duration:.6f}",
        "-map",
        f"0:a:{track_index}",
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(temporary),
    ])
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        if completed.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 44:
            temporary.unlink(missing_ok=True)
            raise ValueError("所选范围没有可试听音频")
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    for old in sorted(folder.glob("track-*.wav"), key=lambda item: item.stat().st_mtime, reverse=True)[20:]:
        old.unlink(missing_ok=True)
    return target
