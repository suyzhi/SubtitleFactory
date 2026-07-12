"""
字幕工厂 - 项目 API 路由
"""

import uuid
import os
import time
import json
import shutil
import logging
import platform
import wave
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

from ..models.database import get_db, init_db, project_to_dict, segment_to_dict
from ..models.schemas import (
    ProjectCreate, ProjectResponse, SegmentResponse,
    ProjectGroupUpdate, SegmentUpdate, ExportRequest, ProcessingConfig,
    WorkflowRequest, TranscriptionRetryRequest,
)
from ..utils.config import PROJECTS_DIR, DOWNLOADS_DIR, MODELS_DIR
from ..utils.task_manager import task_manager
from ..services.downloader import download_video, get_video_info
from ..services.audio_extractor import extract_audio
from ..services.transcriber import (
    PARAKEET_MODEL_IDS,
    SUPPORTED_TRANSCRIPTION_MODELS,
    transcribe_audio,
)
from ..services.parakeet_transcriber import (
    PARAKEET_SUPPORTED_LANGUAGES, PARAKEET_MODEL_ID, PARAKEET_ONNX_MODEL_ID,
    discover_coreml_runtime,
)
from ..services.subtitle_cleaner import clean_subtitles, undo_last_clean
from ..services.subtitle_translator import translate_subtitles
from ..services.subtitle_exporter import (
    export_srt, export_vtt, export_ass,
    get_subtitle_path
)
from ..services.video_renderer import burn_subtitles
from ..services.video_thumbnail import generate_video_thumbnail

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
TRANSCRIPTION_LOCK = threading.Lock()


def _resolve_model(model: str, language: str) -> str:
    if model != "auto":
        return model
    if language not in {"zh", "ja"} and platform.system() == "Darwin" and platform.machine() == "arm64":
        try:
            if discover_coreml_runtime() is not None:
                return PARAKEET_MODEL_ID
        except RuntimeError:
            pass
    return "small"


def _audio_preflight(audio_path: str | None) -> dict:
    if not audio_path or not os.path.isfile(audio_path):
        return {"ok": False, "error_code": "AUDIO_MISSING", "message": "音频尚未提取"}
    try:
        with wave.open(audio_path, "rb") as source:
            duration = source.getnframes() / max(source.getframerate(), 1)
            valid_format = (
                source.getnchannels() == 1 and source.getsampwidth() == 2
                and source.getframerate() == 16000 and source.getcomptype() == "NONE"
            )
    except (OSError, EOFError, wave.Error) as exc:
        return {"ok": False, "error_code": "AUDIO_INVALID", "message": str(exc)}
    if duration <= 0.1:
        return {"ok": False, "error_code": "AUDIO_EMPTY", "message": "音频内容为空"}
    if not valid_format:
        return {"ok": False, "error_code": "AUDIO_FORMAT", "message": "音频需要重新提取为 16kHz 单声道 WAV"}
    free_bytes = shutil.disk_usage(Path(audio_path).parent).free
    if free_bytes < 512 * 1024 * 1024:
        return {"ok": False, "error_code": "DISK_FULL", "message": "可用磁盘空间不足 512 MiB"}
    return {"ok": True, "duration": round(duration, 2), "free_bytes": free_bytes}


@router.get("/transcription/models")
def transcription_models(project_id: Optional[str] = None, language: str = "auto"):
    runtime = None
    runtime_error = None
    try:
        runtime = discover_coreml_runtime()
    except RuntimeError as exc:
        runtime_error = str(exc)
    audio = None
    if project_id:
        db = get_db()
        row = db.execute("SELECT audio_path FROM projects WHERE id=?", (project_id,)).fetchone()
        db.close()
        audio = _audio_preflight(row["audio_path"] if row else None)
    recommended = _resolve_model("auto", language)
    return {
        "recommended_model": recommended,
        "audio": audio,
        "models": [
            {"id": "small", "name": "Whisper Small", "ready": True, "download_required": False,
             "languages": ["auto", "en", "zh", "ja"]},
            {"id": "medium", "name": "Whisper Medium", "ready": True, "download_required": False,
             "languages": ["auto", "en", "zh", "ja"]},
            {"id": "large-v3", "name": "Whisper Large V3", "ready": True, "download_required": False,
             "languages": ["auto", "en", "zh", "ja"]},
            {"id": PARAKEET_MODEL_ID, "name": "Parakeet V3 Core ML",
             "ready": runtime is not None, "download_required": runtime is None,
             "runtime_error": runtime_error, "languages": sorted(PARAKEET_SUPPORTED_LANGUAGES)},
            {"id": PARAKEET_ONNX_MODEL_ID, "name": "Parakeet V3 ONNX",
             "ready": (MODELS_DIR / f"sherpa-onnx-nemo-{PARAKEET_ONNX_MODEL_ID}").is_dir(),
             "download_required": True, "download_bytes": 465 * 1024 * 1024,
             "languages": sorted(PARAKEET_SUPPORTED_LANGUAGES)},
        ],
    }


