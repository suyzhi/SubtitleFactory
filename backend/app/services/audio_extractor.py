"""Prepare bounded 16 kHz audio with reusable, source-aware metadata."""
import json
import logging
import math
import os
import time
import wave
from pathlib import Path

import av

from ..utils.config import AUDIO_DIR
from ..utils.task_manager import task_manager

logger = logging.getLogger(__name__)


def _identity(path: Path):
    stat = path.stat()
    return [str(path.resolve()), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def extract_audio(
    task_id: str, video_path: str, project_id: str,
    track_index: int = 0, range_start: float | None = None, range_end: float | None = None,
    *, progress_start: float = 5, progress_end: float = 100,
) -> str:
    with task_manager.resource_slot(task_id, "ffmpeg"):
        return _extract_audio(task_id, video_path, project_id, track_index, range_start, range_end,
                              progress_start, progress_end)


def _extract_audio(task_id, video_path, project_id, track_index, range_start, range_end,
                   progress_start, progress_end):
    start = max(0.0, float(range_start or 0))
    end = float(range_end) if range_end is not None else None
    if track_index < 0 or not math.isfinite(start) or (end is not None and (not math.isfinite(end) or end <= start)):
        raise ValueError("音轨或截取范围无效")
    audio_dir = Path(AUDIO_DIR) / project_id
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / "audio.wav"
    metadata_path = audio_dir / "audio-source.json"
    temporary_path = audio_dir / f".audio-{task_id}.wav"
    metadata_temporary = audio_dir / f".audio-{task_id}.json"
    identity = {"version": 1, "source": _identity(Path(video_path)), "track": track_index,
                "start": start, "end": end}
    try:
        metadata = json.loads(metadata_path.read_text())
        if metadata.get("input") == identity and metadata.get("output") == _identity(audio_path):
            task_manager.update_task(task_id, step="audio_ready", progress=progress_end,
                                     message="复用已准备的音频", details={"audio_cache_hit": True, "audio_path": str(audio_path)})
            return str(audio_path)
    except (OSError, ValueError, TypeError):
        pass
    task_manager.update_task(task_id, step="extracting_audio", progress=progress_start,
                             message="正在提取音频…", details={"audio_cache_hit": False})
    task_manager.add_log(task_id, "info", "extracting_audio", "提取所选范围的 16kHz 单声道音频")
    try:
        with av.open(video_path) as container:
            if track_index >= len(container.streams.audio):
                raise ValueError("视频中没有所选音轨")
            stream = container.streams.audio[track_index]
            duration = float(stream.duration * stream.time_base) if stream.duration else 0.0
            stop = end if end is not None else duration
            if start:
                container.seek(int(start * av.time_base), backward=True)
            resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=16000)
            last_update = 0.0
            fallback_time = start
            with wave.open(str(temporary_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)

                def write_frame(converted):
                    nonlocal fallback_time
                    timestamp = float(converted.time) if converted.time is not None else fallback_time
                    fallback_time = timestamp + converted.samples / 16000
                    first = max(0, math.ceil((start - timestamp) * 16000 - 1e-6))
                    last = converted.samples if end is None else min(converted.samples, math.ceil((end - timestamp) * 16000 - 1e-6))
                    if last > first:
                        output.writeframes(bytes(converted.planes[0])[first * 2:last * 2])

                reached_end = False
                for packet in container.demux(stream):
                    task_manager.checkpoint(task_id)
                    for frame in packet.decode():
                        timestamp = float(frame.time) if frame.time is not None else fallback_time
                        if end is not None and timestamp >= end:
                            reached_end = True
                            break
                        for converted in resampler.resample(frame):
                            write_frame(converted)
                        now = time.monotonic()
                        if stop > start and now - last_update >= 0.25:
                            ratio = max(0, min(1, (timestamp - start) / (stop - start)))
                            task_manager.update_task(task_id, step="extracting_audio",
                                progress=progress_start + ratio * (progress_end - progress_start),
                                message=f"正在提取音频 {round(ratio * 100)}%")
                            last_update = now
                    if reached_end:
                        break
                for converted in resampler.resample(None):
                    write_frame(converted)
        task_manager.checkpoint(task_id)
        if temporary_path.stat().st_size <= 44:
            raise RuntimeError("音频文件未生成或所选范围为空")
        os.replace(temporary_path, audio_path)
        try:
            metadata_temporary.write_text(json.dumps({"input": identity, "output": _identity(audio_path)}))
            os.replace(metadata_temporary, metadata_path)
        except OSError:
            logger.warning("音频已保存，但未能写入缓存索引", exc_info=True)
        task_manager.update_task(task_id, step="audio_ready", progress=progress_end, message="音频提取完成",
            details={"audio_path": str(audio_path), "file_size": audio_path.stat().st_size, "engine": "PyAV"})
        return str(audio_path)
    finally:
        temporary_path.unlink(missing_ok=True)
        metadata_temporary.unlink(missing_ok=True)
