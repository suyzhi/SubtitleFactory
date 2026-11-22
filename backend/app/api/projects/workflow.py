"""
字幕工厂 - 语音转写、一键工作流、AI 整理与翻译路由
"""

import os
import time
from typing import Optional

from fastapi import APIRouter, Form, HTTPException

from ...models.schemas import TranscriptionRetryRequest, WorkflowRequest
from ...services.app_settings import save_app_settings
from ...services.cloud_asr import FUN_ASR_MODEL_ID
from ...services.distribution import require_youtube_feature
from ...services.local_models import get_imported
from ...services.parakeet_transcriber import PARAKEET_SUPPORTED_LANGUAGES
from ...services.subtitle_cleaner import clean_subtitles, undo_last_clean
from ...services.subtitle_translator import translate_subtitles
from ...services.transcriber import (
    PARAKEET_MODEL_IDS,
    SUPPORTED_TRANSCRIPTION_MODELS,
)
from .common import (
    _audio_preflight,
    facade_attr,
    get_app_settings,
    get_db,
    get_task_manager,
    run_transcribe_audio,
)
from .media import _do_download, _do_extract_audio
from .models import _resolve_model, _runtime_ids, _select_runtime

router = APIRouter()


@router.post("/projects/{project_id}/transcribe")
def start_transcribe(
    project_id: str,
    language: str = Form("auto"),
    model: str = Form("small"),
    runtime: Optional[str] = Form(None),
):
    """开始转写音频（后台任务）"""
    # Direct Python callers (including the compatibility test suite) receive
    # FastAPI's Form sentinel instead of a parsed request value.
    direct_call_sentinel = runtime is not None and not isinstance(runtime, str)
    if not isinstance(runtime, str):
        runtime = None
    model = _resolve_model(model, language)
    if model not in SUPPORTED_TRANSCRIPTION_MODELS and not model.startswith("local:"):
        raise HTTPException(400, "不支持的转写模型")
    normalized_language = (language or "auto").lower()
    if (
        model in PARAKEET_MODEL_IDS
        and normalized_language != "auto"
        and normalized_language not in PARAKEET_SUPPORTED_LANGUAGES
    ):
        raise HTTPException(
            400,
            "Parakeet TDT v3 不支持所选源语言；请选择自动检测或英语等欧洲语言",
        )

    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()

    if not row:
        raise HTTPException(404, "项目不存在")
    if not row["audio_path"] or not os.path.exists(row["audio_path"]):
        raise HTTPException(400, "音频文件不存在，请先提取音频")
    preflight = _audio_preflight(row["audio_path"])
    if not preflight["ok"]:
        raise HTTPException(400, f"{preflight['error_code']}: {preflight['message']}")

    # 更新语言
    db = get_db()
    db.execute(
        "UPDATE projects SET language = ?, updated_at = ? WHERE id = ?",
        (language, time.strftime("%Y-%m-%d %H:%M:%S"), project_id),
    )
    db.commit()
    db.close()

    settings = get_app_settings()
    imported = get_imported(model) if model.startswith("local:") else None
    if direct_call_sentinel and not runtime:
        runtime = _runtime_ids(model, imported)[0]
    runtime = _select_runtime(model, runtime, settings, imported)
    mapping = dict(settings.get("transcription_runtime_by_model") or {})
    mapping[model] = runtime
    save_app_settings({"transcription_runtime_by_model": mapping})
    tm = get_task_manager()
    task_id = tm.create_task(project_id, "transcribe", resource_class="workflow")
    tm.update_task(
        task_id,
        details={"model_id": model, "runtime": runtime, "language": language},
    )
    tm.run_background(
        task_id,
        _do_prepared_transcription,
        project_id,
        row["audio_path"],
        language,
        model,
        runtime,
    )
    return {"task_id": task_id, "message": "转写任务已创建"}


def _do_prepared_transcription(
    task_id: str,
    project_id: str,
    audio_path: str,
    language: str,
    model: str,
    runtime: str | None = None,
):
    # Standalone retries obey the same source/track/range cache contract as workflows.
    db = get_db()
    row = db.execute("SELECT video_path FROM projects WHERE id=?", (project_id,)).fetchone()
    db.close()
    if row and row["video_path"] and os.path.isfile(row["video_path"]):
        facade_attr("_do_extract_audio", _do_extract_audio)(task_id, project_id, row["video_path"])
        db = get_db()
        audio_path = db.execute(
            "SELECT audio_path FROM projects WHERE id=?", (project_id,)
        ).fetchone()["audio_path"]
        db.close()
    facade_attr("_do_transcribe", _do_transcribe)(
        task_id, project_id, audio_path, language, model, runtime
    )


