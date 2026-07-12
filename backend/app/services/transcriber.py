"""
字幕工厂 - 语音转写服务（faster-whisper / Parakeet）增量版

将音频转写为带时间戳的字幕段。

两阶段设计：
  阶段 1：实时转写。模型 segment 逐条写入数据库，标记 is_draft=1。
  阶段 2：最终后处理。读取全部 draft，执行合并/拆分/时长修正，替换为 final。

重要：不要 list(segments_generator) 阻塞等待所有 segment 生成完毕。
"""

import uuid
import re
import math
import time as time_module
import logging
from typing import List, Optional

from ..utils.config import WHISPER_MODEL, MAX_CHARS_CN, MAX_CHARS_EN, MIN_DURATION, MAX_DURATION
from ..utils.task_manager import task_manager
from ..models.database import get_db
from .parakeet_transcriber import (
    PARAKEET_MODEL_ID,
    PARAKEET_ONNX_MODEL_ID,
    create_parakeet_session,
)

logger = logging.getLogger(__name__)

WHISPER_MODEL_IDS = frozenset({"tiny", "base", "small", "medium", "large-v3"})
PARAKEET_MODEL_IDS = frozenset({PARAKEET_MODEL_ID, PARAKEET_ONNX_MODEL_ID})
SUPPORTED_TRANSCRIPTION_MODELS = WHISPER_MODEL_IDS | PARAKEET_MODEL_IDS

# 日志节流：每 N 条字幕写一次日志
LOG_THROTTLE_INTERVAL = 5


class TranscriptionError(RuntimeError):
    def __init__(self, message: str, error_code: str, *, recoverable: bool = True,
                 suggestion: str = "请检查音频和模型状态后重试"):
        super().__init__(message)
        self.error_code = error_code
        self.recoverable = recoverable
        self.available_actions = ["retry", "choose_fallback"] if recoverable else []
        self.suggestion = suggestion


