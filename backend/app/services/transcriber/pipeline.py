"""Two-stage incremental transcription pipeline: streaming drafts + atomic postprocessing."""

from __future__ import annotations

import json
import logging
import time as time_module
import uuid
from contextlib import ExitStack
from typing import Any

from ...models.database import get_db
from ...utils.config import MAX_DURATION, MIN_DURATION, WHISPER_MODEL
from ...utils.task_manager import task_manager
from ..cloud_asr import FUN_ASR_MODEL_ID, FUN_ASR_RUNTIME
from ..parakeet_transcriber import PARAKEET_MODEL_ID
from ..transcription_buffer import DraftWriter
from ..transcription_models import (
    MANAGED_SHERPA_MODEL_IDS,
    QWEN_ASR_MODEL_IDS,
    resolve_transcription_model,
)
from ..transcription_postprocess import (
    _decode_timings,
    _fmt_time,
    _post_process_segments,
    _segment_word_timings,
)
from .engines import EngineSession, TranscriptionError, open_engine_session

logger = logging.getLogger(__name__)

# 日志节流：每 N 条字幕写一次日志
LOG_THROTTLE_INTERVAL = 5


def _resolve_default_runtime(model_id: str, requested: str | None, app_settings: dict) -> str:
    runtime = requested or (app_settings.get("transcription_runtime_by_model") or {}).get(model_id)
    if runtime:
        return runtime
    if model_id == PARAKEET_MODEL_ID:
        return "external_coreml"
    if model_id == FUN_ASR_MODEL_ID:
        return FUN_ASR_RUNTIME
    if model_id in QWEN_ASR_MODEL_IDS and model_id not in MANAGED_SHERPA_MODEL_IDS:
        return "mlx"
    return "cpu"


