"""Audio extraction via PyAV, so the desktop App does not need system ffmpeg."""

import logging
import os
import wave

import av

from ..utils.config import AUDIO_DIR
from ..utils.task_manager import task_manager

logger = logging.getLogger(__name__)


def extract_audio(task_id: str, video_path: str, project_id: str) -> str:
    task_manager.update_task(task_id, step="extracting_audio", progress=5, message="正在提取音频...")
    task_manager.add_log(task_id, "info", "extracting_audio", "使用内置媒体引擎提取 16kHz 单声道音频")

    audio_dir = os.path.join(AUDIO_DIR, project_id)
    os.makedirs(audio_dir, exist_ok=True)
    audio_path = os.path.join(audio_dir, "audio.wav")

    try:
        with av.open(video_path) as container:
            if not container.streams.audio:
                raise ValueError("视频中没有可用音轨")
            stream = container.streams.audio[0]
            duration_seconds = float(stream.duration * stream.time_base) if stream.duration else 0.0
            resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=16000)

            with wave.open(audio_path, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                for packet in container.demux(stream):
                    task_manager.checkpoint(task_id)
                    for frame in packet.decode():
                        for converted in resampler.resample(frame):
                            output.writeframes(bytes(converted.planes[0])[:converted.samples * 2])
                        if duration_seconds and frame.time is not None:
                            progress = min(95, 5 + float(frame.time) / duration_seconds * 90)
                            task_manager.update_task(
                                task_id, step="extracting_audio", progress=progress,
                                message=f"正在提取音频 {min(100, round(float(frame.time) / duration_seconds * 100))}%",
                            )
                task_manager.checkpoint(task_id)
                for converted in resampler.resample(None):
                    output.writeframes(bytes(converted.planes[0])[:converted.samples * 2])

        task_manager.checkpoint(task_id)
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) <= 44:
            raise RuntimeError("音频文件未生成或为空")
        file_size = os.path.getsize(audio_path)
        task_manager.update_task(
            task_id, step="audio_ready", progress=100, message="音频提取完成",
            details={"audio_path": audio_path, "file_size": file_size, "engine": "PyAV"},
        )
        return audio_path
    except Exception:
        if os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                pass
        raise
