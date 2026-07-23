"""Audio extraction via PyAV, so the desktop App does not need system ffmpeg."""

import logging
import os
import wave

import av

from ..utils.config import AUDIO_DIR
from ..utils.task_manager import task_manager

logger = logging.getLogger(__name__)


def extract_audio(
    task_id: str, video_path: str, project_id: str,
    track_index: int = 0, range_start: float | None = None, range_end: float | None = None,
    *, progress_start: float = 5, progress_end: float = 100,
) -> str:
    task_manager.update_task(
        task_id, step="extracting_audio", progress=progress_start,
        message="正在提取音频...",
    )
    task_manager.add_log(task_id, "info", "extracting_audio", "使用内置媒体引擎提取 16kHz 单声道音频")

    audio_dir = os.path.join(AUDIO_DIR, project_id)
    os.makedirs(audio_dir, exist_ok=True)
    audio_path = os.path.join(audio_dir, "audio.wav")
    temporary_path = os.path.join(audio_dir, f".audio-{task_id}.wav")

    try:
        with av.open(video_path) as container:
            if not container.streams.audio:
                raise ValueError("视频中没有可用音轨")
            if track_index >= len(container.streams.audio):
                raise ValueError("所选音轨不存在")
            stream = container.streams.audio[track_index]
            duration_seconds = float(stream.duration * stream.time_base) if stream.duration else 0.0
            resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=16000)

            with wave.open(temporary_path, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                for packet in container.demux(stream):
                    task_manager.checkpoint(task_id)
                    for frame in packet.decode():
                        frame_time = float(frame.time or 0)
                        if range_start is not None and frame_time + float(frame.duration or 0) * float(frame.time_base) < range_start:
                            continue
                        if range_end is not None and frame_time >= range_end:
                            break
                        for converted in resampler.resample(frame):
                            output.writeframes(bytes(converted.planes[0])[:converted.samples * 2])
                        if duration_seconds and frame.time is not None:
                            progress = min(
                                progress_end,
                                progress_start
                                + float(frame.time) / duration_seconds
                                * max(0, progress_end - progress_start),
                            )
                            task_manager.update_task(
                                task_id, step="extracting_audio", progress=progress,
                                message=f"正在提取音频 {min(100, round(float(frame.time) / duration_seconds * 100))}%",
                            )
                task_manager.checkpoint(task_id)
                for converted in resampler.resample(None):
                    output.writeframes(bytes(converted.planes[0])[:converted.samples * 2])

        task_manager.checkpoint(task_id)
        if not os.path.exists(temporary_path) or os.path.getsize(temporary_path) <= 44:
            raise RuntimeError("音频文件未生成或为空")
        os.replace(temporary_path, audio_path)
        file_size = os.path.getsize(audio_path)
        task_manager.update_task(
            task_id, step="audio_ready", progress=progress_end, message="音频提取完成",
            details={"audio_path": audio_path, "file_size": file_size, "engine": "PyAV"},
        )
        return audio_path
    except Exception:
        if os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                pass
        raise