def _stage_draft_segments(
    *,
    task_id: str,
    run_id: str,
    project_id: str,
    session: EngineSession,
    time_offset: float,
) -> int:
    generated_count = 0
    last_log_time = time_module.time()
    progress_span = 90 - session.progress_start
    with DraftWriter() as draft_writer:
        for segment in session.segments:
            task_manager.checkpoint(task_id)
            current_time = segment.end
            text = segment.text.strip()
            if not text:
                continue
            generated_count += 1
            timings_json = json.dumps(
                _segment_word_timings(segment, time_offset),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            flushed = draft_writer.append(
                (
                    str(uuid.uuid4()),
                    run_id,
                    project_id,
                    generated_count,
                    segment.start + time_offset,
                    segment.end + time_offset,
                    text,
                    timings_json,
                )
            )
            if not flushed:
                continue
            progress = min(
                session.progress_start
                + (current_time / max(session.audio_duration, 1)) * progress_span,
                90,
            )
            now = time_module.time()
            should_log = (generated_count % LOG_THROTTLE_INTERVAL == 0) or (now - last_log_time > 3)
            log_msg = ""
            if should_log:
                log_msg = (
                    f"已转写到 {_fmt_time(current_time)} / {_fmt_time(session.audio_duration)}，"
                    f"生成 {generated_count} 条字幕"
                )
                last_log_time = now
                logger.info("[Transcriber] %s", log_msg)
            task_manager.update_task(
                task_id,
                step="transcribing",
                progress=progress,
                message=(
                    f"正在转写 {_fmt_time(current_time)} / {_fmt_time(session.audio_duration)}，"
                    f"已生成 {generated_count} 条字幕"
                ),
                details={
                    "mode": "incremental",
                    "is_generating_segments": True,
                    "is_postprocessing": False,
                    "generated_segments": generated_count,
                    "current_time": round(current_time, 1),
                    "audio_duration": round(session.audio_duration, 1),
                    "latest_segment": {
                        "index": generated_count,
                        "start": round(segment.start + time_offset, 3),
                        "end": round(segment.end + time_offset, 3),
                        "text": text,
                    },
                },
            )
            if should_log:
                task_manager.add_log(task_id, "info", "语音转写", log_msg)
    return generated_count


def _publish_postprocessed_segments(
    *,
    task_id: str,
    run_id: str,
    project_id: str,
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    task_manager.checkpoint(task_id)
    db_reader = get_db()
    draft_rows = db_reader.execute(
        "SELECT id, idx, start, end, text, timings_json FROM transcription_segments WHERE run_id=? ORDER BY idx",
        (run_id,),
    ).fetchall()
    db_reader.close()

    draft_segments = [
        {
            "id": row["id"],
            "start": row["start"],
            "end": row["end"],
            "text": row["text"],
            "timings": _decode_timings(row["timings_json"]),
        }
        for row in draft_rows
    ]
    if not draft_segments:
        task_manager.add_log(task_id, "warning", "字幕后处理", "没有找到草稿字幕，跳过")
        return [], False, {
            "total_final": 0,
            "min_dur": 0,
            "max_dur": 0,
            "avg_dur": 0,
            "merge_count": 0,
            "split_count": 0,
            "too_short_count": 0,
            "too_long_count": 0,
        }

    processed, merge_count, split_count = _post_process_segments(draft_segments)
    task_manager.checkpoint(task_id)

    db_writer = get_db()
    try:
        db_writer.execute("BEGIN IMMEDIATE")
        existing = db_writer.execute(
            "SELECT 1 FROM segments WHERE project_id=? LIMIT 1", (project_id,)
        ).fetchone()
        needs_review = existing is not None
        if not needs_review:
            for seg_index, seg in enumerate(processed):
                if seg_index % 20 == 0:
                    task_manager.checkpoint(task_id)
                db_writer.execute(
                    """INSERT INTO segments
                       (id,project_id,idx,start,end,raw_text,clean_text,is_draft,source_stage,transcription_run_id)
                       VALUES (?,?,?,?,?,?,?,0,'postprocessed',?)""",
                    (
                        str(uuid.uuid4()),
                        project_id,
                        seg["index"],
                        seg["start"],
                        seg["end"],
                        seg["text"],
                        seg["text"],
                        run_id,
                    ),
                )
            db_writer.execute(
                "UPDATE projects SET edit_revision=COALESCE(edit_revision,0)+1, updated_at=datetime('now','localtime') WHERE id=?",
                (project_id,),
            )
        db_writer.execute(
            """UPDATE transcription_runs SET status=?, segments_count=?, result_json=?,
               finished_at=datetime('now','localtime') WHERE id=?""",
            (
                "candidate" if needs_review else "success",
                len(processed),
                json.dumps(processed, ensure_ascii=False),
                run_id,
            ),
        )
        db_writer.execute("UPDATE transcription_segments SET is_draft=0 WHERE run_id=?", (run_id,))
        task_manager.checkpoint(task_id)
        db_writer.commit()
    except Exception:
        db_writer.rollback()
        raise
    finally:
        db_writer.close()

    durations = [seg["end"] - seg["start"] for seg in processed]
    stats = {
        "total_final": len(processed),
        "min_dur": min(durations) if durations else 0,
        "max_dur": max(durations) if durations else 0,
        "avg_dur": sum(durations) / len(durations) if durations else 0,
        "merge_count": merge_count,
        "split_count": split_count,
        "too_short_count": sum(1 for d in durations if d < MIN_DURATION - 0.001),
        "too_long_count": sum(1 for d in durations if d > MAX_DURATION + 0.001),
    }
    logger.info(
        "[Transcriber] 后处理完成: %s 条（合并 %s, 拆分 %s）",
        stats["total_final"],
        merge_count,
        split_count,
    )
    task_manager.add_log(
        task_id,
        "info",
        "字幕后处理",
        f"后处理完成: {stats['total_final']} 条字幕（合并 {merge_count} 条，拆分 {split_count} 条）",
        detail=(
            f"最短: {stats['min_dur']:.1f}s, 最长: {stats['max_dur']:.1f}s, "
            f"平均: {stats['avg_dur']:.1f}s"
        ),
    )
    return processed, needs_review, stats


def transcribe_audio(
    task_id: str,
    audio_path: str,
    project_id: str,
    language: str = "auto",
    model_size: str | None = None,
    runtime: str | None = None,
):
    # Workflow preparation does not occupy the inference slot. Standalone
    # transcription already holds it; resource_slot is reentrant in that case.
    with task_manager.resource_slot(task_id, "ml"), ExitStack() as resources:
        return _transcribe_audio(
            task_id, audio_path, project_id, language, model_size, runtime, resources
        )


def _transcribe_audio(task_id, audio_path, project_id, language, model_size, runtime, resources):
    """转写音频文件，生成字幕段（segments）。增量写入草稿并原子发布后处理结果。"""
    task_manager.update_task(
        task_id, step="loading_model", progress=2, message="正在准备转写模型..."
    )
    task_manager.add_log(task_id, "info", "语音转写", "正在准备转写模型...")

    try:
        from ..app_settings import get_effective_app_settings as get_app_settings

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
        raise TranscriptionError(
            "所选模型不可用，未改用其他模型",
            "MODEL_UNAVAILABLE",
            suggestion=resolution.fallback_reason,
        )
    model_id = resolution.model_id
    resolved_runtime = _resolve_default_runtime(model_id, runtime, app_settings)
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
        task_manager.update_task(task_id, details={"model_resolution": resolution.to_details()})

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
    project_timing = db_run.execute(
        "SELECT range_start FROM projects WHERE id=?", (project_id,)
    ).fetchone()
    time_offset = float(project_timing["range_start"] or 0) if project_timing else 0.0
    db_run.commit()
    db_run.close()
    logger.info("[Transcriber] 加载模型: %s, 来源: %s, 语言: %s", model_id, resolution.source, language)
    task_manager.add_log(task_id, "info", "语音转写", f"加载模型: {model_id}")

    imported = None
    if model_id.startswith("local:"):
        from ..local_models import validate_imported

        imported = validate_imported(model_id)
        if not imported["ready"]:
            raise TranscriptionError("导入模型路径失效，需要重新定位", "MODEL_NEEDS_RELINK")

    session = open_engine_session(
        task_id=task_id,
        audio_path=audio_path,
        language=language,
        model_id=model_id,
        runtime=resolved_runtime,
        resolution=resolution,
        app_settings=app_settings,
        imported=imported,
        resources=resources,
    )

    preparation_seconds = time_module.perf_counter() - inference_started
    task_manager.update_task(
        task_id, details={"preparation_seconds": round(preparation_seconds, 3)}
    )
    logger.info(
        "[Transcriber] 运行模型: %s, 设备: %s, 精度: %s",
        session.model_label,
        session.device,
        session.compute_type,
    )
    task_manager.update_task(
        task_id,
        step="transcribing",
        progress=session.progress_start,
        message="正在转写音频...",
        details={
            "mode": "incremental",
            "model": session.model_label,
            "model_id": model_id,
            "model_source": resolution.source,
            "device": session.device,
            "is_generating_segments": True,
            "is_postprocessing": False,
            "generated_segments": 0,
            "current_time": 0,
        },
    )
    task_manager.add_log(
        task_id,
        "info",
        "语音转写",
        f"模型 {session.model_label} 已加载，设备: {session.device}",
        detail=f"精度: {session.compute_type}",
    )
    logger.info(
        "[Transcriber] 检测到语言: %s, 音频时长: %.1fs",
        session.detected_language,
        session.audio_duration,
    )
    task_manager.add_log(
        task_id,
        "info",
        "语音转写",
        f"检测到语言: {session.detected_language}, 音频时长: {session.audio_duration:.1f}秒",
    )

    generated_count = _stage_draft_segments(
        task_id=task_id,
        run_id=run_id,
        project_id=project_id,
        session=session,
        time_offset=time_offset,
    )
    task_manager.checkpoint(task_id)
    detected_lang = (
        session.detected_lang_box[0]
        if session.detected_lang_box is not None and session.detected_lang_box[0]
        else session.detected_language
    )
    if generated_count == 0:
        db_empty = get_db()
        db_empty.execute(
            """UPDATE transcription_runs SET status='failed', error_code='NO_SPEECH_RESULT',
               error_message='模型没有生成有效字幕', finished_at=datetime('now','localtime')
               WHERE id=?""",
            (run_id,),
        )
        db_empty.commit()
        db_empty.close()
        raise TranscriptionError(
            "转写模型没有生成有效字幕，原有字幕已安全保留",
            "NO_SPEECH_RESULT",
            suggestion="请确认视频包含清晰语音，或选择备用模型后重试",
        )

    logger.info("[Transcriber] 增量转写完成: %s 条原始字幕", generated_count)
    task_manager.update_task(
        task_id,
        step="postprocessing",
        progress=90,
        message="转写完成，正在进行字幕后处理...",
        details={
            "is_generating_segments": False,
            "is_postprocessing": True,
            "generated_segments": generated_count,
        },
    )
    task_manager.add_log(
        task_id, "info", "字幕后处理", f"转写完成, {generated_count} 条原始字幕, 开始后处理"
    )

    processed, needs_review, stats = _publish_postprocessed_segments(
        task_id=task_id,
        run_id=run_id,
        project_id=project_id,
    )
    total_final = stats["total_final"]
    task_manager.update_task(
        task_id,
        step="transcription_done",
        progress=100,
        message=(
            f"新转写候选已生成，共 {total_final} 条；原字幕已保留"
            if needs_review
            else f"转写完成（{detected_lang}），共 {total_final} 条字幕"
        ),
        details={
            "mode": "incremental",
            "is_generating_segments": False,
            "is_postprocessing": False,
            "model": model_id,
            "model_source": resolution.source,
            "device": session.device,
            "detected_language": detected_lang,
            "total_segments": total_final,
            "audio_duration": round(session.audio_duration, 3),
            "merged_short": stats["merge_count"],
            "split_long": stats["split_count"],
            "timestamp_strategy": "word_or_token_aligned",
            "min_duration": round(stats["min_dur"], 3),
            "max_duration": round(stats["max_dur"], 3),
            "avg_duration": round(stats["avg_dur"], 3),
            "too_short_count": stats["too_short_count"],
            "too_long_count": stats["too_long_count"],
            "subtitle_stats": {
                "total_segments": total_final,
                "audio_duration": round(session.audio_duration, 1),
                "average_duration": round(stats["avg_dur"], 1),
                "min_duration": round(stats["min_dur"], 2),
                "max_duration": round(stats["max_dur"], 2),
                "merged_short_segments": stats["merge_count"],
                "split_long_segments": stats["split_count"],
            },
            "run_id": run_id,
            "candidate_ready": needs_review,
            "elapsed_seconds": round(time_module.time() - started_at, 2),
            "realtime_factor": round(
                (time_module.time() - started_at) / max(session.audio_duration, 0.1), 3
            ),
        },
    )
    task_manager.add_log(
        task_id,
        "info",
        "转写完成",
        f"语言: {detected_lang}, 共 {total_final} 条字幕, 音频时长: {session.audio_duration:.1f}秒",
    )
    return processed
