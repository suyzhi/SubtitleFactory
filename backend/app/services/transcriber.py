"""
字幕工厂 - 语音转写服务（faster-whisper / Parakeet）增量版

将音频转写为带时间戳的字幕段。

两阶段设计：
  阶段 1：实时转写。模型 segment 逐条写入数据库，标记 is_draft=1。
  阶段 2：最终后处理。读取全部 draft，执行合并/拆分/时长修正，替换为 final。

重要：不要 list(segments_generator) 阻塞等待所有 segment 生成完毕。
"""

import json
import logging
import time as time_module
import uuid
import wave
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from ..models.database import get_db
from ..utils.config import MAX_DURATION, MIN_DURATION, MODELS_DIR, WHISPER_MODEL
from ..utils.task_manager import task_manager
from .cloud_asr import (
    FUN_ASR_MODEL_ID,
    FUN_ASR_RUNTIME,
    create_fun_asr_session,
)
from .managed_sherpa import create_managed_session
from .model_catalog import (
    prepare_whisper_model,
    resolve_local_model,
)
from .parakeet_transcriber import (
    PARAKEET_MODEL_ID,
    PARAKEET_ONNX_MODEL_ID,
    PARAKEET_SUPPORTED_LANGUAGES,
    create_parakeet_session,
    get_parakeet_model_status,
)
from .transcription_models import (
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
from .transcription_postprocess import (
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

logger = logging.getLogger(__name__)

# 日志节流：每 N 条字幕写一次日志
LOG_THROTTLE_INTERVAL = 5

__all__ = [
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
    "_decode_timings",
    "_fmt_time",
    "_has_sentence_end",
    "_managed_whisper_model_ready",
    "_max_chars_for_text",
    "_post_process_segments",
    "_segment_word_timings",
    "_smart_merge",
    "_split_text_into_pieces",
    "create_parakeet_session",
    "get_parakeet_model_status",
    "get_transcription_model_status",
    "resolve_transcription_model",
    "transcribe_audio",
    "validate_whisper_model_path",
]


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
            from .managed_sherpa import _ensure_vad

            vad_path = _ensure_vad(task_id)
        except Exception:
            return None
    try:
        from .managed_sherpa import iter_vad_audio_segments

        windows: list[tuple[float, float]] = []
        win_start: float | None = None
        win_end: float = 0.0
        for start, end, _samples in iter_vad_audio_segments(
            task_id, audio_path, vad_path, audio_duration, max_speech_seconds=max_window_seconds
        ):
            if win_start is None:
                win_start, win_end = start, end
            elif (
                end - win_start <= max_window_seconds
                and start - win_end <= max_gap_seconds
            ):
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
        _clear_mlx_metal_cache()


def transcribe_audio(task_id: str, audio_path: str, project_id: str, language: str = "auto", model_size: str | None = None, runtime: str | None = None):
    # Workflow preparation does not occupy the inference slot. Standalone
    # transcription already holds it; resource_slot is reentrant in that case.
    with task_manager.resource_slot(task_id, "ml"), ExitStack() as resources:
        return _transcribe_audio(task_id, audio_path, project_id, language, model_size, runtime, resources)


def _transcribe_audio(task_id, audio_path, project_id, language, model_size, runtime, resources):
    """
    转写音频文件，生成字幕段（segments）。
    增量写入：每生成一个 segment 立即写数据库，前端可实时拉取。
    """
    task_manager.update_task(task_id, step="loading_model", progress=2, message="正在准备转写模型...")
    task_manager.add_log(task_id, "info", "语音转写", "正在准备转写模型...")

    try:
        from .app_settings import get_effective_app_settings as get_app_settings
        app_settings = get_app_settings()
    except Exception:
        app_settings = {}
    resolution = resolve_transcription_model(
        model_size or "auto",
        language,
        default_model=app_settings.get("default_model") or WHISPER_MODEL,
        custom_model_path=app_settings.get("custom_model_path"),
        coreml_model_path=app_settings.get("coreml_model_path"),
        coreml_cli_path=app_settings.get("coreml_cli_path"),
    )
    if resolution.fell_back and model_size and model_size != "auto":
        raise TranscriptionError("所选模型不可用，未改用其他模型", "MODEL_UNAVAILABLE", suggestion=resolution.fallback_reason)
    model_id = resolution.model_id
    runtime = runtime or (app_settings.get("transcription_runtime_by_model") or {}).get(model_id)
    if not runtime:
        if model_id == PARAKEET_MODEL_ID:
            runtime = "external_coreml"
        elif model_id == FUN_ASR_MODEL_ID:
            runtime = FUN_ASR_RUNTIME
        elif model_id in QWEN_ASR_MODEL_IDS and model_id not in MANAGED_SHERPA_MODEL_IDS:
            runtime = "mlx"
        else:
            runtime = "cpu"
    if resolution.fell_back:
        task_manager.update_task(
            task_id,
            step="loading_model",
            progress=2,
            message=resolution.fallback_reason,
            details={"model_resolution": resolution.to_details()},
        )
        task_manager.add_log(
            task_id,
            "warning",
            "语音转写",
            resolution.fallback_reason,
            suggestion="可在转写设置中校验模型后重新选择",
        )
    else:
        task_manager.update_task(
            task_id,
            details={"model_resolution": resolution.to_details()},
        )
    run_id = str(uuid.uuid4())
    started_at = time_module.time()
    inference_started = time_module.perf_counter()
    db_run = get_db()
    db_run.execute(
        """INSERT INTO transcription_runs
           (id,project_id,task_id,model,language,status,started_at)
           VALUES (?,?,?,?,?,'running',datetime('now','localtime'))""",
        (run_id, project_id, task_id, model_id, language),
    )
    project_timing = db_run.execute("SELECT range_start FROM projects WHERE id=?", (project_id,)).fetchone()
    time_offset = float(project_timing["range_start"] or 0) if project_timing else 0.0
    db_run.commit()
    db_run.close()
    logger.info("[Transcriber] 加载模型: %s, 来源: %s, 语言: %s", model_id, resolution.source, language)
    task_manager.add_log(task_id, "info", "语音转写", f"加载模型: {model_id}")

    imported = None
    if model_id.startswith("local:"):
        from .local_models import validate_imported
        imported = validate_imported(model_id)
        if not imported["ready"]:
            raise TranscriptionError("导入模型路径失效，需要重新定位", "MODEL_NEEDS_RELINK")

    detected_lang_box: list[str] | None = None
    if model_id == FUN_ASR_MODEL_ID:
        session = create_fun_asr_session(task_id, audio_path, language)
        segments_gen = session.segments
        detected_lang = session.detected_language
        audio_duration = session.audio_duration
        device = session.device
        compute_type = session.compute_type
        runtime_model_name = session.model_label
        progress_start = session.progress_start
    elif model_id in QWEN_ASR_MODEL_IDS and runtime == "mlx":
        from .qwen_mlx import create_qwen_mlx_session

        session = create_qwen_mlx_session(task_id, audio_path, language, model_id)
        segments_gen = session.segments
        detected_lang = session.detected_language
        audio_duration = session.audio_duration
        device = session.device
        compute_type = session.compute_type
        runtime_model_name = session.model_label
        progress_start = session.progress_start
    elif model_id in MANAGED_SHERPA_MODEL_IDS:
        session = create_managed_session(
            task_id,
            audio_path,
            language,
            model_id,
            runtime or "cpu",
            resources=resources,
        )
        segments_gen = session.segments
        detected_lang = session.detected_language
        audio_duration = session.audio_duration
        device = session.device
        compute_type = session.compute_type
        runtime_model_name = session.model_label
        progress_start = session.progress_start
    elif imported and imported["format"] == "memo-coreml":
        session = create_parakeet_session(task_id,audio_path,language,PARAKEET_MODEL_ID,
            coreml_model_dir=imported["path"],coreml_cli_path=imported.get("cli_path"),runtime="external_coreml")
        segments_gen=session.segments; detected_lang=session.detected_language; audio_duration=session.audio_duration
        device=session.device; compute_type=session.compute_type; runtime_model_name=imported["display_name"]; progress_start=session.progress_start
    elif imported and imported["format"] == "sherpa-onnx":
        session = create_parakeet_session(task_id,audio_path,language,PARAKEET_ONNX_MODEL_ID,
            runtime=runtime,onnx_model_dir=imported["path"],resources=resources)
        segments_gen=session.segments; detected_lang=session.detected_language; audio_duration=session.audio_duration
        device=session.device; compute_type=session.compute_type; runtime_model_name=imported["display_name"]; progress_start=session.progress_start
    elif model_id in PARAKEET_MODEL_IDS:
        session = create_parakeet_session(
            task_id,
            audio_path,
            language,
            model_id,
            coreml_model_dir=app_settings.get("coreml_model_path"),
            coreml_cli_path=app_settings.get("coreml_cli_path"),
            runtime=runtime,
            resources=resources,
        )
        segments_gen = session.segments
        detected_lang = session.detected_language
        audio_duration = session.audio_duration
        device = session.device
        compute_type = session.compute_type
        runtime_model_name = session.model_label
        progress_start = session.progress_start
    elif runtime == "mlx" or (imported and imported["format"] == "mlx"):
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
        detected_lang = detected_lang_box[0]
        device, compute_type = "mlx", "MLX"
        runtime_model_name = f"MLX Whisper {model_id}"
        progress_start = 5.0
    else:
        from faster_whisper import WhisperModel

        from .model_pool import cpu_model_pool, model_identity, optimal_cpu_threads

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
        model, reused = resources.enter_context(cpu_model_pool.lease(
            (model_identity(str(load_target), device, compute_type), cpu_threads),
            lambda: WhisperModel(
                load_target,
                device=device,
                compute_type=compute_type,
                cpu_threads=cpu_threads,
            ),
        ))
        task_manager.update_task(task_id, details={
            "model_cache_hit": reused,
            "model_load_seconds": round(time_module.perf_counter() - load_started, 3),
        })
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
        detected_lang = info.language
        audio_duration = info.duration
        runtime_model_name = f"faster-whisper {model_id}"
        progress_start = 5.0

    preparation_seconds = time_module.perf_counter() - inference_started
    task_manager.update_task(task_id, details={"preparation_seconds": round(preparation_seconds, 3)})
    logger.info(
        "[Transcriber] 运行模型: %s, 设备: %s, 精度: %s",
        runtime_model_name, device, compute_type,
    )

    task_manager.update_task(
        task_id, step="transcribing", progress=progress_start, message="正在转写音频...",
        details={
            "mode": "incremental",
            "model": runtime_model_name,
            "model_id": model_id,
            "model_source": resolution.source,
            "device": device,
            "is_generating_segments": True,
            "is_postprocessing": False,
            "generated_segments": 0,
            "current_time": 0,
        }
    )
    task_manager.add_log(
        task_id, "info", "语音转写",
        f"模型 {runtime_model_name} 已加载，设备: {device}",
        detail=f"精度: {compute_type}"
    )

    logger.info(f"[Transcriber] 检测到语言: {detected_lang}, 音频时长: {audio_duration:.1f}s")
    task_manager.add_log(
        task_id, "info", "语音转写",
        f"检测到语言: {detected_lang}, 音频时长: {audio_duration:.1f}秒"
    )

    # 核心：逐段迭代，边转写边写入
    generated_count = 0
    last_log_time = time_module.time()

    from .transcription_buffer import DraftWriter
    with DraftWriter() as draft_writer:
        for segment in segments_gen:
            task_manager.checkpoint(task_id)
            current_time = segment.end

            # Write to a run-scoped staging table. Existing published subtitles are
            # intentionally untouched until this run has a valid final result.
            seg_id = str(uuid.uuid4())
            text = segment.text.strip()
            if not text:
                continue
            generated_count += 1
            timings_json = json.dumps(
                _segment_word_timings(segment, time_offset), ensure_ascii=False, separators=(",", ":")
            )

            flushed = draft_writer.append((
                seg_id, run_id, project_id, generated_count, segment.start + time_offset,
                segment.end + time_offset, text, timings_json,
            ))
            if not flushed:
                continue

            # 不同引擎会预留各自的模型准备/CLI 运行进度区间。
            progress_span = 90 - progress_start
            progress = min(progress_start + (current_time / max(audio_duration, 1)) * progress_span, 90)

            # 节流：每 5 条或每 3 秒写一次日志
            now = time_module.time()
            should_log = (generated_count % LOG_THROTTLE_INTERVAL == 0) or (now - last_log_time > 3)
            log_msg = ""
            if should_log:
                log_msg = f"已转写到 {_fmt_time(current_time)} / {_fmt_time(audio_duration)}，生成 {generated_count} 条字幕"
                last_log_time = now
                logger.info(f"[Transcriber] {log_msg}")

            # Publish only committed draft counts; avoid per-segment SQLite writes.
            detail_update = {
                "mode": "incremental",
                "is_generating_segments": True,
                "is_postprocessing": False,
                "generated_segments": generated_count,
                "current_time": round(current_time, 1),
                "audio_duration": round(audio_duration, 1),
                "latest_segment": {
                    "index": generated_count,
                    "start": round(segment.start + time_offset, 3),
                    "end": round(segment.end + time_offset, 3),
                    "text": text,
                },
            }
            task_manager.update_task(
                task_id,
                step="transcribing",
                progress=progress,
                message=f"正在转写 {_fmt_time(current_time)} / {_fmt_time(audio_duration)}，已生成 {generated_count} 条字幕",
                details=detail_update,
            )
            if should_log:
                task_manager.add_log(task_id, "info", "语音转写", log_msg)

    task_manager.checkpoint(task_id)
    if detected_lang_box is not None and detected_lang_box[0]:
        detected_lang = detected_lang_box[0]
    if generated_count == 0:
        db_empty = get_db()
        db_empty.execute(
            """UPDATE transcription_runs SET status='failed', error_code='NO_SPEECH_RESULT',
               error_message='模型没有生成有效字幕', finished_at=datetime('now','localtime')
               WHERE id=?""", (run_id,),
        )
        db_empty.commit()
        db_empty.close()
        raise TranscriptionError(
            "转写模型没有生成有效字幕，原有字幕已安全保留",
            "NO_SPEECH_RESULT",
            suggestion="请确认视频包含清晰语音，或选择备用模型后重试",
        )

    # ── 阶段 1 完成 ──
    logger.info(f"[Transcriber] 增量转写完成: {generated_count} 条原始字幕")
    task_manager.update_task(
        task_id, step="postprocessing", progress=90,
        message="转写完成，正在进行字幕后处理...",
        details={
            "is_generating_segments": False,
            "is_postprocessing": True,
            "generated_segments": generated_count,
        }
    )
    task_manager.add_log(task_id, "info", "字幕后处理", f"转写完成, {generated_count} 条原始字幕, 开始后处理")

    # ── 阶段 2：最终后处理 ──
    task_manager.checkpoint(task_id)
    # Read only this run's staged segments.
    db_reader = get_db()
    draft_rows = db_reader.execute(
        "SELECT id, idx, start, end, text, timings_json FROM transcription_segments WHERE run_id=? ORDER BY idx",
        (run_id,)
    ).fetchall()
    db_reader.close()

    # 转为 _post_process 需要的格式
    draft_segments = []
    for r in draft_rows:
        draft_segments.append({
            "id": r["id"],
            "start": r["start"],
            "end": r["end"],
            "text": r["text"],
            "timings": _decode_timings(r["timings_json"]),
        })

    needs_review = False
    if draft_segments:
        processed, merge_count, split_count = _post_process_segments(draft_segments)
        task_manager.checkpoint(task_id)

        # Publish atomically only after a non-empty result is fully processed.
        db_writer = get_db()
        try:
            db_writer.execute("BEGIN IMMEDIATE")
            existing = db_writer.execute("SELECT 1 FROM segments WHERE project_id=? LIMIT 1", (project_id,)).fetchone()
            needs_review = existing is not None
            if not needs_review:
                for seg_index, seg in enumerate(processed):
                    if seg_index % 20 == 0:
                        task_manager.checkpoint(task_id)
                    db_writer.execute(
                        """INSERT INTO segments
                           (id,project_id,idx,start,end,raw_text,clean_text,is_draft,source_stage,transcription_run_id)
                           VALUES (?,?,?,?,?,?,?,0,'postprocessed',?)""",
                        (str(uuid.uuid4()), project_id, seg["index"], seg["start"], seg["end"], seg["text"], seg["text"], run_id)
                    )
                db_writer.execute("UPDATE projects SET edit_revision=COALESCE(edit_revision,0)+1, updated_at=datetime('now','localtime') WHERE id=?", (project_id,))
            db_writer.execute(
                """UPDATE transcription_runs SET status=?, segments_count=?, result_json=?,
                   finished_at=datetime('now','localtime') WHERE id=?""",
                ('candidate' if needs_review else 'success', len(processed), json.dumps(processed, ensure_ascii=False), run_id),
            )
            # Preserve the model's raw segment/token timeline for diagnostics and
            # future reprocessing.  Published subtitles remain in ``segments``.
            db_writer.execute("UPDATE transcription_segments SET is_draft=0 WHERE run_id=?", (run_id,))
            task_manager.checkpoint(task_id)
            db_writer.commit()
        except Exception:
            db_writer.rollback()
            raise
        finally:
            db_writer.close()

        # 计算统计
        durations = [s["end"] - s["start"] for s in processed]
        min_dur = min(durations) if durations else 0
        max_dur = max(durations) if durations else 0
        avg_dur = sum(durations) / len(durations) if durations else 0
        total_final = len(processed)
        too_short_count = sum(1 for d in durations if d < MIN_DURATION - 0.001)
        too_long_count = sum(1 for d in durations if d > MAX_DURATION + 0.001)

        logger.info(
            f"[Transcriber] 后处理完成: {total_final} 条（合并 {merge_count}, 拆分 {split_count}）"
        )
        task_manager.add_log(
            task_id, "info", "字幕后处理",
            f"后处理完成: {total_final} 条字幕（合并 {merge_count} 条，拆分 {split_count} 条）",
            detail=f"最短: {min_dur:.1f}s, 最长: {max_dur:.1f}s, 平均: {avg_dur:.1f}s"
        )
    else:
        total_final = 0
        min_dur = max_dur = avg_dur = 0
        merge_count = split_count = 0
        too_short_count = too_long_count = 0
        task_manager.add_log(task_id, "warning", "字幕后处理", "没有找到草稿字幕，跳过")

    # ── 完成 ──
    task_manager.update_task(
        task_id, step="transcription_done", progress=100,
        message=(f"新转写候选已生成，共 {total_final} 条；原字幕已保留" if needs_review else f"转写完成（{detected_lang}），共 {total_final} 条字幕"),
        details={
            "mode": "incremental",
            "is_generating_segments": False,
            "is_postprocessing": False,
            "model": model_id,
            "model_source": resolution.source,
            "device": device,
            "detected_language": detected_lang,
            "total_segments": total_final,
            "audio_duration": round(audio_duration, 3),
            "merged_short": merge_count,
            "split_long": split_count,
            "timestamp_strategy": "word_or_token_aligned",
            "min_duration": round(min_dur, 3),
            "max_duration": round(max_dur, 3),
            "avg_duration": round(avg_dur, 3),
            "too_short_count": too_short_count,
            "too_long_count": too_long_count,
            "subtitle_stats": {
                "total_segments": total_final,
                "audio_duration": round(audio_duration, 1),
                "average_duration": round(avg_dur, 1),
                "min_duration": round(min_dur, 2),
                "max_duration": round(max_dur, 2),
                "merged_short_segments": merge_count,
                "split_long_segments": split_count,
            },
            "run_id": run_id,
            "candidate_ready": needs_review,
            "elapsed_seconds": round(time_module.time() - started_at, 2),
            "realtime_factor": round((time_module.time() - started_at) / max(audio_duration, 0.1), 3),
        }
    )
    task_manager.add_log(
        task_id, "info", "转写完成",
        f"语言: {detected_lang}, 共 {total_final} 条字幕, 音频时长: {audio_duration:.1f}秒"
    )

    return processed if draft_segments else []