# ============================
# 项目 CRUD
# ============================

@router.get("/projects")
def list_projects():
    """获取所有项目列表"""
    init_db()
    db = get_db()
    rows = db.execute(
        "SELECT p.*, (SELECT COUNT(*) FROM segments s WHERE s.project_id = p.id) as segments_count "
        "FROM projects p ORDER BY p.updated_at DESC"
    ).fetchall()
    db.close()
    return {
        "projects": [
            {**project_to_dict(r), "segments_count": r["segments_count"]}
            for r in rows
        ]
    }


@router.post("/projects", status_code=201)
def create_project(req: ProjectCreate):
    """创建新项目"""
    init_db()
    project_id = str(uuid.uuid4())
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    db = get_db()
    db.execute(
        """INSERT INTO projects (id, title, source_type, source_url, language, target_language, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (project_id, req.title or "未命名项目", req.source_type, req.source_url,
         req.language, req.target_language, now, now)
    )
    db.commit()
    db.close()

    # 创建项目目录
    os.makedirs(os.path.join(PROJECTS_DIR, project_id), exist_ok=True)

    logger.info(f"[API] 创建项目: {project_id}")
    return {"project_id": project_id, "message": "项目创建成功"}


@router.get("/projects/{project_id}")
def get_project(project_id: str):
    """获取项目详情"""
    db = get_db()
    row = db.execute(
        "SELECT p.*, (SELECT COUNT(*) FROM segments s WHERE s.project_id = p.id) as segments_count "
        "FROM projects p WHERE p.id = ?", (project_id,)
    ).fetchone()
    db.close()

    if not row:
        raise HTTPException(404, "项目不存在")

    return {**project_to_dict(row), "segments_count": row["segments_count"]}


@router.patch("/projects/{project_id}/group", response_model=ProjectResponse)
def update_project_group(project_id: str, update: ProjectGroupUpdate):
    """设置项目分组；null、空字符串或纯空白表示未分组。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    try:
        cursor = db.execute(
            "UPDATE projects SET group_name = ?, updated_at = ? WHERE id = ?",
            (update.group_name, now, project_id),
        )
        if cursor.rowcount == 0:
            db.rollback()
            raise HTTPException(404, "项目不存在")
        row = db.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM segments s WHERE s.project_id = p.id) as segments_count "
            "FROM projects p WHERE p.id = ?",
            (project_id,),
        ).fetchone()
        db.commit()
    finally:
        db.close()

    return {**project_to_dict(row), "segments_count": row["segments_count"]}


# ============================
# 下载 / 导入
# ============================

@router.post("/projects/{project_id}/download")
def start_download(project_id: str, url: str = Form(...)):
    """开始下载 YouTube 视频（后台任务）"""
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "项目不存在")

    # 更新源 URL
    db.execute("UPDATE projects SET source_url = ?, updated_at = ? WHERE id = ?",
               (url, time.strftime("%Y-%m-%d %H:%M:%S"), project_id))
    db.commit()
    db.close()

    task_id = task_manager.create_task(project_id, "download")
    task_manager.run_background(task_id, _do_download, project_id, url)
    return {"task_id": task_id, "message": "下载任务已创建"}


