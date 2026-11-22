"""Decouple faster-whisper from PyAV (42 MiB) and standalone onnxruntime (63 MiB).

- Audio decoding uses stdlib ``wave`` for 16 kHz mono 16-bit PCM WAV (our own
  ``audio_extractor`` format) and falls back to the bundled ``ffmpeg`` binary.
- Silero VAD reuses the already-bundled ``sherpa-onnx`` runtime + shared
  ``silero_vad.onnx`` (629 KB), eliminating the duplicate ``onnxruntime`` 1.27
  wheel while keeping all faster-whisper CPU models and CTranslate2 imports
  working identically.
- Core ML availability is probed directly on ``sherpa-onnx``'s bundled
  ``libonnxruntime`` library.
"""

from __future__ import annotations

import ctypes
import io
import logging
import math
import os
import subprocess
import sys
import types
import wave
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO, Optional, Union

import numpy as np

from ..utils.config import BASE_DIR, MODELS_DIR, is_frozen_app
from .media_tools import require_ffmpeg

logger = logging.getLogger(__name__)


def _candidate_silero_vad_paths() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.getenv("SUBTITLE_FACTORY_SILERO_VAD_PATH", "").strip()
    if env_path:
        candidates.append(Path(env_path))

    resource_dir = os.getenv("SUBTITLE_FACTORY_RESOURCE_DIR", "").strip()
    if resource_dir:
        candidates.append(Path(resource_dir) / "assets" / "silero_vad.onnx")

    if is_frozen_app():
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "assets" / "silero_vad.onnx")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "assets" / "silero_vad.onnx")

    candidates.append(MODELS_DIR / "silero_vad.onnx")
    if not is_frozen_app():
        candidates.append(BASE_DIR / "runtime" / "assets" / "silero_vad.onnx")
        candidates.append(BASE_DIR.parent / "vendor" / "silero-vad" / "silero_vad.onnx")
        candidates.append(
            BASE_DIR.parent / "frontend" / "src-tauri" / "backend-runtime" / "assets" / "silero_vad.onnx"
        )
    return candidates


def resolve_silero_vad_path() -> Path | None:
    for path in _candidate_silero_vad_paths():
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


@lru_cache(maxsize=1)
def sherpa_coreml_available() -> bool:
    """Verify Core ML Execution Provider directly on sherpa-onnx's ORT library."""
    if sys.platform != "darwin":
        return False
    try:
        import sherpa_onnx
    except Exception:
        return False
    lib_dir = Path(sherpa_onnx.__file__).resolve().parent / "lib"
    candidates = sorted(
        p for p in lib_dir.glob("libonnxruntime*.dylib") if p.is_file() and not p.is_symlink()
    )
    if not candidates:
        candidates = sorted(lib_dir.glob("libonnxruntime*.dylib"))
    for candidate in candidates:
        try:
            lib = ctypes.CDLL(str(candidate))
            if hasattr(lib, "OrtSessionOptionsAppendExecutionProvider_CoreML"):
                return True
        except OSError:
            continue
    return False


def _try_read_pcm_wav(
    input_file: Union[str, Path, BinaryIO],
    sampling_rate: int,
    split_stereo: bool,
) -> np.ndarray | tuple[np.ndarray, np.ndarray] | None:
    if isinstance(input_file, (str, os.PathLike)):
        path = Path(input_file)
        if path.suffix.lower() != ".wav" or not path.is_file():
            return None
        try:
            with wave.open(str(path), "rb") as reader:
                if (
                    reader.getsampwidth() != 2
                    or reader.getframerate() != sampling_rate
                    or reader.getcomptype() != "NONE"
                ):
                    return None
                channels = reader.getnchannels()
                if split_stereo and channels != 2:
                    return None
                if not split_stereo and channels != 1:
                    return None
                raw = reader.readframes(reader.getnframes())
        except (wave.Error, OSError):
            return None
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if split_stereo:
            return samples[0::2], samples[1::2]
        return samples
    return None


def decode_audio(
    input_file: Union[str, os.PathLike[str], BinaryIO],
    sampling_rate: int = 16000,
    split_stereo: bool = False,
) -> Union[np.ndarray, tuple[np.ndarray, np.ndarray]]:
    """Decode audio without PyAV, matching ``faster_whisper.audio.decode_audio``."""
    fast = _try_read_pcm_wav(input_file, sampling_rate, split_stereo)
    if fast is not None:
        return fast

    ffmpeg = require_ffmpeg()
    channels = 2 if split_stereo else 1
    cmd = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    stdin_bytes: bytes | None = None
    if isinstance(input_file, (str, os.PathLike)):
        cmd.extend(["-nostdin", "-i", str(input_file)])
    else:
        stdin_bytes = input_file.read()
        cmd.extend(["-i", "pipe:0"])
    cmd.extend([
        "-vn",
        "-sn",
        "-dn",
        "-f",
        "s16le",
        "-ac",
        str(channels),
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(sampling_rate),
        "pipe:1",
    ])
    completed = subprocess.run(
        cmd,
        input=stdin_bytes,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"FFmpeg 音频解码失败：{detail or completed.returncode}")

    audio = np.frombuffer(completed.stdout, dtype="<i2").astype(np.float32) / 32768.0
    if split_stereo:
        return audio[0::2], audio[1::2]
    return audio


