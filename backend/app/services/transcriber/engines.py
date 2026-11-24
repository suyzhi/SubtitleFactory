"""ASR engine dispatchers (Fun-ASR, Qwen MLX, Sherpa, Parakeet, MLX Whisper, faster-whisper)."""

from __future__ import annotations

import sys
import time as time_module
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any, Iterator

from ...utils.task_manager import task_manager
from ..cloud_asr import FUN_ASR_MODEL_ID, create_fun_asr_session
from ..managed_sherpa import create_managed_session
from ..model_catalog import prepare_whisper_model, resolve_local_model
from ..parakeet_transcriber import (
    PARAKEET_MODEL_ID,
    PARAKEET_ONNX_MODEL_ID,
    create_parakeet_session,
)
from ..transcription_models import (
    MANAGED_SHERPA_MODEL_IDS,
    PARAKEET_MODEL_IDS,
    QWEN_ASR_MODEL_IDS,
    WHISPER_MODEL_IDS,
    ModelResolution,
)
from .mlx_whisper import (
    _iter_mlx_whisper_segments,
    _load_pcm16_wav_float32,
    _vad_speech_windows,
)


class TranscriptionError(RuntimeError):
    def __init__(
        self,
        message: str,
        error_code: str,
        *,
        recoverable: bool = True,
        suggestion: str = "请检查音频和模型状态后重试",
    ):
        super().__init__(message)
        self.error_code = error_code
        self.recoverable = recoverable
        self.available_actions = ["retry", "choose_fallback"] if recoverable else []
        self.suggestion = suggestion


@dataclass
class EngineSession:
    segments: Iterator[Any]
    detected_language: str
    audio_duration: float
    device: str
    compute_type: str
    model_label: str
    progress_start: float = 5.0
    detected_lang_box: list[str] | None = None


def _facade_attr(name: str, default: Any) -> Any:
    pkg = sys.modules.get(__package__ or "")
    return getattr(pkg, name, default) if pkg is not None else default


def _from_session(session: Any, *, model_label: str | None = None) -> EngineSession:
    return EngineSession(
        segments=session.segments,
        detected_language=session.detected_language,
        audio_duration=session.audio_duration,
        device=session.device,
        compute_type=session.compute_type,
        model_label=model_label or session.model_label,
        progress_start=session.progress_start,
    )


def _open_mlx_whisper_session(
    task_id: str,
    audio_path: str,
    language: str,
    model_id: str,
    imported: dict | None,
) -> EngineSession:
    import mlx_whisper

    if imported:
        model_path = imported["path"]
    else:
        model_path = resolve_local_model(model_id, "mlx")
        if model_path is None:
            prepare_whisper_model(task_id, model_id, "mlx")
            model_path = resolve_local_model(model_id, "mlx")
        if model_path is None:
            raise TranscriptionError(
                "Apple GPU 模型准备完成后仍无法加载",
                "MODEL_INSTALL_FAILED",
                suggestion="请在模型中心修复此模型的 Apple GPU 格式",
            )
    lang = None if language in ("auto", "") else language
    loaded_wav = _load_pcm16_wav_float32(audio_path)
    audio_duration = loaded_wav[1] if loaded_wav is not None else 0.0
    windows = (
        _vad_speech_windows(task_id, audio_path, audio_duration)
        if loaded_wav is not None and audio_duration > 30.0
        else None
    )
    detected_lang_box = [lang or "auto"]
    segments_gen = _iter_mlx_whisper_segments(
        task_id,
        mlx_whisper,
        str(model_path),
        audio_path,
        lang,
        loaded_wav,
        windows,
        detected_lang_box,
    )
    return EngineSession(
        segments=segments_gen,
        detected_language=detected_lang_box[0],
        audio_duration=audio_duration,
        device="mlx",
        compute_type="MLX",
        model_label=f"MLX Whisper {model_id}",
        progress_start=5.0,
        detected_lang_box=detected_lang_box,
    )