def _do_download(task_id: str, project_id: str, url: str):
    """后台执行下载"""
    video_path = download_video(task_id, url, project_id)
    task_manager.checkpoint(task_id)

    db = get_db()
    try:
        details = (task_manager.get_task(task_id) or {}).get("details", {})
        title = details.get("title") or os.path.basename(video_path)
        thumbnail_url = details.get("thumbnail_url")
        thumbnail_path = details.get("thumbnail_path")
        db.execute(
            """UPDATE projects
               SET video_path = ?, title = ?, thumbnail_url = ?, thumbnail_path = ?, updated_at = ?
               WHERE id = ?""",
            (
                video_path, title, thumbnail_url, thumbnail_path,
                time.strftime("%Y-%m-%d %H:%M:%S"), project_id,
            )
        )
        task_manager.checkpoint(task_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    task_manager.update_task(task_id, step="downloaded", progress=50, message="视频下载完成，可继续提取音频")


@router.post("/projects/{project_id}/import-local")
async def import_local_video(
    project_id: str, file: UploadFile = File(...),
    autostart: bool = Form(False), model: str = Form("auto"), language: str = Form("auto"),
):
    """导入本地视频文件"""
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "项目不存在")

    # 保存上传文件
    project_dir = os.path.join(PROJECTS_DIR, project_id)
    os.makedirs(project_dir, exist_ok=True)

    extension = os.path.splitext(file.filename or "")[1].lower()
    if extension not in VIDEO_EXTENSIONS:
        db.close()
        raise HTTPException(400, "不支持的视频格式；请选择 MP4、MKV、MOV、WebM 或 AVI")
    video_filename = f"local_video{extension}"
    video_path = os.path.join(project_dir, video_filename)

    with open(video_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)

    # 封面提取失败不影响视频导入；数据库会清空旧封面并回退默认图标。
    try:
        thumbnail_path = generate_video_thumbnail(video_path, project_dir)
    except Exception as exc:
        logger.warning("[API] 本地视频封面提取异常，继续导入: %s", exc, exc_info=True)
        thumbnail_path = None

    # 更新项目
    db.execute(
        """UPDATE projects
           SET video_path = ?, source_type = 'local', title = ?,
               thumbnail_url = NULL, thumbnail_path = ?, updated_at = ?
           WHERE id = ?""",
        (
            video_path, file.filename or "本地视频", thumbnail_path,
            time.strftime("%Y-%m-%d %H:%M:%S"), project_id,
        )
    )
    db.commit()
    db.close()

    logger.info(f"[API] 导入本地视频: {video_path}")
    result = {
        "message": "本地视频导入成功",
        "video_path": video_path,
        "thumbnail_url": f"/api/projects/{project_id}/thumbnail" if thumbnail_path else None,
    }
    if autostart is True:
        resolved_model = _resolve_model(model, language)
        task_id = task_manager.create_task(project_id, "workflow")
        task_manager.run_background(
            task_id, _do_workflow, project_id, resolved_model, language, None,
        )
        result.update({"task_id": task_id, "message": "视频已导入，正在自动生成字幕"})
    return result


# ============================
# 音频提取
# ============================

@router.post("/projects/{project_id}/extract-audio")
def start_extract_audio(project_id: str):
    """开始提取音频（后台任务）"""
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()

    if not row:
        raise HTTPException(404, "项目不存在")
    if not row["video_path"] or not os.path.exists(row["video_path"]):
        raise HTTPException(400, "视频文件不存在，请先下载或导入视频")

    task_id = task_manager.create_task(project_id, "extract_audio")
    task_manager.run_background(task_id, _do_extract_audio, project_id, row["video_path"])
    return {"task_id": task_id, "message": "音频提取任务已创建"}


