"""Hard-subtitle OCR using FFmpeg sampling and the macOS Vision helper."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

from ..utils.task_manager import task_manager
from .runtime_diagnostics import resolve_ffmpeg_path


def _helper() -> Path:
    root = Path(__file__).resolve().parents[2]
    bundled = root / "runtime" / "bin" / "vision-ocr"
    if bundled.is_file(): return bundled
    if platform.system() != "Darwin": raise RuntimeError("Vision OCR 仅支持 macOS")
    compiler = shutil.which("swiftc")
    source = root / "runtime" / "vision_ocr.swift"
    if not compiler or not source.is_file(): raise RuntimeError("当前运行包缺少 Vision OCR helper")
    target = Path(tempfile.gettempdir()) / "subtitle-factory-vision-ocr"
    if not target.is_file() or target.stat().st_mtime < source.stat().st_mtime:
        subprocess.run([compiler, str(source), "-O", "-o", str(target)], check=True, timeout=120)
    return target


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def run_ocr(
    task_id: str, video_path: str, region: dict, start: float, end: float,
    interval: float = 0.5,
):
    ffmpeg = resolve_ffmpeg_path(None)
    if not ffmpeg: raise RuntimeError("OCR 需要可用的 FFmpeg")
    helper = _helper()
    with tempfile.TemporaryDirectory(prefix="subtitle-ocr-") as folder:
        pattern = str(Path(folder) / "frame-%06d.jpg")
        x, y, width, height = (float(region[key]) for key in ("x", "y", "width", "height"))
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 and 0 < height <= 1 and x + width <= 1.001 and y + height <= 1.001):
            raise ValueError("OCR 字幕区域无效")
        filter_value = f"fps=1/{interval},crop=iw*{width}:ih*{height}:iw*{x}:ih*{y}"
        command = [str(ffmpeg.path), "-hide_banner", "-loglevel", "error", "-ss", str(start), "-i", video_path]
        if end > start: command.extend(["-t", str(end - start)])
        command.extend(["-vf", filter_value, "-q:v", "3", pattern])
        subprocess.run(command, check=True, timeout=max(120, int((end - start) * 2)))
        frames = sorted(Path(folder).glob("frame-*.jpg")); observations = []
        for index, frame in enumerate(frames):
            task_manager.checkpoint(task_id)
            result = subprocess.run([str(helper), str(frame)], capture_output=True, text=True, check=True, timeout=30)
            lines = json.loads(result.stdout or "[]")
            text = _normalize(" ".join(item["text"] for item in lines if float(item.get("confidence", 0)) >= .25))
            confidence = sum(float(item.get("confidence", 0)) for item in lines) / max(1, len(lines))
            observations.append({"time": start + index * interval, "text": text, "confidence": confidence})
            task_manager.update_task(task_id, step="ocr", progress=(index + 1) / max(1, len(frames)) * 90, message=f"正在识别字幕画面 {index + 1}/{len(frames)}")
    cues = []
    for item in observations:
        if not item["text"]: continue
        if cues and SequenceMatcher(None, cues[-1]["text"], item["text"]).ratio() >= .9:
            cues[-1]["end"] = item["time"] + interval; cues[-1]["confidence"] = min(cues[-1]["confidence"], item["confidence"])
        else:
            cues.append({"start": item["time"], "end": item["time"] + interval, "text": item["text"], "confidence": item["confidence"]})
    task_manager.update_task(task_id, step="ocr_preview", progress=100, message=f"OCR 预览已生成：{len(cues)} 条", details={"ocr_preview": cues})
    return cues
