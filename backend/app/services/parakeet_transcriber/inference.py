"""Parakeet ONNX (sherpa-onnx + Silero VAD) and Core ML transcription sessions."""

from __future__ import annotations

import os
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from ...utils.config import environment_path_overrides_enabled
from ...utils.task_manager import TaskCancelled, task_manager
from .assets import ensure_parakeet_assets, ensure_parakeet_coreml_model
from .constants import (
    _PUNCTUATION_ONLY,
    PARAKEET_COREML_DISPLAY_NAME,
    PARAKEET_MODEL_ID,
    PARAKEET_ONNX_MODEL_ID,
    PARAKEET_SUPPORTED_LANGUAGES,
    CoreMLRuntime,
    ParakeetSegment,
    ParakeetSession,
    facade_attr,
)
from .coreml import (
    _redact_runtime_paths,
    _run_coreml_cli,
    _segments_from_coreml_json,
    bundled_coreml_cli,
    discover_coreml_runtime,
)


def _import_sherpa_onnx() -> Any:
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise RuntimeError(
            "缺少 Parakeet 推理组件 sherpa-onnx；请重新安装应用或运行 pip install -r backend/requirements.txt"
        ) from exc
    return sherpa_onnx


def _wave_info(audio_path: str) -> tuple[float, int, int]:
    try:
        with wave.open(audio_path, "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frame_count = audio.getnframes()
            compression = audio.getcomptype()
    except (OSError, wave.Error) as exc:
        raise RuntimeError(f"无法读取待转写 WAV 音频：{exc}") from exc
    if channels != 1 or sample_width != 2 or compression != "NONE":
        raise RuntimeError("Parakeet 需要 16-bit PCM 单声道 WAV，请重新提取音频")
    if sample_rate != 16000:
        raise RuntimeError("Parakeet 字幕模式需要 16kHz 音频，请重新提取音频")
    return frame_count / max(sample_rate, 1), sample_rate, frame_count


def _iter_vad_segments(
    task_id: str,
    audio_path: str,
    recognizer: Any,
    vad_path: Path,
    sherpa_onnx: Any,
    audio_duration: float,
) -> Iterator[ParakeetSegment]:
    """Follow sherpa-onnx's official VAD subtitle flow, yielding incrementally."""
    import numpy as np

    tm = facade_attr("task_manager", task_manager)
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(vad_path)
    config.silero_vad.threshold = 0.2
    config.silero_vad.min_silence_duration = 0.25
    config.silero_vad.min_speech_duration = 0.25
    config.silero_vad.max_speech_duration = 8.0
    config.sample_rate = 16000
    window_size = int(config.silero_vad.window_size)
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=100)

    pending = np.empty(0, dtype=np.float32)
    with wave.open(audio_path, "rb") as audio:
        frames_per_read = 16000 * 2
        while True:
            tm.checkpoint(task_id)
            raw = audio.readframes(frames_per_read)
            is_eof = not raw
            if raw:
                samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                pending = np.concatenate((pending, samples))
                while pending.size >= window_size:
                    vad.accept_waveform(pending[:window_size])
                    pending = pending[window_size:]
            else:
                if pending.size:
                    padded = np.zeros(window_size, dtype=np.float32)
                    padded[: pending.size] = pending
                    vad.accept_waveform(padded)
                    pending = np.empty(0, dtype=np.float32)
                vad.flush()

            while not vad.empty():
                tm.checkpoint(task_id)
                speech = vad.front
                start = float(speech.start) / 16000.0
                speech_samples = np.asarray(speech.samples, dtype=np.float32).copy()
                vad.pop()

                stream = recognizer.create_stream()
                stream.accept_waveform(16000, speech_samples)
                recognizer.decode_stream(stream)
                tm.checkpoint(task_id)
                text = str(stream.result.text or "").strip()
                if not text or _PUNCTUATION_ONLY.fullmatch(text):
                    continue
                end = min(audio_duration, start + speech_samples.size / 16000.0)
                if end > start:
                    yield ParakeetSegment(start=start, end=end, text=text)

            if is_eof:
                break


