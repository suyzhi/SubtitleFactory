"""Qwen3-ASR inference on Apple Silicon through the MLX Metal backend."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Iterator

from ..utils.task_manager import task_manager
from .managed_sherpa import (
    _ensure_vad,
    _wave_info,
    iter_vad_audio_segments,
    prefetch_vad_segments,
)
from .model_catalog import (
    QWEN_ASR_CATALOG_BY_ID,
    prepare_catalog_model,
    resolve_local_model,
)

_PUNCTUATION_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)
_EOS_TOKEN_IDS = frozenset({151643, 151645})
_MASK_NEG_VALUE = -1e4
_DEFAULT_MAX_BATCH_AUDIO_SECONDS = 45.0
_CACHE_CLEAR_BATCH_INTERVAL = 8
_CHECKPOINT_STEP_INTERVAL = 16


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


def _clear_mlx_cache() -> None:
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


def _resolve_batch_size(model_id: str | None = None) -> int:
    raw = os.getenv("SUBTITLE_FACTORY_QWEN_BATCH_SIZE", "").strip()
    if raw:
        try:
            return max(1, min(16, int(raw)))
        except ValueError:
            pass
    if model_id and "1.7b" in model_id.lower():
        return 6
    return 8


def _load_fast_mlx_components() -> dict[str, Any] | None:
    """Return mlx_qwen3_asr internals when using the real package (not a unit-test stub)."""
    try:
        import mlx_qwen3_asr

        transcribe_fn = getattr(mlx_qwen3_asr, "transcribe", None)
        if not getattr(transcribe_fn, "__module__", "").startswith("mlx_qwen3_asr"):
            return None
        import mlx.core as mx
        import numpy as np
        from mlx_qwen3_asr.audio import compute_features
        from mlx_qwen3_asr.generate import detect_repetition, resolve_max_new_tokens
        from mlx_qwen3_asr.load_models import _ModelHolder
        from mlx_qwen3_asr.tokenizer import (
            _TokenizerHolder,
            canonicalize_language,
            parse_asr_output,
        )
    except Exception:
        return None
    return {
        "mx": mx,
        "np": np,
        "compute_features": compute_features,
        "detect_repetition": detect_repetition,
        "resolve_max_new_tokens": resolve_max_new_tokens,
        "model_holder": _ModelHolder,
        "tokenizer_holder": _TokenizerHolder,
        "canonicalize_language": canonicalize_language,
        "parse_asr_output": parse_asr_output,
    }


def _decode_qwen_batch(
    task_id: str,
    model: Any,
    tokenizer: Any,
    batch_samples: list[Any],
    forced_language: str | None,
    components: dict[str, Any],
) -> list[str]:
    """Decode a batch of VAD speech spans in float16 with async_eval pipelining.

    Casting audio_features to float16 prevents mlx_qwen3_asr's float32
    SinusoidalPositionEmbedding from silently promoting the text decoder and KV
    cache to float32, and batching amortizes memory-bandwidth-bound weight reads
    across multiple VAD spans simultaneously.
    """
    mx = components["mx"]
    np = components["np"]
    compute_features = components["compute_features"]
    resolve_max_new_tokens = components["resolve_max_new_tokens"]
    detect_repetition = components["detect_repetition"]
    parse_asr_output = components["parse_asr_output"]

    dtype = mx.float16
    batch_size = len(batch_samples)
    embeds_list: list[Any] = []
    prompt_lens: list[int] = []
    max_new_list: list[int] = []

    for samples in batch_samples:
        task_manager.checkpoint(task_id)
        mel, feature_lens = compute_features(samples)
        audio_features, _ = model.audio_tower(mel.astype(dtype), feature_lens)
        audio_features = audio_features.astype(dtype)
        prompt_tokens = tokenizer.build_prompt_tokens(
            n_audio_tokens=audio_features.shape[1],
            language=forced_language,
            context="",
        )
        ids = mx.array([prompt_tokens], dtype=mx.int32)
        embeds = model._embed_tokens(ids, validate_input_ids=False)
        embeds = model._inject_audio_features(
            embeds, audio_features, ids == model.audio_token_id
        ).astype(dtype)
        embeds_list.append(embeds[0])
        prompt_lens.append(len(prompt_tokens))
        max_new_list.append(
            resolve_max_new_tokens(None, audio_duration_sec=len(samples) / 16_000.0)
        )

    max_prompt = max(prompt_lens)
    max_new = max(max_new_list)
    hidden_dim = embeds_list[0].shape[-1]
    pad_lens = [max_prompt - length for length in prompt_lens]
    has_pad = any(pad > 0 for pad in pad_lens)

    padded_embeds: list[Any] = []
    pos_rows: list[Any] = []
    for emb, length, pad in zip(embeds_list, prompt_lens, pad_lens, strict=True):
        if pad > 0:
            padded_embeds.append(
                mx.concatenate([mx.zeros((pad, hidden_dim), dtype=dtype), emb], axis=0)
            )
            pos_rows.append(
                np.concatenate(
                    [np.zeros(pad, dtype=np.int32), np.arange(length, dtype=np.int32)]
                )
            )
        else:
            padded_embeds.append(emb)
            pos_rows.append(np.arange(length, dtype=np.int32))

    batch_embeds = mx.stack(padded_embeds, axis=0)
    pos_arr = mx.array(np.stack(pos_rows, axis=0), dtype=mx.int32)
    pos_3d = mx.stack([pos_arr, pos_arr, pos_arr], axis=1)

    causal = np.triu(
        np.full((max_prompt, max_prompt), _MASK_NEG_VALUE, dtype=np.float16), k=1
    )
    prefill_mask = np.broadcast_to(
        causal[None, None, :, :], (batch_size, 1, max_prompt, max_prompt)
    ).copy()
    if has_pad:
        for idx, pad in enumerate(pad_lens):
            if pad > 0:
                prefill_mask[idx, 0, :, :pad] = _MASK_NEG_VALUE
                # Keep padded query diagonals unmasked so softmax never yields NaN.
                diag = np.arange(pad)
                prefill_mask[idx, 0, diag, diag] = 0.0
    prefill_mask_mx = mx.array(prefill_mask, dtype=dtype)

    cache = model.create_cache(max_seq_len=max_prompt + max_new)
    hidden = model.model(
        inputs_embeds=batch_embeds,
        position_ids=pos_3d,
        attention_mask=prefill_mask_mx,
        cache=cache,
    )
    logits = model.lm_head(hidden[:, -1:, :])
    prev_tok = mx.argmax(logits[:, -1, :], axis=-1)
    mx.async_eval(prev_tok)

    if has_pad:
        full_pad_mask = np.zeros(
            (batch_size, 1, 1, max_prompt + max_new), dtype=np.float16
        )
        for idx, pad in enumerate(pad_lens):
            if pad > 0:
                full_pad_mask[idx, 0, 0, :pad] = _MASK_NEG_VALUE
        full_pad_mask_mx: Any = mx.array(full_pad_mask, dtype=dtype)
    else:
        full_pad_mask_mx = None

    generated: list[list[int]] = [[] for _ in range(batch_size)]
    finished = [False] * batch_size
    base_pos = mx.array(prompt_lens, dtype=mx.int32)

    for step in range(1, max_new + 1):
        if step % _CHECKPOINT_STEP_INTERVAL == 0:
            task_manager.checkpoint(task_id)
        if step < max_new:
            cur_pos = base_pos + (step - 1)
            step_pos = mx.stack([cur_pos, cur_pos, cur_pos], axis=1)[:, :, None]
            step_mask = (
                full_pad_mask_mx[:, :, :, : max_prompt + step] if has_pad else None
            )
            step_hidden = model.model(
                input_ids=prev_tok[:, None],
                position_ids=step_pos,
                attention_mask=step_mask,
                cache=cache,
            )
            step_logits = model.lm_head(step_hidden[:, -1:, :])
            cand_tok: Any = mx.argmax(step_logits[:, -1, :], axis=-1)
            mx.async_eval(cand_tok)
        else:
            cand_tok = None

        tok_vals = prev_tok.tolist()
        all_done = True
        for idx, token_id in enumerate(tok_vals):
            if not finished[idx]:
                if token_id in _EOS_TOKEN_IDS:
                    finished[idx] = True
                else:
                    generated[idx].append(int(token_id))
                    if len(generated[idx]) >= max_new_list[idx] or detect_repetition(
                        generated[idx]
                    ):
                        finished[idx] = True
            if not finished[idx]:
                all_done = False

        if all_done or cand_tok is None:
            break
        prev_tok = cand_tok

    outputs: list[str] = []
    for tokens in generated:
        raw_text = tokenizer.decode(tokens)
        _lang, parsed_text = parse_asr_output(raw_text, user_language=forced_language)
        outputs.append(str(parsed_text or ""))
    return outputs


def _iter_segments(
    task_id: str,
    audio_path: str,
    model_path: str,
    language: str,
    vad_path,
    audio_duration: float,
    *,
    max_speech_seconds: float = 20.0,
    model_id: str | None = None,
) -> Iterator[QwenMlxSegment]:
    if not 5 <= max_speech_seconds <= 30:
        raise ValueError("Qwen 实验切段长度必须在 5 到 30 秒之间")

    forced_language = None if language in {"", "auto"} else language
    vad_iter = iter_vad_audio_segments(
        task_id,
        audio_path,
        vad_path,
        audio_duration,
        max_speech_seconds=max_speech_seconds,
    )
    components = _load_fast_mlx_components()
    if components is None:
        from mlx_qwen3_asr import transcribe

        for start, end, samples in vad_iter:
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
        return

    mx = components["mx"]
    model, _config = components["model_holder"].get(model_path, dtype=mx.float16)
    resolved_path = components["model_holder"].get_resolved_path(
        model_path, dtype=mx.float16
    )
    tokenizer = components["tokenizer_holder"].get(resolved_path)
    canonical_lang = (
        components["canonicalize_language"](forced_language)
        if forced_language
        else None
    )
    max_batch_size = _resolve_batch_size(model_id)
    pending: list[tuple[float, float, Any]] = []
    pending_audio_sec = 0.0
    batch_count = 0

    def _flush(items: list[tuple[float, float, Any]]) -> list[QwenMlxSegment]:
        nonlocal batch_count
        texts = _decode_qwen_batch(
            task_id,
            model,
            tokenizer,
            [samples for _, _, samples in items],
            canonical_lang,
            components,
        )
        batch_count += 1
        if batch_count % _CACHE_CLEAR_BATCH_INTERVAL == 0:
            _clear_mlx_cache()
        segments: list[QwenMlxSegment] = []
        for (seg_start, seg_end, _samples), raw_text in zip(items, texts, strict=True):
            cleaned = re.sub(r"\s+", " ", raw_text).strip()
            if not cleaned or _PUNCTUATION_ONLY.fullmatch(cleaned):
                continue
            segments.append(QwenMlxSegment(start=seg_start, end=seg_end, text=cleaned))
        return segments

    try:
        for start, end, samples in prefetch_vad_segments(
            vad_iter, thread_name="qwen-vad-prefetch"
        ):
            task_manager.checkpoint(task_id)
            seg_dur = len(samples) / 16_000.0
            if pending and (
                len(pending) >= max_batch_size
                or pending_audio_sec + seg_dur > _DEFAULT_MAX_BATCH_AUDIO_SECONDS
            ):
                yield from _flush(pending)
                pending = []
                pending_audio_sec = 0.0
            pending.append((start, end, samples))
            pending_audio_sec += seg_dur
            if (
                len(pending) >= max_batch_size
                or pending_audio_sec >= _DEFAULT_MAX_BATCH_AUDIO_SECONDS
            ):
                yield from _flush(pending)
                pending = []
                pending_audio_sec = 0.0
        if pending:
            yield from _flush(pending)
    finally:
        _clear_mlx_cache()


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
            model_id=model_id,
        ),
        audio_duration=audio_duration,
        detected_language=normalized or "auto",
        device="Apple GPU (Metal)",
        compute_type="MLX float16",
        model_label=f"{definition.name} · MLX",
    )