def _open_faster_whisper_session(
    task_id: str,
    audio_path: str,
    language: str,
    model_id: str,
    resolution: ModelResolution,
    resources: ExitStack,
) -> EngineSession:
    from faster_whisper import WhisperModel

    from ..model_pool import cpu_model_pool, model_identity, optimal_cpu_threads

    device, compute_type = "cpu", "int8"
    cpu_threads = optimal_cpu_threads()
    load_target = resolution.load_target
    if model_id in WHISPER_MODEL_IDS and resolution.source != "built_in":
        local_model = resolve_local_model(model_id, "cpu")
        if local_model is None:
            prepare_whisper_model(task_id, model_id, "cpu")
            local_model = resolve_local_model(model_id, "cpu")
        if local_model is None:
            raise TranscriptionError(
                "CPU 模型准备完成后仍无法加载",
                "MODEL_INSTALL_FAILED",
                suggestion="请在模型中心修复此模型的 CPU 格式",
            )
        load_target = str(local_model)
    load_started = time_module.perf_counter()
    model, reused = resources.enter_context(
        cpu_model_pool.lease(
            (model_identity(str(load_target), device, compute_type), cpu_threads),
            lambda: WhisperModel(
                load_target,
                device=device,
                compute_type=compute_type,
                cpu_threads=cpu_threads,
            ),
        )
    )
    task_manager.update_task(
        task_id,
        details={
            "model_cache_hit": reused,
            "model_load_seconds": round(time_module.perf_counter() - load_started, 3),
        },
    )
    task_manager.checkpoint(task_id)

    lang = None if language in ("auto", "") else language
    loaded_wav = _load_pcm16_wav_float32(audio_path)
    audio_input = loaded_wav[0] if loaded_wav is not None else audio_path
    segments_gen, info = model.transcribe(
        audio_input,
        language=lang,
        beam_size=5,
        best_of=2,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        word_timestamps=True,
    )
    return EngineSession(
        segments=segments_gen,
        detected_language=info.language,
        audio_duration=info.duration,
        device=device,
        compute_type=compute_type,
        model_label=f"faster-whisper {model_id}",
        progress_start=5.0,
    )


def open_engine_session(
    *,
    task_id: str,
    audio_path: str,
    language: str,
    model_id: str,
    runtime: str,
    resolution: ModelResolution,
    app_settings: dict,
    imported: dict | None,
    resources: ExitStack,
) -> EngineSession:
    parakeet_session_fn = _facade_attr("create_parakeet_session", create_parakeet_session)

    if model_id == FUN_ASR_MODEL_ID:
        return _from_session(create_fun_asr_session(task_id, audio_path, language))
    if model_id in QWEN_ASR_MODEL_IDS and runtime == "mlx":
        from ..qwen_mlx import create_qwen_mlx_session

        return _from_session(create_qwen_mlx_session(task_id, audio_path, language, model_id))
    if model_id in MANAGED_SHERPA_MODEL_IDS:
        return _from_session(
            create_managed_session(
                task_id, audio_path, language, model_id, runtime or "cpu", resources=resources
            )
        )
    if imported and imported["format"] == "memo-coreml":
        session = parakeet_session_fn(
            task_id,
            audio_path,
            language,
            PARAKEET_MODEL_ID,
            coreml_model_dir=imported["path"],
            coreml_cli_path=imported.get("cli_path"),
            runtime="external_coreml",
        )
        return _from_session(session, model_label=imported["display_name"])
    if imported and imported["format"] == "sherpa-onnx":
        session = parakeet_session_fn(
            task_id,
            audio_path,
            language,
            PARAKEET_ONNX_MODEL_ID,
            runtime=runtime,
            onnx_model_dir=imported["path"],
            resources=resources,
        )
        return _from_session(session, model_label=imported["display_name"])
    if model_id in PARAKEET_MODEL_IDS:
        session = parakeet_session_fn(
            task_id,
            audio_path,
            language,
            model_id,
            coreml_model_dir=app_settings.get("coreml_model_path"),
            coreml_cli_path=app_settings.get("coreml_cli_path"),
            runtime=runtime,
            resources=resources,
        )
        return _from_session(session)
    if runtime == "mlx" or (imported and imported["format"] == "mlx"):
        return _open_mlx_whisper_session(task_id, audio_path, language, model_id, imported)
    return _open_faster_whisper_session(task_id, audio_path, language, model_id, resolution, resources)