def _do_extract_audio(task_id: str, project_id: str, video_path: str):
    audio_path = extract_audio(task_id, video_path, project_id)
    task_manager.checkpoint(task_id)

    db = get_db()
    try:
        db.execute(
            "UPDATE projects SET audio_path = ?, updated_at = ? WHERE id = ?",
            (audio_path, time.strftime("%Y-%m-%d %H:%M:%S"), project_id)
        )
        task_manager.checkpoint(task_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ============================
# 转写
# ============================

@router.post("/projects/{project_id}/transcribe")
def start_transcribe(project_id: str, language: str = Form("auto"), model: str = Form("small")):
    """开始转写音频（后台任务）"""
    model = _resolve_model(model, language)
    if model not in SUPPORTED_TRANSCRIPTION_MODELS:
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
        (language, time.strftime("%Y-%m-%d %H:%M:%S"), project_id)
    )
    db.commit()
    db.close()

    task_id = task_manager.create_task(project_id, "transcribe")
    task_manager.run_background(task_id, _do_transcribe, project_id, row["audio_path"], language, model)
    return {"task_id": task_id, "message": "转写任务已创建"}


def _do_transcribe(task_id: str, project_id: str, audio_path: str, language: str, model: str):
    task_manager.update_task(task_id, message="等待本地转写引擎")
    while not TRANSCRIPTION_LOCK.acquire(timeout=0.25):
        task_manager.checkpoint(task_id)
    try:
        task_manager.checkpoint(task_id)
        try:
            transcribe_audio(task_id, audio_path, project_id, language, model)
        except Exception as exc:
            transient = any(token in str(exc).lower() for token in (
                "timeout", "timed out", "temporarily", "connection", "连接", "503",
            ))
            if not transient:
                raise
            task_manager.update_task(
                task_id, attempt=2, message="遇到临时错误，正在自动重试一次",
                details={"retry_reason": str(exc)},
            )
            task_manager.add_log(task_id, "warning", "语音转写", "临时错误，自动重试一次", detail=str(exc))
            transcribe_audio(task_id, audio_path, project_id, language, model)
    finally:
        TRANSCRIPTION_LOCK.release()


@router.post("/projects/{project_id}/workflow")
def start_workflow(project_id: str, request: WorkflowRequest):
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "项目不存在")
    source_url = request.source_url or row["source_url"]
    if not row["video_path"] and not source_url:
        raise HTTPException(400, "项目没有可处理的视频或链接")
    model = _resolve_model(request.model, request.language)
    task_id = task_manager.create_task(project_id, "workflow")
    task_manager.run_background(
        task_id, _do_workflow, project_id, model, request.language,
        source_url if not row["video_path"] else None,
    )
    return {"task_id": task_id, "message": "自动字幕工作流已创建", "model": model}