def _create_onnx_session(
    task_id: str,
    audio_path: str,
    normalized_language: str,
    provider: str = "cpu",
    model_dir: str | Path | None = None,
) -> ParakeetSession:
    tm = facade_attr("task_manager", task_manager)
    ensure_assets_fn = facade_attr("ensure_parakeet_assets", ensure_parakeet_assets)
    import_sherpa_fn = facade_attr("_import_sherpa_onnx", _import_sherpa_onnx)
    if model_dir:
        root = Path(model_dir).expanduser().resolve()
        assets = SimpleNamespace(
            encoder=root / "encoder.int8.onnx",
            decoder=root / "decoder.int8.onnx",
            joiner=root / "joiner.int8.onnx",
            tokens=root / "tokens.txt",
            vad=root / "silero_vad.onnx",
        )
        missing = [
            item.name
            for item in (assets.encoder, assets.decoder, assets.joiner, assets.tokens)
            if not item.is_file()
        ]
        if missing:
            raise ValueError(f"导入的 Parakeet ONNX 模型缺少文件：{', '.join(missing)}")
    else:
        assets = ensure_assets_fn(task_id)
    tm.checkpoint(task_id)
    tm.update_task(
        task_id,
        step="loading_model",
        progress=24,
        message="正在加载 Parakeet int8 ONNX 模型...",
    )
    try:
        sherpa_onnx = import_sherpa_fn()
        thread_default = max(1, min(4, os.cpu_count() or 2))
        num_threads = max(1, int(os.getenv("PARAKEET_NUM_THREADS", str(thread_default))))
        recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(assets.encoder),
            decoder=str(assets.decoder),
            joiner=str(assets.joiner),
            tokens=str(assets.tokens),
            model_type="nemo_transducer",
            num_threads=num_threads,
            provider=provider,
            decoding_method="greedy_search",
            sample_rate=16000,
            feature_dim=80,
            debug=False,
        )
        audio_duration, _, _ = _wave_info(audio_path)
    except TaskCancelled:
        raise
    except Exception as exc:
        tm.update_task(
            task_id,
            step="model_error",
            message="Parakeet 推理引擎加载失败",
        )
        tm.add_log(
            task_id,
            "error",
            "Parakeet 模型",
            "推理引擎加载失败",
            detail=str(exc),
            suggestion="请重新安装应用；若缓存损坏，可删除数据目录中的 models 后重试",
        )
        raise RuntimeError(f"Parakeet 推理引擎加载失败：{exc}") from exc

    segments = _iter_vad_segments(
        task_id,
        audio_path,
        recognizer,
        assets.vad,
        sherpa_onnx,
        audio_duration,
    )
    detected_language = normalized_language if normalized_language != "auto" else "auto"
    return ParakeetSession(
        segments=segments,
        audio_duration=audio_duration,
        detected_language=detected_language,
        device="Core ML" if provider == "coreml" else "cpu",
    )


