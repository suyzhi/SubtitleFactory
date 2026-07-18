"""Small cached audio-track previews for track selection."""

from __future__ import annotations

import hashlib
import wave
from pathlib import Path

import av

from ..utils.config import PROJECTS_DIR


def generate_track_preview(project_id: str, video_path: str, track_index: int, start: float, duration: float = 15) -> Path:
    source = Path(video_path).resolve(strict=True)
    stat = source.stat()
    key = hashlib.sha256(f"{source}|{stat.st_size}|{stat.st_mtime_ns}|{track_index}|{start:.1f}|{duration:.1f}".encode()).hexdigest()[:20]
    folder = Path(PROJECTS_DIR) / project_id / "previews"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"track-{key}.wav"
    if target.is_file() and target.stat().st_size > 44:
        return target
    temporary = target.with_suffix(".tmp")
    with av.open(str(source)) as container:
        if track_index < 0 or track_index >= len(container.streams.audio):
            raise ValueError("所选音轨不存在")
        stream = container.streams.audio[track_index]
        if start > 0:
            container.seek(int(start * 1_000_000), backward=True)
        resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=16_000)
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(16_000)
            written = 0
            limit = int(duration * 16_000)
            for packet in container.demux(stream):
                for frame in packet.decode():
                    frame_time = float(frame.time or 0)
                    if frame_time + float(frame.duration or 0) * float(frame.time_base) < start:
                        continue
                    for converted in resampler.resample(frame):
                        remaining = limit - written
                        if remaining <= 0:
                            break
                        count = min(converted.samples, remaining)
                        output.writeframes(bytes(converted.planes[0])[:count * 2])
                        written += count
                    if written >= limit:
                        break
                if written >= limit:
                    break
            for converted in resampler.resample(None):
                remaining = limit - written
                if remaining <= 0: break
                count = min(converted.samples, remaining)
                output.writeframes(bytes(converted.planes[0])[:count * 2]); written += count
    if temporary.stat().st_size <= 44:
        temporary.unlink(missing_ok=True)
        raise ValueError("所选范围没有可试听音频")
    temporary.replace(target)
    for old in sorted(folder.glob("track-*.wav"), key=lambda item: item.stat().st_mtime, reverse=True)[20:]:
        old.unlink(missing_ok=True)
    return target
