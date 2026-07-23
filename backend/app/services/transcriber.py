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
import json
import time as time_module
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from ..utils.config import (
    WHISPER_MODEL,
    WHISPER_MODELS_DIR,
    MAX_CHARS_CN,
    MAX_CHARS_EN,
    MIN_DURATION,
    MAX_DURATION,
)
from ..utils.task_manager import task_manager
from ..models.database import get_db
from .parakeet_transcriber import (
    PARAKEET_MODEL_ID,
    PARAKEET_ONNX_MODEL_ID,
    PARAKEET_SUPPORTED_LANGUAGES,
    create_parakeet_session,
    get_parakeet_model_status,
)

logger = logging.getLogger(__name__)

WHISPER_MODEL_IDS = frozenset({"tiny", "base", "small", "medium", "large-v3"})
PARAKEET_MODEL_IDS = frozenset({PARAKEET_MODEL_ID, PARAKEET_ONNX_MODEL_ID})
SUPPORTED_TRANSCRIPTION_MODELS = WHISPER_MODEL_IDS | PARAKEET_MODEL_IDS | {"custom"}
SAFE_TRANSCRIPTION_MODEL = "small"
_WHISPER_REQUIRED_FILES = frozenset({"model.bin", "config.json", "tokenizer.json"})

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


@dataclass(frozen=True)
class ModelResolution:
    requested_model: str
    model_id: str
    load_target: str
    source: str
    fallback_reason: str = ""

    @property
    def fell_back(self) -> bool:
        return bool(self.fallback_reason)

    def to_details(self) -> dict:
        # load_target may be a user-selected local path; never persist it.
        value = asdict(self)
        value.pop("load_target", None)
        value["fell_back"] = self.fell_back
        return value


def validate_whisper_model_path(path: str | Path | None) -> dict:
    """Validate a local faster-whisper CTranslate2 model directory."""
    if not path:
        return {"ok": False, "source": "unavailable", "error": "尚未选择模型目录"}
    candidate = Path(path).expanduser()
    missing = sorted(
        name for name in _WHISPER_REQUIRED_FILES if not (candidate / name).is_file()
    )
    if not candidate.is_dir() or missing:
        return {
            "ok": False,
            "source": "unavailable",
            "error": "模型目录缺少必要文件" if missing else "模型目录不存在",
            "missing_files": missing,
        }
    return {"ok": True, "source": "custom_path", "error": "", "missing_files": []}


def _bundled_whisper_model(model_id: str) -> Path | None:
    roots: list[Path] = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        roots.append(Path(frozen_root))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    for root in roots:
        for candidate in (
            root / "models" / "whisper" / model_id,
            root / "models" / f"faster-whisper-{model_id}",
        ):
            if validate_whisper_model_path(candidate)["ok"]:
                return candidate.resolve()
    return None


def _managed_whisper_model_ready(model_id: str) -> bool:
    if not WHISPER_MODELS_DIR.is_dir():
        return False
    # Hugging Face snapshots use a nested cache layout, while a repaired/imported
    # App model may be stored directly. Required filenames keep this bounded and
    # independent of cache naming conventions.
    for model_file in WHISPER_MODELS_DIR.rglob("model.bin"):
        parent = model_file.parent
        if model_id in str(parent).lower() and validate_whisper_model_path(parent)["ok"]:
            return True
    return False