def _do_workflow(
    task_id: str, project_id: str, model: str, language: str, source_url: str | None,
):
    stages = {
        "download": "waiting", "extract_audio": "waiting", "transcribe": "waiting",
    }
    if source_url:
        stages["download"] = "running"
        task_manager.update_task(task_id, step="download", details={"stages": stages})
        _do_download(task_id, project_id, source_url)
        stages["download"] = "success"
    else:
        stages["download"] = "success"

    db = get_db()
    row = db.execute("SELECT video_path,audio_path FROM projects WHERE id=?", (project_id,)).fetchone()
    db.close()
    if not row or not row["video_path"]:
        raise RuntimeError("工作流未找到可用视频")

    audio_check = _audio_preflight(row["audio_path"])
    if not audio_check["ok"]:
        stages["extract_audio"] = "running"
        task_manager.update_task(task_id, step="extract_audio", details={"stages": stages})
        _do_extract_audio(task_id, project_id, row["video_path"])
    stages["extract_audio"] = "success"

    db = get_db()
    audio_path = db.execute("SELECT audio_path FROM projects WHERE id=?", (project_id,)).fetchone()["audio_path"]
    db.close()
    stages["transcribe"] = "running"
    task_manager.update_task(task_id, step="transcribe", details={"stages": stages, "resolved_model": model})
    _do_transcribe(task_id, project_id, audio_path, language, model)
    stages["transcribe"] = "success"
    task_manager.update_task(
        task_id, step="workflow_done", progress=100,
        message="字幕已生成，可以开始编辑", details={"stages": stages},
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
    task_id = task_manager.create_task(project_id, "transcribe")
    task_manager.run_background(task_id, _do_transcribe, project_id, row["audio_path"], request.language, model)
    return {"task_id": task_id, "message": "转写重试任务已创建", "model": model}


# ============================
# AI 整理
# ============================

@router.post("/projects/{project_id}/clean")
def start_clean(project_id: str, target_length: int = Form(42)):
    """开始 AI 整理字幕（后台任务）"""
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()

    if not row:
        raise HTTPException(404, "项目不存在")

    if target_length < 16 or target_length > 100:
        raise HTTPException(400, "目标单句长度必须在 16 到 100 个字符之间")
    task_id = task_manager.create_task(project_id, "clean")
    task_manager.run_background(task_id, _do_clean, project_id, target_length)
    return {"task_id": task_id, "message": "AI 整理任务已创建"}


def _do_clean(task_id: str, project_id: str, target_length: int):
    clean_subtitles(task_id, project_id, target_length)


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


# ============================
# AI 翻译
# ============================

@router.post("/projects/{project_id}/translate")
def start_translate(project_id: str, target_language: str = Form("zh")):
    """开始 AI 翻译字幕（后台任务）"""
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()

    if not row:
        raise HTTPException(404, "项目不存在")

    db = get_db()
    db.execute(
        "UPDATE projects SET target_language = ?, updated_at = ? WHERE id = ?",
        (target_language, time.strftime("%Y-%m-%d %H:%M:%S"), project_id)
    )
    db.commit()
    db.close()

    task_id = task_manager.create_task(project_id, "translate")
    task_manager.run_background(task_id, _do_translate, project_id, target_language)
    return {"task_id": task_id, "message": "AI 翻译任务已创建"}


def _do_translate(task_id: str, project_id: str, target_language: str):
    translate_subtitles(task_id, project_id, target_language)


# ============================
# 字幕段管理
# ============================

@router.get("/projects/{project_id}/segments")
def get_segments(project_id: str, after_idx: int = 0):
    """获取项目的所有字幕段。after_idx>0 时只返回比该索引新的字幕（增量模式）。"""
    db = get_db()
    if after_idx > 0:
        rows = db.execute(
            "SELECT * FROM segments WHERE project_id = ? AND idx > ? ORDER BY idx",
            (project_id, after_idx)
        ).fetchall()
        # 获取当前最大 idx
        max_row = db.execute(
            "SELECT MAX(idx) as max_idx FROM segments WHERE project_id = ?",
            (project_id,)
        ).fetchone()
        latest_idx = max_row["max_idx"] if max_row and max_row["max_idx"] else after_idx

        # 检查是否还有 is_draft=1 的 segments
        draft_row = db.execute(
            "SELECT COUNT(*) as cnt FROM segments WHERE project_id = ? AND is_draft = 1",
            (project_id,)
        ).fetchone()
        has_more = draft_row["cnt"] > 0 if draft_row else False
    else:
        rows = db.execute(
            "SELECT * FROM segments WHERE project_id = ? ORDER BY idx",
            (project_id,)
        ).fetchall()
        latest_idx = len(rows)
        has_more = False

    db.close()

    segments = [segment_to_dict(r) for r in rows]

    result = {
        "segments": segments,
        "total": len(rows),
    }
    if after_idx > 0:
        result["latest_idx"] = latest_idx
        result["has_more"] = has_more

    return result


@router.patch("/projects/{project_id}/segments/{segment_index}")
def update_segment(project_id: str, segment_index: int, update: SegmentUpdate):
    """修改某一条字幕"""
    db = get_db()
    row = db.execute(
        "SELECT * FROM segments WHERE project_id = ? AND idx = ?",
        (project_id, segment_index)
    ).fetchone()

    if not row:
        db.close()
        raise HTTPException(404, "字幕段不存在")

    updates = {}
    if update.clean_text is not None:
        updates["clean_text"] = update.clean_text
    if update.translated_text is not None:
        updates["translated_text"] = update.translated_text
    if update.locked is not None:
        updates["locked"] = 1 if update.locked else 0

    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [project_id, segment_index]
        db.execute(
            f"UPDATE segments SET {set_clause} WHERE project_id = ? AND idx = ?",
            values
        )
        db.commit()

    updated_row = db.execute(
        "SELECT * FROM segments WHERE project_id = ? AND idx = ?",
        (project_id, segment_index)
    ).fetchone()
    db.close()

    return segment_to_dict(updated_row)


# ============================
# 导出
# ============================

@router.post("/projects/{project_id}/export")
def export_subtitles(project_id: str, req: ExportRequest):
    """导出字幕或压制视频"""
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    segments_rows = db.execute(
        "SELECT * FROM segments WHERE project_id = ? ORDER BY idx",
        (project_id,)
    ).fetchall()
    db.close()

    if not row:
        raise HTTPException(404, "项目不存在")
    if not segments_rows:
        raise HTTPException(400, "没有字幕数据")

    segments = [segment_to_dict(r) for r in segments_rows]

    fmt = req.format
    bilingual = req.bilingual

    if fmt == "srt":
        out = get_subtitle_path(project_id, "srt")
        export_srt(segments, out, bilingual=bilingual, primary_lang=req.primary_language)
        media_type = "text/plain"
    elif fmt == "vtt":
        out = get_subtitle_path(project_id, "vtt")
        export_vtt(segments, out, bilingual=bilingual, primary_lang=req.primary_language)
        media_type = "text/vtt"
    elif fmt == "ass":
        out = get_subtitle_path(project_id, "ass")
        export_ass(segments, out, bilingual=bilingual, primary_lang=req.primary_language)
        media_type = "text/plain"
    elif fmt == "srt-bilingual":
        out = get_subtitle_path(project_id, "bilingual.srt")
        export_srt(segments, out, bilingual=True, primary_lang=req.primary_language)
        media_type = "text/plain"
    elif fmt in {"mp4", "mkv"}:
        if not row["video_path"] or not os.path.exists(row["video_path"]):
            raise HTTPException(400, "视频文件不存在，无法压制")

        # 先导出 ASS 字幕
        ass_path = get_subtitle_path(project_id, "ass")
        export_ass(segments, ass_path, bilingual=bilingual, primary_lang=req.primary_language)

        # 后台压制
        task_id = task_manager.create_task(project_id, "render")
        from ..utils.config import EXPORTS_DIR
        output_path = os.path.join(EXPORTS_DIR, f"{project_id}_hardsub.{fmt}")
        task_manager.run_background(
            task_id, burn_subtitles,
            row["video_path"], ass_path, output_path, project_id
        )
        return {"task_id": task_id, "message": f"{fmt.upper()} 视频导出任务已创建"}
    else:
        raise HTTPException(400, f"不支持的导出格式: {fmt}")

    return {"path": out, "message": f"字幕已导出: {fmt}"}


@router.get("/projects/{project_id}/export/download")
def download_export(project_id: str, fmt: str = "srt"):
    """下载已导出的字幕文件"""
    if fmt in {"mp4", "mkv"}:
        from ..utils.config import EXPORTS_DIR
        filepath = os.path.join(EXPORTS_DIR, f"{project_id}_hardsub.{fmt}")
    else:
        ext = fmt.replace("srt-bilingual", "bilingual.srt")
        filepath = get_subtitle_path(project_id, ext)
    if not os.path.exists(filepath):
        raise HTTPException(404, "导出文件不存在，请先导出")
    return FileResponse(filepath, filename=os.path.basename(filepath))


# ============================
# 视频/音频文件访问
# ============================

@router.get("/projects/{project_id}/video")
def get_video_file(project_id: str):
    """获取视频文件（供前端视频播放器使用）"""
    db = get_db()
    row = db.execute("SELECT video_path FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()
    if not row or not row["video_path"] or not os.path.exists(row["video_path"]):
        raise HTTPException(404, "视频文件不存在")
    return FileResponse(row["video_path"])


@router.get("/projects/{project_id}/thumbnail")
def get_thumbnail_file(project_id: str):
    """获取项目的持久化视频封面。"""
    db = get_db()
    row = db.execute(
        "SELECT thumbnail_path FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    db.close()
    if not row or not row["thumbnail_path"] or not os.path.isfile(row["thumbnail_path"]):
        raise HTTPException(404, "视频封面不存在")
    return FileResponse(
        row["thumbnail_path"],
        headers={"Cache-Control": "no-cache"},
    )