def pad_or_trim(array: np.ndarray, length: int = 3000, *, axis: int = -1) -> np.ndarray:
    """Drop-in copy of ``faster_whisper.audio.pad_or_trim``."""
    if array.shape[axis] > length:
        array = array.take(indices=range(length), axis=axis)
    if array.shape[axis] < length:
        pad_widths = [(0, 0)] * array.ndim
        pad_widths[axis] = (0, length - array.shape[axis])
        array = np.pad(array, pad_widths)
    return array


def sherpa_get_speech_timestamps(
    audio: np.ndarray,
    vad_options: Optional[Any] = None,
    sampling_rate: int = 16000,
    **kwargs: Any,
) -> list[dict[str, int]]:
    """Run Silero VAD via sherpa-onnx, matching faster_whisper.vad.get_speech_timestamps."""
    import sherpa_onnx

    samples = np.ascontiguousarray(audio, dtype=np.float32).reshape(-1)
    audio_length_samples = int(samples.shape[0])
    if audio_length_samples == 0:
        return []

    vad_path = resolve_silero_vad_path()
    if vad_path is None:
        # Try downloading the verified shared Silero VAD model if online.
        try:
            from .managed_sherpa import _ensure_vad

            vad_path = _ensure_vad("faster-whisper-vad")
        except Exception as exc:
            logger.warning("无法获取 Silero VAD 模型，将回退为整段音频转写: %s", exc)
            return [{"start": 0, "end": audio_length_samples}]

    threshold = float(getattr(vad_options, "threshold", 0.5))
    min_speech_ms = float(getattr(vad_options, "min_speech_duration_ms", 0) or 0)
    max_speech_s = float(getattr(vad_options, "max_speech_duration_s", float("inf")))
    min_silence_ms = float(getattr(vad_options, "min_silence_duration_ms", 2000) or 2000)
    speech_pad_ms = float(getattr(vad_options, "speech_pad_ms", 400) or 400)

    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(vad_path)
    config.silero_vad.threshold = max(0.05, min(0.95, threshold))
    config.silero_vad.min_silence_duration = max(0.05, min_silence_ms / 1000.0)
    config.silero_vad.min_speech_duration = max(0.01, min_speech_ms / 1000.0)
    config.silero_vad.max_speech_duration = (
        max(1.0, max_speech_s) if math.isfinite(max_speech_s) else 30.0
    )
    config.sample_rate = int(sampling_rate)
    window_size = int(config.silero_vad.window_size) or 512

    buffer_seconds = max(30.0, min(600.0, audio_length_samples / float(sampling_rate) + 10.0))
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=buffer_seconds)

    speeches: list[dict[str, int]] = []
    offset = 0
    while offset + window_size <= audio_length_samples:
        vad.accept_waveform(samples[offset : offset + window_size])
        offset += window_size
        while not vad.empty():
            segment = vad.front
            start = max(0, int(segment.start))
            end = min(audio_length_samples, start + len(segment.samples))
            vad.pop()
            if end > start:
                speeches.append({"start": start, "end": end})

    if offset < audio_length_samples:
        tail = np.zeros(window_size, dtype=np.float32)
        tail[: audio_length_samples - offset] = samples[offset:]
        vad.accept_waveform(tail)

    vad.flush()
    while not vad.empty():
        segment = vad.front
        start = max(0, int(segment.start))
        end = min(audio_length_samples, start + len(segment.samples))
        vad.pop()
        if end > start:
            speeches.append({"start": start, "end": end})

    if not speeches:
        return []

    # Match faster_whisper.vad's midpoint-preserving speech padding so adjacent
    # segments remain strictly non-overlapping for SpeechTimestampsMap.
    speech_pad_samples = int(sampling_rate * speech_pad_ms / 1000.0)
    for i, speech in enumerate(speeches):
        if i == 0:
            speech["start"] = int(max(0, speech["start"] - speech_pad_samples))
        if i != len(speeches) - 1:
            silence_duration = speeches[i + 1]["start"] - speech["end"]
            if silence_duration < 2 * speech_pad_samples:
                half = max(0, int(silence_duration // 2))
                speech["end"] = min(audio_length_samples, speech["end"] + half)
                speeches[i + 1]["start"] = max(speech["end"], speeches[i + 1]["start"] - half)
            else:
                speech["end"] = int(min(audio_length_samples, speech["end"] + speech_pad_samples))
                speeches[i + 1]["start"] = int(
                    max(speech["end"], speeches[i + 1]["start"] - speech_pad_samples)
                )
        else:
            speech["end"] = int(min(audio_length_samples, speech["end"] + speech_pad_samples))

    return speeches


def install_faster_whisper_shim() -> Any:
    """Install PyAV-free audio decoding and sherpa-onnx VAD for faster-whisper."""
    audio_mod = sys.modules.get("faster_whisper.audio")
    if audio_mod is None:
        audio_mod = types.ModuleType("faster_whisper.audio")
        audio_mod.decode_audio = decode_audio  # type: ignore[attr-defined]
        audio_mod.pad_or_trim = pad_or_trim  # type: ignore[attr-defined]
        sys.modules["faster_whisper.audio"] = audio_mod
    else:
        audio_mod.decode_audio = decode_audio  # type: ignore[attr-defined]
        audio_mod.pad_or_trim = pad_or_trim  # type: ignore[attr-defined]

    import faster_whisper
    import faster_whisper.transcribe as fw_transcribe
    import faster_whisper.vad as fw_vad

    fw_transcribe.decode_audio = decode_audio
    fw_vad.get_speech_timestamps = sherpa_get_speech_timestamps
    fw_transcribe.get_speech_timestamps = sherpa_get_speech_timestamps
    return faster_whisper