def get_transcription_model_status(
    model_id: str,
    *,
    custom_model_path: str | Path | None = None,
    coreml_model_path: str | Path | None = None,
    coreml_cli_path: str | Path | None = None,
) -> dict:
    """Return model state with explicit source categories for the settings UI."""
    if model_id in PARAKEET_MODEL_IDS:
        return get_parakeet_model_status(
            model_id,
            coreml_model_dir=coreml_model_path,
            coreml_cli_path=coreml_cli_path,
        )
    if model_id == "custom":
        validation = validate_whisper_model_path(custom_model_path)
        return {
            "model_id": model_id,
            "ready": validation["ok"],
            "source": "custom_path" if validation["ok"] else "unavailable",
            "state": "ready" if validation["ok"] else "invalid",
            "download_required": False,
            "error": validation["error"],
        }
    if model_id not in WHISPER_MODEL_IDS:
        return {
            "model_id": model_id, "ready": False, "source": "unavailable",
            "state": "unavailable", "download_required": False,
            "error": "不支持的转写模型",
        }
    bundled = _bundled_whisper_model(model_id)
    managed_ready = _managed_whisper_model_ready(model_id)
    return {
        "model_id": model_id,
        "ready": bool(bundled or managed_ready),
        "source": "built_in" if bundled else "app_download",
        "state": "ready" if bundled or managed_ready else "not_downloaded",
        "download_required": not bool(bundled or managed_ready),
        "error": "" if bundled or managed_ready else "首次使用时将下载到 App 模型目录",
    }


def resolve_transcription_model(
    model_size: str | None,
    language: str = "auto",
    *,
    default_model: str | None = None,
    custom_model_path: str | Path | None = None,
    coreml_model_path: str | Path | None = None,
    coreml_cli_path: str | Path | None = None,
) -> ModelResolution:
    """Resolve to a safe runtime model and explain every automatic fallback."""
    requested = (model_size or "auto").strip()
    candidate = requested
    if candidate == "auto":
        configured = (default_model or SAFE_TRANSCRIPTION_MODEL).strip()
        candidate = configured if configured and configured != "auto" else SAFE_TRANSCRIPTION_MODEL

    normalized_language = (language or "auto").lower()
    if candidate.startswith("local:"):
        try:
            from .local_models import validate_imported
            imported = validate_imported(candidate)
            if imported["ready"]:
                return ModelResolution(requested, candidate, imported["path"], "imported_reference")
        except Exception:
            pass
        return ModelResolution(requested, candidate, candidate, "unavailable", "导入模型路径失效，需要重新定位")
    if (
        candidate in PARAKEET_MODEL_IDS
        and normalized_language != "auto"
        and normalized_language not in PARAKEET_SUPPORTED_LANGUAGES
    ):
        return ModelResolution(
            requested, SAFE_TRANSCRIPTION_MODEL, SAFE_TRANSCRIPTION_MODEL, "app_download",
            "Parakeet 不支持所选源语言，已切换到 Whisper Small",
        )

    if candidate == PARAKEET_MODEL_ID:
        status = get_parakeet_model_status(
            candidate,
            coreml_model_dir=coreml_model_path,
            coreml_cli_path=coreml_cli_path,
        )
        if not status["ready"]:
            return ModelResolution(
                requested, SAFE_TRANSCRIPTION_MODEL, SAFE_TRANSCRIPTION_MODEL, "app_download",
                "外部 Core ML 模型或 CLI 无效，已切换到 Whisper Small",
            )
        return ModelResolution(requested, candidate, candidate, status["source"])

    if candidate == PARAKEET_ONNX_MODEL_ID:
        return ModelResolution(requested, candidate, candidate, "app_download")

    if candidate == "custom" or Path(candidate).expanduser().is_absolute():
        selected_path = custom_model_path if candidate == "custom" else candidate
        validation = validate_whisper_model_path(selected_path)
        if validation["ok"]:
            return ModelResolution(
                requested, "custom", str(Path(selected_path).expanduser().resolve()), "custom_path",
            )
        return ModelResolution(
            requested, SAFE_TRANSCRIPTION_MODEL, SAFE_TRANSCRIPTION_MODEL, "app_download",
            "自定义模型目录无效，已切换到 Whisper Small",
        )

    if candidate in WHISPER_MODEL_IDS:
        bundled = _bundled_whisper_model(candidate)
        return ModelResolution(
            requested,
            candidate,
            str(bundled) if bundled else candidate,
            "built_in" if bundled else "app_download",
        )

    return ModelResolution(
        requested, SAFE_TRANSCRIPTION_MODEL, SAFE_TRANSCRIPTION_MODEL, "app_download",
        "所选模型不可用，已切换到 Whisper Small",
    )


