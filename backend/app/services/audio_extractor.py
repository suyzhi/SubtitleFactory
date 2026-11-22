"""Prepare bounded 16 kHz audio with reusable, source-aware metadata."""

from __future__ import annotations

import json
import logging
import math
import os
import select
import subprocess
import time
from pathlib import Path

from ..utils.config import AUDIO_DIR
from ..utils.task_manager import task_manager
from .media_tools import parse_duration, probe_media, require_ffmpeg

logger = logging.getLogger(__name__)


def _identity(path: Path):
    stat = path.stat()
    return [str(path.resolve()), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def extract_audio(
    task_id: str,
    video_path: str,
    project_id: str,
    track_index: int = 0,
    range_start: float | None = None,
    range_end: float | None = None,
    *,
    progress_start: float = 5,
    progress_end: float = 100,
) -> str:
    with task_manager.resource_slot(task_id, "ffmpeg"):
        return _extract_audio(
            task_id,
            video_path,
            project_id,
            track_index,
            range_start,
            range_end,
            progress_start,
            progress_end,
        )


def _extract_audio(
    task_id: str,
    video_path: str,
    project_id: str,
    track_index: int,
    range_start: float | None,
    range_end: float | None,
    progress_start: float,
    progress_end: float,
) -> str:
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
    identity = {
        "version": 1,
        "source": _identity(Path(video_path)),
        "track": track_index,
        "start": start,
        "end": end,
    }
    try:
        metadata = json.loads(metadata_path.read_text())
        if metadata.get("input") == identity and metadata.get("output") == _identity(audio_path):
            task_manager.update_task(
                task_id,
                step="audio_ready",
                progress=progress_end,
                message="复用已准备的音频",
                details={"audio_cache_hit": True, "audio_path": str(audio_path)},
            )
            return str(audio_path)
    except (OSError, ValueError, TypeError):
        pass

    task_manager.update_task(
        task_id,
        step="extracting_audio",
        progress=progress_start,
        message="正在提取音频…",
        details={"audio_cache_hit": False},
    )
    task_manager.add_log(task_id, "info", "extracting_audio", "提取所选范围的 16kHz 单声道音频")

    process: subprocess.Popen[str] | None = None
    try:
        media_info = probe_media(video_path)
        audio_streams = [
            stream
            for stream in (media_info.get("streams") or [])
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        ]
        if track_index >= len(audio_streams):
            raise ValueError("视频中没有所选音轨")

        stream_duration = parse_duration(audio_streams[track_index].get("duration"))
        format_duration = parse_duration((media_info.get("format") or {}).get("duration"))
        total_duration = stream_duration if stream_duration is not None else (format_duration or 0.0)
        stop = end if end is not None else total_duration
        expected_span = max(0.0, stop - start) if stop > start else 0.0

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
        cmd.extend(["-i", str(video_path)])
        if end is not None:
            cmd.extend(["-t", f"{end - start:.6f}"])
        cmd.extend([
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
            "-progress",
            "pipe:1",
            str(temporary_path),
        ])

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        last_update = 0.0
        assert process.stdout is not None
        while True:
            task_manager.checkpoint(task_id)
            ready, _, _ = select.select([process.stdout], [], [], 0.2)
            if ready:
                line = process.stdout.readline()
                if not line:
                    if process.poll() is not None:
                        break
                    continue
                if expected_span > 0 and line.startswith("out_time_us="):
                    try:
                        out_seconds = int(line.split("=", 1)[1].strip()) / 1_000_000.0
                    except ValueError:
                        out_seconds = 0.0
                    now = time.monotonic()
                    if now - last_update >= 0.25:
                        ratio = max(0.0, min(1.0, out_seconds / expected_span))
                        task_manager.update_task(
                            task_id,
                            step="extracting_audio",
                            progress=progress_start + ratio * (progress_end - progress_start),
                            message=f"正在提取音频 {round(ratio * 100)}%",
                        )
                        last_update = now
            elif process.poll() is not None:
                break

        stderr_output = process.stderr.read() if process.stderr else ""
        return_code = process.wait()
        process = None
        if return_code != 0:
            detail = stderr_output.strip()
            raise RuntimeError(f"FFmpeg 音频提取失败：{detail or f'退出码 {return_code}'}")

        task_manager.checkpoint(task_id)
        if not temporary_path.is_file() or temporary_path.stat().st_size <= 44:
            raise RuntimeError("音频文件未生成或所选范围为空")
        os.replace(temporary_path, audio_path)
        try:
            metadata_temporary.write_text(
                json.dumps({"input": identity, "output": _identity(audio_path)})
            )
            os.replace(metadata_temporary, metadata_path)
        except OSError:
            logger.warning("音频已保存，但未能写入缓存索引", exc_info=True)
        task_manager.update_task(
            task_id,
            step="audio_ready",
            progress=progress_end,
            message="音频提取完成",
            details={
                "audio_path": str(audio_path),
                "file_size": audio_path.stat().st_size,
                "engine": "FFmpeg",
            },
        )
        return str(audio_path)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        temporary_path.unlink(missing_ok=True)
        metadata_temporary.unlink(missing_ok=True)
