"""Shared FFmpeg / FFprobe helpers replacing PyAV in backend services."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

from ..utils.config import BASE_DIR, is_frozen_app
from .runtime_diagnostics import resolve_ffmpeg_path, resolve_ffprobe_path


def _dev_vendor_binary(name: str) -> Path | None:
    if is_frozen_app():
        return None
    from .distribution import distribution_capabilities

    if not distribution_capabilities().external_runtime_paths:
        return None
    candidate = BASE_DIR.parent / "vendor" / "ffmpeg" / "darwin-arm64" / f"{name}-darwin-arm64"
    return candidate if candidate.is_file() else None


def require_ffmpeg(user_path: str | Path | None = None) -> Path:
    resolved = resolve_ffmpeg_path(user_path)
    if resolved is not None:
        return resolved.path
    vendor = _dev_vendor_binary("ffmpeg")
    if vendor is not None:
        return vendor
    raise RuntimeError("未找到可用的 FFmpeg 运行时")


def require_ffprobe(user_path: str | Path | None = None) -> Path:
    resolved = resolve_ffprobe_path(user_path)
    if resolved is not None:
        return resolved.path
    vendor = _dev_vendor_binary("ffprobe")
    if vendor is not None:
        return vendor
    raise RuntimeError("未找到可用的 FFprobe 运行时")


def parse_rational(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"0/0", "N/A", "nan"}:
        return None
    if "/" in text:
        num_str, den_str = text.split("/", 1)
        try:
            num = float(num_str)
            den = float(den_str)
        except ValueError:
            return None
        if den == 0:
            return None
        result = num / den
    else:
        try:
            result = float(text)
        except ValueError:
            return None
    return result if math.isfinite(result) else None


def parse_duration(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def probe_media(
    media_path: str | Path,
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Run bundled ffprobe and return parsed format + stream metadata."""
    ffprobe = require_ffprobe()
    cmd = [
        str(ffprobe),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(media_path),
    ]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"无法读取媒体信息：{exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"无法读取媒体信息：{detail or f'ffprobe 退出码 {completed.returncode}'}")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("无法解析媒体元信息") from exc
    if not isinstance(payload, dict):
        raise ValueError("无法解析媒体元信息")
    return payload
