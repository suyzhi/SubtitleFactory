"""
字幕工厂 - 语音转写服务包入口

按职责拆分为：
- mlx_whisper: MLX Whisper 音频快速读取、Silero VAD 分窗与迭代
- engines: 各 ASR 推理引擎会话派发（Fun-ASR、Qwen MLX、Sherpa、Parakeet、Whisper）
- pipeline: 两阶段增量转写（流式草稿落盘 + 原子后处理发布）
"""

from __future__ import annotations

from ..cloud_asr import FUN_ASR_MODEL_ID, FUN_ASR_RUNTIME
from ..parakeet_transcriber import (
    PARAKEET_MODEL_ID,
    PARAKEET_ONNX_MODEL_ID,
    PARAKEET_SUPPORTED_LANGUAGES,
    create_parakeet_session,
    get_parakeet_model_status,
)
from ..transcription_models import (
    MANAGED_SHERPA_MODEL_IDS,
    PARAKEET_MODEL_IDS,
    QWEN_ASR_MODEL_IDS,
    SAFE_TRANSCRIPTION_MODEL,
    SUPPORTED_TRANSCRIPTION_MODELS,
    WHISPER_MODEL_IDS,
    ModelResolution,
    _bundled_whisper_model,
    _managed_whisper_model_ready,
    get_transcription_model_status,
    resolve_transcription_model,
    validate_whisper_model_path,
)
from ..transcription_postprocess import (
    _allocate_piece_times,
    _decode_timings,
    _fmt_time,
    _has_sentence_end,
    _max_chars_for_text,
    _post_process_segments,
    _segment_word_timings,
    _smart_merge,
    _split_text_into_pieces,
)
from .engines import EngineSession, TranscriptionError, open_engine_session
from .mlx_whisper import (
    _clear_mlx_metal_cache,
    _iter_mlx_whisper_segments,
    _load_pcm16_wav_float32,
    _shift_mlx_segments,
    _vad_speech_windows,
)
from .pipeline import LOG_THROTTLE_INTERVAL, _transcribe_audio, logger, transcribe_audio

__all__ = [
    "EngineSession",
    "FUN_ASR_MODEL_ID",
    "FUN_ASR_RUNTIME",
    "LOG_THROTTLE_INTERVAL",
    "MANAGED_SHERPA_MODEL_IDS",
    "ModelResolution",
    "PARAKEET_MODEL_ID",
    "PARAKEET_MODEL_IDS",
    "PARAKEET_ONNX_MODEL_ID",
    "PARAKEET_SUPPORTED_LANGUAGES",
    "QWEN_ASR_MODEL_IDS",
    "SAFE_TRANSCRIPTION_MODEL",
    "SUPPORTED_TRANSCRIPTION_MODELS",
    "TranscriptionError",
    "WHISPER_MODEL_IDS",
    "_allocate_piece_times",
    "_bundled_whisper_model",
    "_clear_mlx_metal_cache",
    "_decode_timings",
    "_fmt_time",
    "_has_sentence_end",
    "_iter_mlx_whisper_segments",
    "_load_pcm16_wav_float32",
    "_managed_whisper_model_ready",
    "_max_chars_for_text",
    "_post_process_segments",
    "_segment_word_timings",
    "_shift_mlx_segments",
    "_smart_merge",
    "_split_text_into_pieces",
    "_transcribe_audio",
    "_vad_speech_windows",
    "create_parakeet_session",
    "get_parakeet_model_status",
    "get_transcription_model_status",
    "logger",
    "open_engine_session",
    "resolve_transcription_model",
    "transcribe_audio",
    "validate_whisper_model_path",
]