def transcribe_audio(task_id: str, audio_path: str, project_id: str, language: str = "auto", model_size: str | None = None):
    """
    转写音频文件，生成字幕段（segments）。
    增量写入：每生成一个 segment 立即写数据库，前端可实时拉取。
    """
    task_manager.update_task(task_id, step="loading_model", progress=2, message="正在准备转写模型...")
    task_manager.add_log(task_id, "info", "语音转写", "正在准备转写模型...")

    model_size = model_size or WHISPER_MODEL
    run_id = str(uuid.uuid4())
    started_at = time_module.time()
    db_run = get_db()
    db_run.execute(
        """INSERT INTO transcription_runs
           (id,project_id,task_id,model,language,status,started_at)
           VALUES (?,?,?,?,?,'running',datetime('now','localtime'))""",
        (run_id, project_id, task_id, model_size, language),
    )
    db_run.commit()
    db_run.close()
    logger.info(f"[Transcriber] 加载模型: {model_size}, 语言: {language}")
    task_manager.add_log(task_id, "info", "语音转写", f"加载模型: {model_size}")

    if model_size in PARAKEET_MODEL_IDS:
        session = create_parakeet_session(task_id, audio_path, language, model_size)
        segments_gen = session.segments
        detected_lang = session.detected_language
        audio_duration = session.audio_duration
        device = session.device
        compute_type = session.compute_type
        runtime_model_name = session.model_label
        progress_start = session.progress_start
    else:
        from faster_whisper import WhisperModel
        import ctranslate2

        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        task_manager.checkpoint(task_id)

        lang = None if language in ("auto", "") else language
        segments_gen, info = model.transcribe(
            audio_path,
            language=lang,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        detected_lang = info.language
        audio_duration = info.duration
        runtime_model_name = f"faster-whisper {model_size}"
        progress_start = 5.0

    logger.info(
        "[Transcriber] 运行模型: %s, 设备: %s, 精度: %s",
        runtime_model_name, device, compute_type,
    )

    task_manager.update_task(
        task_id, step="transcribing", progress=progress_start, message="正在转写音频...",
        details={
            "mode": "incremental",
            "model": runtime_model_name,
            "model_id": model_size,
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
    last_idx = 0

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

        db_writer = get_db()
        db_writer.execute(
            """INSERT INTO transcription_segments
               (id, run_id, project_id, idx, start, end, text, is_draft)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
            (seg_id, run_id, project_id, generated_count, segment.start, segment.end, text)
        )
        db_writer.commit()
        db_writer.close()

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

        # 更新 task details（每条都更新，不节流，前端需要）
        detail_update = {
            "mode": "incremental",
            "is_generating_segments": True,
            "is_postprocessing": False,
            "generated_segments": generated_count,
            "current_time": round(current_time, 1),
            "audio_duration": round(audio_duration, 1),
            "latest_segment": {
                "index": generated_count,
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
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

        last_idx = generated_count

    task_manager.checkpoint(task_id)
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
        message=f"转写完成，正在进行字幕后处理...",
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
        "SELECT id, idx, start, end, text FROM transcription_segments WHERE run_id=? ORDER BY idx",
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
        })

    if draft_segments:
        processed, merge_count, split_count = _post_process_segments(draft_segments)
        task_manager.checkpoint(task_id)

        # Publish atomically only after a non-empty result is fully processed.
        db_writer = get_db()
        try:
            db_writer.execute("BEGIN IMMEDIATE")
            db_writer.execute("DELETE FROM segments WHERE project_id=?", (project_id,))
            for seg_index, seg in enumerate(processed):
                if seg_index % 20 == 0:
                    task_manager.checkpoint(task_id)
                seg_id = str(uuid.uuid4())
                db_writer.execute(
                    """INSERT INTO segments
                       (id,project_id,idx,start,end,raw_text,clean_text,is_draft,source_stage,transcription_run_id)
                       VALUES (?,?,?,?,?,?,?,0,'postprocessed',?)""",
                    (seg_id, project_id, seg["index"], seg["start"], seg["end"], seg["text"], seg["text"], run_id)
                )
            db_writer.execute(
                """UPDATE transcription_runs SET status='success', segments_count=?,
                   finished_at=datetime('now','localtime') WHERE id=?""",
                (len(processed), run_id),
            )
            db_writer.execute("DELETE FROM transcription_segments WHERE run_id=?", (run_id,))
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
        message=f"转写完成（{detected_lang}），共 {total_final} 条字幕",
        details={
            "mode": "incremental",
            "is_generating_segments": False,
            "is_postprocessing": False,
            "model": model_size,
            "device": device,
            "detected_language": detected_lang,
            "total_segments": total_final,
            "audio_duration": round(audio_duration, 3),
            "merged_short": merge_count,
            "split_long": split_count,
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
            "elapsed_seconds": round(time_module.time() - started_at, 2),
            "realtime_factor": round((time_module.time() - started_at) / max(audio_duration, 0.1), 3),
        }
    )
    task_manager.add_log(
        task_id, "info", "转写完成",
        f"语言: {detected_lang}, 共 {total_final} 条字幕, 音频时长: {audio_duration:.1f}秒"
    )

    return processed if draft_segments else []


def _fmt_time(seconds: float) -> str:
    """格式化时间: 00:04:32"""
    if not seconds:
        return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _post_process_segments(raw_segments: list) -> tuple:
    """
    后处理字幕段：
    1. 合并持续时间 < MIN_DURATION 且相邻的短字幕
    2. 拆分文本过长的字幕
    3. 应用持续时间约束

    Returns: (final_segments, merge_count, split_count)
    """
    logger.info("[Transcriber] 后处理: 规范时间轴、合并短句、拆分长句")
    merge_count = 0
    split_count = 0

    # 先规范非法/重叠区间，避免同一时刻出现多条字幕。
    normalized: List[dict] = []
    previous_end = 0.0
    for raw in sorted(raw_segments, key=lambda item: (float(item.get("start", 0)), float(item.get("end", 0)))):
        text = str(raw.get("text", "")).strip()
        if not text:
            continue
        start = max(0.0, float(raw.get("start", 0)))
        end = max(start + 0.05, float(raw.get("end", start)))
        if start < previous_end:
            start = previous_end
        if end <= start:
            continue
        normalized.append({"start": start, "end": end, "text": text})
        previous_end = end

    # 合并极短字幕，但不跨越明显静音，也不制造超过最大时长的段。
    merged: List[dict] = []
    i = 0
    while i < len(normalized):
        seg = dict(normalized[i])
        duration = seg["end"] - seg["start"]
        if duration < MIN_DURATION and merged:
            prev = merged[-1]
            gap = seg["start"] - prev["end"]
            if gap <= 0.75 and seg["end"] - prev["start"] <= MAX_DURATION:
                prev["end"] = seg["end"]
                prev["text"] = _smart_merge(prev["text"], seg["text"])
                merge_count += 1
                i += 1
                continue
        if duration < MIN_DURATION and i + 1 < len(normalized):
            nxt = normalized[i + 1]
            gap = nxt["start"] - seg["end"]
            if gap <= 0.75 and nxt["end"] - seg["start"] <= MAX_DURATION:
                seg["end"] = nxt["end"]
                seg["text"] = _smart_merge(seg["text"], nxt["text"])
                merge_count += 1
                i += 1
        merged.append(seg)
        i += 1

    # 同时按可读字符数和最大显示时长拆分，包含无标点长句的兜底。
    final: List[dict] = []
    for seg in merged:
        duration = seg["end"] - seg["start"]
        max_chars = _max_chars_for_text(seg["text"])
        required_parts = max(1, math.ceil(duration / MAX_DURATION), math.ceil(len(seg["text"]) / max_chars))
        if required_parts > 1:
            pieces = _split_text_into_pieces(seg["text"], max_chars, required_parts)
            split = _allocate_piece_times(seg["start"], seg["end"], pieces)
            final.extend(split)
            split_count += len(split) - 1
        else:
            final.append(seg)

    # 重新分配稳定的一基索引。
    output = []
    for i, seg in enumerate(final):
        output.append({
            "index": i + 1,
            "start": round(seg["start"], 3),
            "end": round(seg["end"], 3),
            "text": seg["text"].strip(),
        })

    return output, merge_count, split_count


def _smart_merge(text1: str, text2: str) -> str:
    if text1 and text1[-1] in ".。!！?？,，":
        return f"{text1} {text2}"
    return f"{text1} {text2}"


def _max_chars_for_text(text: str) -> int:
    cn_count = sum(1 for c in text if '\u3400' <= c <= '\u9fff')
    return MAX_CHARS_CN if cn_count >= max(1, len(text) // 4) else MAX_CHARS_EN


def _split_text_into_pieces(text: str, max_chars: int, min_parts: int = 1) -> List[str]:
    """Split at punctuation/words first, then fall back to character chunks."""
    clauses = [p.strip() for p in re.findall(r".+?(?:[.。!！?？，,;；]+|$)", text) if p.strip()]
    atoms: List[str] = []
    for clause in clauses or [text]:
        if len(clause) <= max_chars:
            atoms.append(clause)
            continue
        words = clause.split()
        if len(words) > 1:
            current = ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if current and len(candidate) > max_chars:
                    atoms.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                atoms.append(current)
        else:
            atoms.extend(clause[pos:pos + max_chars] for pos in range(0, len(clause), max_chars))

    pieces: List[str] = []
    for atom in atoms:
        candidate = _smart_merge(pieces[-1], atom) if pieces else atom
        if pieces and len(candidate) <= max_chars and len(pieces) + 1 >= min_parts:
            pieces[-1] = candidate
        else:
            pieces.append(atom)

    # Long-duration, short-text segments still need time slices. Split the longest
    # piece until the requested count is reached, without duplicating text.
    while len(pieces) < min_parts:
        idx = max(range(len(pieces)), key=lambda n: len(pieces[n]))
        value = pieces[idx]
        if len(value) < 2:
            break
        cut = len(value) // 2
        left = value[:cut].rstrip()
        right = value[cut:].lstrip()
        pieces[idx:idx + 1] = [left, right]
    return [piece for piece in pieces if piece]


def _allocate_piece_times(start: float, end: float, pieces: List[str]) -> List[dict]:
    if not pieces:
        return []
    duration = end - start
    per_duration = duration / len(pieces)
    cursor = start
    result = []
    for index, piece in enumerate(pieces):
        piece_end = end if index == len(pieces) - 1 else start + (index + 1) * per_duration
        result.append({"start": round(cursor, 3), "end": round(piece_end, 3), "text": piece})
        cursor = piece_end
    return result
