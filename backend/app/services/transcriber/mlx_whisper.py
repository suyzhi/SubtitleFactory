"""MLX Whisper audio loading, VAD windowing, and segment iteration helpers."""

from __future__ import annotations

import logging
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from ...utils.config import MODELS_DIR
from ...utils.task_manager import task_manager

logger = logging.getLogger(__name__)


def _facade_attr(name: str, default: Any) -> Any:
    pkg = sys.modules.get(__package__ or "")
    return getattr(pkg, name, default) if pkg is not None else default


def _load_pcm16_wav_float32(audio_path: str) -> tuple[Any, float] | None:
    """Fast-path read for prepared 16 kHz mono 16-bit PCM WAV without spawning FFmpeg/PyAV."""
    try:
        import numpy as np

        with wave.open(audio_path, "rb") as audio:
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getframerate() != 16_000
                or audio.getcomptype() != "NONE"
            ):
                return None
            frames = audio.getnframes()
            raw = audio.readframes(frames)
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        return samples, frames / 16_000.0
    except Exception:
        return None


def _clear_mlx_metal_cache() -> None:
    try:
        import mlx.core as mx
    except Exception:
        return
    clear_cache = getattr(mx, "clear_cache", None)
    if callable(clear_cache):
        clear_cache()
        return
    metal = getattr(mx, "metal", None)
    metal_clear_cache = getattr(metal, "clear_cache", None)
    if callable(metal_clear_cache):
        metal_clear_cache()


def _vad_speech_windows(
    task_id: str,
    audio_path: str,
    audio_duration: float,
    *,
    max_window_seconds: float = 28.0,
    max_gap_seconds: float = 1.5,
) -> list[tuple[float, float]] | None:
    """Compute coalesced speech windows (~<=28s) using local Silero VAD if available."""
    candidate = Path(MODELS_DIR) / "silero_vad.onnx"
    vad_path: Path | None = candidate if candidate.is_file() else None
    if vad_path is None:
        try:
            from ..managed_sherpa import _ensure_vad

            vad_path = _ensure_vad(task_id)
        except Exception:
            return None
    try:
        from ..managed_sherpa import iter_vad_audio_segments

        windows: list[tuple[float, float]] = []
        win_start: float | None = None
        win_end: float = 0.0
        for start, end, _samples in iter_vad_audio_segments(
            task_id, audio_path, vad_path, audio_duration, max_speech_seconds=max_window_seconds
        ):
            if win_start is None:
                win_start, win_end = start, end
            elif end - win_start <= max_window_seconds and start - win_end <= max_gap_seconds:
                win_end = max(win_end, end)
            else:
                windows.append((win_start, win_end))
                win_start, win_end = start, end
        if win_start is not None:
            windows.append((win_start, win_end))
        return windows
    except Exception:
        logger.debug("Silero VAD unavailable for MLX Whisper; falling back to full audio", exc_info=True)
        return None


def _shift_mlx_segments(items: list[dict], offset: float) -> list[SimpleNamespace]:
    shifted: list[SimpleNamespace] = []
    for item in items:
        words = []
        for word in item.get("words") or []:
            if isinstance(word, dict):
                shifted_word = dict(word)
                if "start" in shifted_word:
                    shifted_word["start"] = float(shifted_word["start"]) + offset
                if "end" in shifted_word:
                    shifted_word["end"] = float(shifted_word["end"]) + offset
                words.append(shifted_word)
            else:
                words.append(word)
        shifted.append(
            SimpleNamespace(
                start=float(item["start"]) + offset,
                end=float(item["end"]) + offset,
                text=str(item["text"]),
                words=words,
            )
        )
    return shifted


def _iter_mlx_whisper_segments(
    task_id: str,
    mlx_whisper_mod: Any,
    model_path: str,
    audio_path: str,
    lang: str | None,
    loaded_wav: tuple[Any, float] | None,
    windows: list[tuple[float, float]] | None,
    detected_lang_box: list[str],
) -> Iterator[SimpleNamespace]:
    try:
        if loaded_wav is not None and windows is not None:
            samples, _duration = loaded_wav
            active_lang = lang
            for win_start, win_end in windows:
                task_manager.checkpoint(task_id)
                start_idx = max(0, int(round(win_start * 16_000)))
                end_idx = min(len(samples), int(round(win_end * 16_000)))
                if end_idx <= start_idx:
                    continue
                result = mlx_whisper_mod.transcribe(
                    samples[start_idx:end_idx],
                    path_or_hf_repo=model_path,
                    language=active_lang,
                    word_timestamps=True,
                    condition_on_previous_text=False,
                )
                if not active_lang and result.get("language"):
                    active_lang = str(result["language"])
                    detected_lang_box[0] = active_lang
                for seg in _shift_mlx_segments(result.get("segments", []), win_start):
                    yield seg
            return

        audio_input = loaded_wav[0] if loaded_wav is not None else audio_path
        result = mlx_whisper_mod.transcribe(
            audio_input,
            path_or_hf_repo=model_path,
            language=lang,
            word_timestamps=True,
            condition_on_previous_text=False,
        )
        if result.get("language"):
            detected_lang_box[0] = str(result["language"])
        for seg in _shift_mlx_segments(result.get("segments", []), 0.0):
            yield seg
    finally:
        _facade_attr("_clear_mlx_metal_cache", _clear_mlx_metal_cache)()