def transcribe_audio(task_id: str, audio_path: str, project_id: str, language: str = "auto", model_size: str | None = None, runtime: str | None = None):
    """
    转写音频文件，生成字幕段（segments）。
    增量写入：每生成一个 segment 立即写数据库，前端可实时拉取。
    """
    task_manager.update_task(task_id, step="loading_model", progress=2, message="正在准备转写模型...")
    task_manager.add_log(task_id, "info", "语音转写", "正在准备转写模型...")

    try:
        from .app_settings import get_app_settings
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
    model_id = resolution.model_id
    runtime = runtime or (app_settings.get("transcription_runtime_by_model") or {}).get(model_id)
    runtime = runtime or ("external_coreml" if model_id == PARAKEET_MODEL_ID else "cpu")
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

    if imported and imported["format"] == "memo-coreml":
        session = create_parakeet_session(task_id,audio_path,language,PARAKEET_MODEL_ID,
            coreml_model_dir=imported["path"],coreml_cli_path=imported.get("cli_path"),runtime="external_coreml")
        segments_gen=session.segments; detected_lang=session.detected_language; audio_duration=session.audio_duration
        device=session.device; compute_type=session.compute_type; runtime_model_name=imported["display_name"]; progress_start=session.progress_start
    elif imported and imported["format"] == "sherpa-onnx":
        session = create_parakeet_session(task_id,audio_path,language,PARAKEET_ONNX_MODEL_ID,
            runtime=runtime,onnx_model_dir=imported["path"])
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
        )
        segments_gen = session.segments
        detected_lang = session.detected_language
        audio_duration = session.audio_duration
        device = session.device
        compute_type = session.compute_type
        runtime_model_name = session.model_label
        progress_start = session.progress_start
    elif runtime == "mlx" or (imported and imported["format"] == "mlx"):
        from types import SimpleNamespace
        from huggingface_hub import snapshot_download
        import mlx_whisper
        from ..utils.config import MLX_MODELS_DIR
        if imported:
            model_path=imported["path"]
        else:
            local_dir = MLX_MODELS_DIR / model_id
            local_dir.mkdir(parents=True, exist_ok=True)
            model_path = snapshot_download(repo_id=f"mlx-community/whisper-{model_id}-mlx",local_dir=str(local_dir))
        lang = None if language in ("auto", "") else language
        result = mlx_whisper.transcribe(
            audio_path, path_or_hf_repo=model_path,
            language=lang, word_timestamps=True,
        )
        mlx_segments = [
            SimpleNamespace(
                start=float(item["start"]), end=float(item["end"]), text=str(item["text"]),
                words=item.get("words") or [],
            )
            for item in result.get("segments", [])
        ]
        segments_gen = iter(mlx_segments)
        detected_lang = result.get("language") or lang or "auto"
        audio_duration = max((item.end for item in mlx_segments), default=0.0)
        device, compute_type = "mlx", "MLX"
        runtime_model_name = f"MLX Whisper {model_id}"
        progress_start = 5.0
    else:
        from faster_whisper import WhisperModel
        import ctranslate2

        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        WHISPER_MODELS_DIR.mkdir(parents=True, exist_ok=True)
        model = WhisperModel(
            resolution.load_target,
            device=device,
            compute_type=compute_type,
            download_root=str(WHISPER_MODELS_DIR),
        )
        task_manager.checkpoint(task_id)

        lang = None if language in ("auto", "") else language
        segments_gen, info = model.transcribe(
            audio_path,
            language=lang,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            word_timestamps=True,
        )
        detected_lang = info.language
        audio_duration = info.duration
        runtime_model_name = f"faster-whisper {model_id}"
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
        timings_json = json.dumps(
            _segment_word_timings(segment, time_offset), ensure_ascii=False, separators=(",", ":")
        )

        db_writer = get_db()
        db_writer.execute(
            """INSERT INTO transcription_segments
               (id, run_id, project_id, idx, start, end, text, timings_json, is_draft)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (seg_id, run_id, project_id, generated_count, segment.start + time_offset,
             segment.end + time_offset, text, timings_json)
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
        message=f"转写完成（{detected_lang}），共 {total_final} 条字幕",
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
            "elapsed_seconds": round(time_module.time() - started_at, 2),
            "realtime_factor": round((time_module.time() - started_at) / max(audio_duration, 0.1), 3),
        }
    )
    task_manager.add_log(
        task_id, "info", "转写完成",
        f"语言: {detected_lang}, 共 {total_final} 条字幕, 音频时长: {audio_duration:.1f}秒"
    )

    return processed if draft_segments else []


def _decode_timings(value) -> list[dict]:
    try:
        parsed = json.loads(value or "[]") if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _segment_word_timings(segment, time_offset: float = 0.0) -> list[dict]:
    """Normalize Parakeet/Whisper token objects into one durable timing format."""
    source = getattr(segment, "timings", None) or getattr(segment, "words", None) or []
    result: list[dict] = []
    for item in source:
        getter = item.get if isinstance(item, dict) else lambda key, default=None: getattr(item, key, default)
        text = str(getter("text") or getter("word") or getter("token") or "")
        try:
            start = float(getter("start", getter("startTime", 0.0))) + time_offset
            end = float(getter("end", getter("endTime", start - time_offset + 0.04))) + time_offset
        except (TypeError, ValueError):
            continue
        if not text or end <= start:
            continue
        result.append({"text": text, "start": round(max(0.0, start), 4), "end": round(end, 4)})
    return result


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
    1. 规范非法或重叠的时间区间
    2. 只合并没有完整句界、持续时间 < MIN_DURATION 的相邻碎片
    3. 保留模型识别出的完整语义句，不再按显示长度或时长硬拆

    Returns: (final_segments, merge_count, split_count)
    """
    logger.info("[Transcriber] 后处理: 规范时间轴并保留完整语义句")
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
        timings = [
            item for item in _decode_timings(raw.get("timings", []))
            if isinstance(item, dict) and float(item.get("end", 0) or 0) > start
            and float(item.get("start", 0) or 0) < end
        ]
        normalized.append({"start": start, "end": end, "text": text, "timings": timings})
        previous_end = end

    # 合并极短的非完整碎片，但不跨越明显静音、完整句界，也不制造
    # 超过最大显示时长的段。短而完整的问候或回答必须保持独立。
    merged: List[dict] = []
    i = 0
    while i < len(normalized):
        seg = dict(normalized[i])
        duration = seg["end"] - seg["start"]
        has_sentence_end = _has_sentence_end(seg["text"])
        if duration < MIN_DURATION and not has_sentence_end and merged:
            prev = merged[-1]
            gap = seg["start"] - prev["end"]
            if (
                not _has_sentence_end(prev["text"])
                and gap <= 0.75
                and seg["end"] - prev["start"] <= MAX_DURATION
            ):
                prev["end"] = seg["end"]
                prev["text"] = _smart_merge(prev["text"], seg["text"])
                prev["timings"] = [*prev.get("timings", []), *seg.get("timings", [])]
                merge_count += 1
                i += 1
                continue
        if duration < MIN_DURATION and not has_sentence_end and i + 1 < len(normalized):
            nxt = normalized[i + 1]
            gap = nxt["start"] - seg["end"]
            if gap <= 0.75 and nxt["end"] - seg["start"] <= MAX_DURATION:
                seg["end"] = nxt["end"]
                seg["text"] = _smart_merge(seg["text"], nxt["text"])
                seg["timings"] = [*seg.get("timings", []), *nxt.get("timings", [])]
                merge_count += 1
                i += 1
        merged.append(seg)
        i += 1

    # 长度和显示时长属于质检问题，不应在转写阶段破坏语义句界。旧版在
    # 这里按 42 字/7 秒硬拆，导致 Parakeet 的自然句被放大成大量 ASR
    # 碎片，再让 AI 反向拼接。保留 split_count 供现有任务详情兼容。
    final = merged

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


def _has_sentence_end(text: str) -> bool:
    return bool(re.search(r"[.!?。！？][\"'”’)]?$", (text or "").strip()))


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
        midpoint = len(value) // 2
        whitespace = [match.start() for match in re.finditer(r"\s+", value)]
        cut = min(whitespace, key=lambda position: abs(position - midpoint)) if whitespace else midpoint
        if cut <= 0 or cut >= len(value):
            cut = midpoint
        left = value[:cut].rstrip()
        right = value[cut:].lstrip()
        pieces[idx:idx + 1] = [left, right]
    return [piece for piece in pieces if piece]


def _allocate_piece_times(
    start: float, end: float, pieces: List[str], timings: list[dict] | None = None,
) -> List[dict]:
    """Allocate text pieces on real token boundaries, never equal-duration slices.

    Core ML Parakeet and Whisper expose token/word timestamps.  When an engine
    cannot provide them, character weight is a more faithful fallback than the
    former ``duration / piece_count`` calculation.
    """
    if not pieces:
        return []
    if len(pieces) == 1:
        return [{"start": round(start, 3), "end": round(end, 3), "text": pieces[0]}]

    valid_timings = []
    for item in timings or []:
        try:
            token_start = max(start, float(item.get("start")))
            token_end = min(end, float(item.get("end")))
        except (AttributeError, TypeError, ValueError):
            continue
        token_text = str(item.get("text") or item.get("word") or item.get("token") or "")
        if token_text and token_end > token_start:
            valid_timings.append({"text": token_text, "start": token_start, "end": token_end})
    valid_timings.sort(key=lambda item: (item["start"], item["end"]))

    boundaries: list[float] = []
    if len(valid_timings) >= len(pieces):
        token_weights = [max(1, len(re.sub(r"\s+", "", item["text"]))) for item in valid_timings]
        piece_weights = [max(1, len(re.sub(r"\s+", "", piece))) for piece in pieces]
        total_token_weight = sum(token_weights)
        total_piece_weight = sum(piece_weights)
        token_cumulative = []
        running = 0
        for weight in token_weights:
            running += weight
            token_cumulative.append(running)
        previous_cut = 0
        piece_running = 0
        for piece_index, piece_weight in enumerate(piece_weights[:-1]):
            piece_running += piece_weight
            target = total_token_weight * piece_running / total_piece_weight
            minimum_cut = previous_cut + 1
            maximum_cut = len(valid_timings) - (len(pieces) - piece_index - 1)
            cut = min(
                range(minimum_cut, maximum_cut + 1),
                key=lambda value: abs(token_cumulative[value - 1] - target),
            )
            left = valid_timings[cut - 1]
            right = valid_timings[cut]
            boundary = right["start"] if right["start"] >= left["end"] else left["end"]
            boundaries.append(max(start, min(end, boundary)))
            previous_cut = cut

    if len(boundaries) != len(pieces) - 1:
        # Engines without token timestamps use proportional text weight.  This
        # still avoids falsely implying evenly spaced speech.
        duration = max(0.001, end - start)
        weights = [max(1, len(re.sub(r"\s+", "", piece))) for piece in pieces]
        total_weight = sum(weights)
        running = 0
        boundaries = []
        for weight in weights[:-1]:
            running += weight
            boundaries.append(start + duration * running / total_weight)

    cursor = start
    result = []
    for index, piece in enumerate(pieces):
        piece_end = end if index == len(pieces) - 1 else boundaries[index]
        piece_end = max(cursor + 0.001, min(end, piece_end))
        result.append({"start": round(cursor, 3), "end": round(piece_end, 3), "text": piece})
        cursor = piece_end
    return result