def _create_coreml_session(
    task_id: str,
    audio_path: str,
    normalized_language: str,
    runtime: CoreMLRuntime,
) -> ParakeetSession:
    tm = facade_attr("task_manager", task_manager)
    run_cli_fn = facade_attr("_run_coreml_cli", _run_coreml_cli)
    tm.update_task(
        task_id,
        step="loading_model",
        progress=3,
        message="正在加载 Parakeet Core ML 模型...",
        details={
            "coreml": {
                "status": "ready",
                "source": runtime.source,
            },
            "model_download": {
                "status": "external_runtime",
                "model_id": PARAKEET_MODEL_ID,
            },
        },
    )
    tm.add_log(
        task_id,
        "info",
        "Parakeet Core ML",
        "已加载 Core ML 模型与转写组件",
    )
    try:
        payload = run_cli_fn(task_id, audio_path, runtime)
        segments, audio_duration = _segments_from_coreml_json(payload)
        if audio_duration <= 0:
            audio_duration, _, _ = _wave_info(audio_path)
    except TaskCancelled:
        raise
    except Exception as exc:
        safe_error = _redact_runtime_paths(str(exc), runtime)
        tm.update_task(
            task_id,
            step="model_error",
            message="Parakeet Core ML 转写失败",
            details={"coreml": {"status": "error", "error": safe_error}},
        )
        tm.add_log(
            task_id,
            "error",
            "Parakeet Core ML",
            "本地 Core ML 转写失败",
            detail=safe_error,
            suggestion="请重新选择完整的 Core ML 模型目录与兼容当前架构的 CLI",
        )
        raise RuntimeError(f"Parakeet Core ML 转写失败：{safe_error}") from exc

    tm.update_task(
        task_id,
        step="transcribing",
        progress=82,
        message=f"Parakeet Core ML 识别完成，正在导入 {len(segments)} 条时间轴字幕...",
        details={
            "coreml": {
                "status": "success",
                "progress": 100,
                "segments": len(segments),
                "source": runtime.source,
            }
        },
    )
    detected_language = normalized_language if normalized_language != "auto" else "auto"
    return ParakeetSession(
        segments=iter(segments),
        audio_duration=audio_duration,
        detected_language=detected_language,
        device="Core ML",
        compute_type="Core ML",
        model_label=PARAKEET_COREML_DISPLAY_NAME,
        progress_start=82.0,
    )


def create_parakeet_session(
    task_id: str,
    audio_path: str,
    language: str = "auto",
    model_id: str = PARAKEET_MODEL_ID,
    coreml_model_dir: str | Path | None = None,
    coreml_cli_path: str | Path | None = None,
    runtime: str | None = None,
    onnx_model_dir: str | Path | None = None,
) -> ParakeetSession:
    discover_fn = facade_attr("discover_coreml_runtime", discover_coreml_runtime)
    bundled_cli_fn = facade_attr("bundled_coreml_cli", bundled_coreml_cli)
    ensure_coreml_fn = facade_attr("ensure_parakeet_coreml_model", ensure_parakeet_coreml_model)
    create_coreml_fn = facade_attr("_create_coreml_session", _create_coreml_session)
    create_onnx_fn = facade_attr("_create_onnx_session", _create_onnx_session)

    normalized_language = (language or "auto").lower()
    if normalized_language != "auto" and normalized_language not in PARAKEET_SUPPORTED_LANGUAGES:
        raise ValueError(
            "Parakeet TDT v3 不支持所选源语言；请选择自动检测或其支持的欧洲语言（例如英语）"
        )
    if model_id not in {PARAKEET_MODEL_ID, PARAKEET_ONNX_MODEL_ID}:
        raise ValueError(f"不支持的 Parakeet 模型：{model_id}")

    if model_id == PARAKEET_MODEL_ID:
        coreml_runtime = discover_fn(
            coreml_model_dir,
            coreml_cli_path,
            strict=False,
            allow_environment=(
                coreml_model_dir is None
                and coreml_cli_path is None
                and environment_path_overrides_enabled()
            ),
        )
        if coreml_runtime is None and coreml_model_dir is None and bundled_cli_fn() is not None:
            ensure_coreml_fn(task_id)
            coreml_runtime = discover_fn(
                None, coreml_cli_path, strict=False, allow_environment=False
            )
        if coreml_runtime is None:
            error = RuntimeError("Parakeet Core ML 模型或转写组件当前不可用")
            error.error_code = "MODEL_RUNTIME_MISSING"  # type: ignore[attr-defined]
            error.recoverable = True  # type: ignore[attr-defined]
            error.available_actions = ["choose_fallback", "open_settings"]  # type: ignore[attr-defined]
            error.suggestion = (  # type: ignore[attr-defined]
                "请在设置中下载 Parakeet Core ML 模型，或改用 Parakeet V3 ONNX / Whisper"
            )
            raise error
        return create_coreml_fn(task_id, audio_path, normalized_language, coreml_runtime)
    return create_onnx_fn(
        task_id,
        audio_path,
        normalized_language,
        "coreml" if runtime == "coreml" else "cpu",
        onnx_model_dir,
    )
