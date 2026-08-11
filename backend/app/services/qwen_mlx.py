"""Qwen3-ASR inference on Apple Silicon through the MLX Metal backend."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator

from ..utils.task_manager import task_manager
from .managed_sherpa import _ensure_vad, _wave_info, iter_vad_audio_segments
from .model_catalog import (
    QWEN_ASR_CATALOG_BY_ID,
    prepare_catalog_model,
    resolve_local_model,
)


_PUNCTUATION_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)


class QwenMlxError(RuntimeError):
    def __init__(
        self,
        message: str,
        error_code: str,
        suggestion: str,
        *,
        recoverable: bool = True,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.suggestion = suggestion
        self.recoverable = recoverable
        self.available_actions = ["retry", "repair"] if recoverable else []


@dataclass(frozen=True)
class QwenMlxSegment:
    start: float
    end: float
    text: str
    words: tuple[dict, ...] = ()


@dataclass(frozen=True)
class QwenMlxSession:
    segments: Iterator[QwenMlxSegment]
    audio_duration: float
    detected_language: str
    device: str
    compute_type: str
    model_label: str
    progress_start: float = 24.0


def _iter_segments(
    task_id: str,
    audio_path: str,
    model_path: str,
    language: str,
    vad_path,
    audio_duration: float,
) -> Iterator[QwenMlxSegment]:
    from mlx_qwen3_asr import transcribe

    forced_language = None if language in {"", "auto"} else language
    for start, end, samples in iter_vad_audio_segments(
        task_id,
        audio_path,
        vad_path,
        audio_duration,
        max_speech_seconds=20.0,
    ):
        task_manager.checkpoint(task_id)
        result: Any = transcribe(
            (samples, 16_000),
            model=model_path,
            language=forced_language,
            on_progress=lambda _event: task_manager.checkpoint(task_id),
        )
        task_manager.checkpoint(task_id)
        text = re.sub(r"\s+", " ", str(getattr(result, "text", "") or "")).strip()
        if not text or _PUNCTUATION_ONLY.fullmatch(text):
            continue
        yield QwenMlxSegment(start=start, end=end, text=text)


def create_qwen_mlx_session(
    task_id: str,
    audio_path: str,
    language: str,
    model_id: str,
) -> QwenMlxSession:
    definition = QWEN_ASR_CATALOG_BY_ID.get(model_id)
    if not definition:
        raise QwenMlxError(
            "不支持的 Qwen3-ASR 模型",
            "MODEL_NOT_SUPPORTED",
            "请重新选择模型",
            recoverable=False,
        )
    try:
        import mlx_qwen3_asr  # noqa: F401
    except Exception as exc:
        raise QwenMlxError(
            f"Qwen3-ASR Apple GPU 运行时不可用：{exc}",
            "MODEL_RUNTIME_MISSING",
            "请修复 App 运行包或改用其他模型",
        ) from exc

    model_path = resolve_local_model(model_id, "mlx")
    if model_path is None:
        prepare_catalog_model(task_id, model_id, "mlx")
        model_path = resolve_local_model(model_id, "mlx")
    if model_path is None:
        raise QwenMlxError(
            "Qwen3-ASR 模型准备完成后仍无法加载",
            "MODEL_INSTALL_FAILED",
            "请在模型中心修复 Apple GPU 格式",
        )
    try:
        audio_duration, sample_rate = _wave_info(audio_path)
        if sample_rate != 16_000:
            raise QwenMlxError(
                "Qwen3-ASR 字幕模式需要 16kHz 音频",
                "AUDIO_FORMAT",
                "请重新提取音频",
            )
        vad_path = _ensure_vad(task_id)
    except QwenMlxError:
        raise
    except Exception as exc:
        if hasattr(exc, "error_code"):
            raise
        raise QwenMlxError(
            f"无法准备 Qwen3-ASR 音频：{exc}",
            "AUDIO_INVALID",
            "请重新提取音频",
        ) from exc

    task_manager.update_task(
        task_id,
        step="loading_model",
        progress=24,
        message=f"正在用 Apple GPU 加载 {definition.name}...",
    )
    normalized = (language or "auto").lower()
    return QwenMlxSession(
        segments=_iter_segments(
            task_id,
            audio_path,
            str(model_path),
            normalized,
            vad_path,
            audio_duration,
        ),
        audio_duration=audio_duration,
        detected_language=normalized or "auto",
        device="Apple GPU (Metal)",
        compute_type="MLX float16",
        model_label=f"{definition.name} · MLX",
    )