def _do_transcribe(
    task_id: str,
    project_id: str,
    audio_path: str,
    language: str,
    model: str,
    runtime: str | None = None,
):
    tm = get_task_manager()
    tm.update_task(task_id, message="等待本地转写引擎")
    tm.checkpoint(task_id)
    try:
        run_transcribe_audio(task_id, audio_path, project_id, language, model, runtime)
    except Exception as exc:
        # A cloud timeout can occur after the provider has already processed
        # a billable chunk. Never submit it again without a new user action.
        transient = model != FUN_ASR_MODEL_ID and any(
            token in str(exc).lower()
            for token in ("timeout", "timed out", "temporarily", "connection", "连接", "503")
        )
        if not transient:
            raise
        tm.update_task(
            task_id,
            attempt=2,
            message="遇到临时错误，正在自动重试一次",
            details={"retry_reason": str(exc)},
        )
        tm.add_log(task_id, "warning", "语音转写", "临时错误，自动重试一次", detail=str(exc))
        run_transcribe_audio(task_id, audio_path, project_id, language, model, runtime)


@router.post("/projects/{project_id}/workflow")
def start_workflow(project_id: str, request: WorkflowRequest):
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "项目不存在")
    if row["source_type"] == "youtube":
        require_youtube_feature()
    source_url = request.source_url or row["source_url"]
    video_ready = bool(row["video_path"] and os.path.isfile(row["video_path"]))
    if not video_ready and not source_url:
        raise HTTPException(400, "项目没有可处理的视频或链接")
    model = _resolve_model(request.model, request.language)
    settings = get_app_settings()
    imported = get_imported(model) if model.startswith("local:") else None
    runtime = _select_runtime(model, request.runtime, settings, imported)
    mapping = dict(settings.get("transcription_runtime_by_model") or {})
    mapping[model] = runtime
    save_app_settings({"transcription_runtime_by_model": mapping})
    if (request.enable_clean or request.enable_translate) and not request.text_processing_consent:
        raise HTTPException(400, "请确认按设置中的服务商发送字幕文本后再启用整理或翻译")
    options = {
        key: getattr(request, key)
        for key in (
            "enable_clean",
            "enable_translate",
            "target_language",
            "clean_target_length",
            "text_processing_consent",
        )
    }
    tm = get_task_manager()
    previous = None
    if request.resume_task_id:
        original = tm.get_task(request.resume_task_id)
        if (
            not original
            or original.get("project_id") != project_id
            or original.get("status") not in ("failed", "cancelled", "partial")
        ):
            raise HTTPException(409, "这个任务当前不能重试")
        previous = original.get("details") or {}
    if tm.active_task_ids(project_id):
        raise HTTPException(409, "当前项目已有任务正在处理，请完成或取消后再开始")
    task_id = tm.create_task(project_id, "workflow")
    should_download_source = source_url if not video_ready else None
    tm.update_task(
        task_id,
        details={
            "resume_policy": "automatic_local_only",
            "resume_payload": {
                "project_id": project_id,
                "model": model,
                "language": request.language,
                "source_url": should_download_source,
                "runtime": runtime,
                "options": options,
            },
        },
    )
    tm.run_background(
        task_id,
        facade_attr("_do_workflow", _do_workflow),
        project_id,
        model,
        request.language,
        should_download_source,
        runtime,
        options,
        previous,
    )
    return {"task_id": task_id, "message": "自动字幕工作流已创建", "model": model}


def _do_workflow(
    task_id: str,
    project_id: str,
    model: str,
    language: str,
    source_url: str | None,
    runtime: str,
    options: dict | None = None,
    previous: dict | None = None,
):
    tm = get_task_manager()
    options = options or {}
    previous = previous or {}
    stages = {
        "download": "waiting",
        "extract_audio": "waiting",
        "transcribe": "waiting",
    }
    db = get_db()
    row = db.execute(
        "SELECT source_url,video_path,audio_path,media_mode FROM projects WHERE id=?",
        (project_id,),
    ).fetchone()
    db.close()
    if not row:
        raise RuntimeError("工作流未找到项目")

    if source_url and not (row["video_path"] and os.path.isfile(row["video_path"])):
        stages["download"] = "running"
        tm.update_task(task_id, step="download", details={"stages": stages})
        with tm.resource_slot(task_id, "io"):
            facade_attr("_do_download", _do_download)(
                task_id,
                project_id,
                source_url,
                progress_start=2,
                progress_end=45,
                completed_progress=45,
            )
    stages["download"] = "success"
    db = get_db()
    row = db.execute(
        "SELECT video_path,audio_path FROM projects WHERE id=?",
        (project_id,),
    ).fetchone()
    db.close()
    if not row or not row["video_path"] or not os.path.isfile(row["video_path"]):
        raise RuntimeError("工作流未找到可用视频")
    # Always validate source, track, range and output signatures. The extractor
    # returns immediately for a valid cache; mere file existence is insufficient.
    stages["extract_audio"] = "running"
    tm.update_task(task_id, step="extract_audio", details={"stages": stages})
    facade_attr("_do_extract_audio", _do_extract_audio)(
        task_id, project_id, row["video_path"], progress_start=45, progress_end=78
    )
    stages["extract_audio"] = "success"

    db = get_db()
    prepared = dict(
        db.execute(
            "SELECT audio_path,audio_track_index,range_start,range_end,edit_revision "
            "FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
    )
    db.close()
    audio_path = prepared["audio_path"]
    stat = os.stat(audio_path)
    identity = [
        audio_path,
        stat.st_size,
        stat.st_mtime_ns,
        prepared["audio_track_index"],
        prepared["range_start"],
        prepared["range_end"],
        model,
        language,
        runtime,
    ]
    prior_stages = previous.get("stages") or {}
    reuse = (
        previous.get("preparation_identity") == identity
        and prior_stages.get("transcribe") == "success"
    )
    if reuse and previous.get("result_revision") != prepared["edit_revision"]:
        raise RuntimeError("字幕在上次任务后已经修改，请从任务面板单独运行所需的整理或翻译步骤")
    tm.update_task(task_id, details={"preparation_identity": identity})
    if not reuse:
        stages["transcribe"] = "running"
        tm.update_task(
            task_id,
            step="transcribe",
            details={"stages": stages, "resolved_model": model},
        )
        facade_attr("_do_transcribe", _do_transcribe)(
            task_id, project_id, audio_path, language, model, runtime
        )
    stages["transcribe"] = "success"

    def save_stage_state():
        conn = get_db()
        revision = conn.execute(
            "SELECT edit_revision FROM projects WHERE id=?", (project_id,)
        ).fetchone()[0]
        conn.close()
        tm.update_task(task_id, details={"stages": stages, "result_revision": revision})

    save_stage_state()
    candidate = bool(((tm.get_task(task_id) or {}).get("details") or {}).get("candidate_ready"))
    if candidate:
        tm.update_task(
            task_id,
            step="workflow_done",
            progress=100,
            message="新转写候选已生成；比较并采用后可继续整理和翻译",
            details={"stages": stages},
        )
        return
    for stage in ("clean", "translate"):
        if not options.get("enable_" + stage):
            continue
        if not options.get("text_processing_consent"):
            raise RuntimeError("整理和翻译需要明确确认字幕文本处理")
        if reuse and prior_stages.get(stage) == "success":
            stages[stage] = "success"
            continue
        stages[stage] = "running"
        tm.update_task(task_id, step=stage, details={"stages": stages})
        try:
            with tm.resource_slot(task_id, "network_ai"):
                if stage == "clean":
                    facade_attr("_do_clean", _do_clean)(
                        task_id, project_id, int(options.get("clean_target_length", 42))
                    )
                else:
                    facade_attr("_do_translate", _do_translate)(
                        task_id, project_id, options.get("target_language") or "zh"
                    )
            if (tm.get_task(task_id) or {}).get("status") == "partial":
                stages[stage] = "partial"
                save_stage_state()
                return
            stages[stage] = "success"
        except Exception:
            stages[stage] = "failed"
            raise
        finally:
            save_stage_state()
    tm.update_task(
        task_id,
        step="workflow_done",
        progress=100,
        message="字幕流程已完成，可以开始校对和导出",
        details={"stages": stages},
    )


@router.post("/projects/{project_id}/transcribe/retry")
def retry_transcription(project_id: str, request: TranscriptionRetryRequest):
    """Explicit retry/fallback confirmation endpoint used by the recovery UI."""
    model = _resolve_model(request.model, request.language)
    db = get_db()
    row = db.execute("SELECT audio_path FROM projects WHERE id=?", (project_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "项目不存在")
    preflight = _audio_preflight(row["audio_path"])
    if not preflight["ok"]:
        raise HTTPException(400, f"{preflight['error_code']}: {preflight['message']}")
    settings = get_app_settings()
    imported = get_imported(model) if model.startswith("local:") else None
    runtime = _select_runtime(model, request.runtime, settings, imported)
    tm = get_task_manager()
    task_id = tm.create_task(project_id, "transcribe", resource_class="workflow")
    tm.update_task(
        task_id,
        details={"model_id": model, "runtime": runtime, "language": request.language},
    )
    tm.run_background(
        task_id,
        _do_prepared_transcription,
        project_id,
        row["audio_path"],
        request.language,
        model,
        runtime,
    )
    return {"task_id": task_id, "message": "转写重试任务已创建", "model": model}


@router.post("/projects/{project_id}/clean")
def start_clean(
    project_id: str,
    target_length: int = Form(42),
    provider_id: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
):
    """开始 AI 整理字幕（后台任务）"""
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()

    if not row:
        raise HTTPException(404, "项目不存在")

    if target_length < 16 or target_length > 100:
        raise HTTPException(400, "目标单句长度必须在 16 到 100 个字符之间")
    db = get_db()
    existing = db.execute(
        "SELECT id FROM tasks WHERE project_id=? AND type='clean' "
        "AND status IN ('pending','running','paused') ORDER BY created_at DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    db.close()
    if existing:
        return {"task_id": existing["id"], "message": "AI 整理已在运行", "existing": True}
    tm = get_task_manager()
    task_id = tm.create_task(project_id, "clean")
    tm.update_task(
        task_id,
        details={
            "target_length": target_length,
            "provider_id": provider_id,
            "model": model,
        },
    )
    tm.run_background(
        task_id, facade_attr("_do_clean", _do_clean), project_id, target_length, provider_id, model
    )
    return {"task_id": task_id, "message": "AI 整理任务已创建"}


def _do_clean(
    task_id: str,
    project_id: str,
    target_length: int,
    provider_id: str | None = None,
    model: str | None = None,
):
    clean_subtitles(task_id, project_id, target_length, provider_id, model)


@router.post("/projects/{project_id}/clean/undo")
def undo_clean(project_id: str):
    """撤销最近一次 AI 句子重组。"""
    db = get_db()
    exists = db.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()
    if not exists:
        raise HTTPException(404, "项目不存在")
    try:
        restored = undo_last_clean(project_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"message": f"已恢复 {restored} 条整理前字幕", "segments_count": restored}


@router.post("/projects/{project_id}/translate")
def start_translate(
    project_id: str,
    target_language: str = Form("zh"),
    provider_id: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
):
    """开始 AI 翻译字幕（后台任务）"""
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()

    if not row:
        raise HTTPException(404, "项目不存在")

    db = get_db()
    db.execute(
        "UPDATE projects SET target_language = ?, updated_at = ? WHERE id = ?",
        (target_language, time.strftime("%Y-%m-%d %H:%M:%S"), project_id),
    )
    db.commit()
    db.close()

    tm = get_task_manager()
    task_id = tm.create_task(project_id, "translate")
    tm.update_task(
        task_id,
        details={
            "target_language": target_language,
            "provider_id": provider_id,
            "model": model,
        },
    )
    tm.run_background(
        task_id,
        facade_attr("_do_translate", _do_translate),
        project_id,
        target_language,
        provider_id,
        model,
    )
    return {"task_id": task_id, "message": "AI 翻译任务已创建"}


def _do_translate(
    task_id: str,
    project_id: str,
    target_language: str,
    provider_id: str | None = None,
    model: str | None = None,
):
    translate_subtitles(task_id, project_id, target_language, provider_id, model)
