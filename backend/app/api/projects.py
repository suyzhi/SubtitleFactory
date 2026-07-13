"""
字幕工厂 - 项目 API 路由
"""

import uuid
import os
import time
import json
import shutil
import logging
import wave
import threading
import importlib.util
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse

from ..models.database import get_db, init_db, project_to_dict, segment_to_dict
from ..models.schemas import (
    ProjectCreate, ProjectResponse, ProjectUpdate, SegmentResponse,
    ProjectGroupUpdate, SegmentUpdate, ExportRequest, ProcessingConfig,
    WorkflowRequest, TranscriptionRetryRequest, ModelPrepareRequest,
    ModelScanRequest, ModelImportRequest,
)
from ..utils.config import (
    DATA_DIR, PROJECTS_DIR, DOWNLOADS_DIR, AUDIO_DIR, SUBTITLES_DIR,
    EXPORTS_DIR,
)
from ..utils.task_manager import task_manager
from ..services.app_settings import get_app_settings
from ..services.downloader import download_video, get_video_info, normalize_youtube_url
from ..services.audio_extractor import extract_audio
from ..services.transcriber import (
    PARAKEET_MODEL_IDS,
    SUPPORTED_TRANSCRIPTION_MODELS,
    get_transcription_model_status,
    resolve_transcription_model,
    transcribe_audio,
)
from ..services.parakeet_transcriber import (
    PARAKEET_SUPPORTED_LANGUAGES, PARAKEET_MODEL_ID, PARAKEET_ONNX_MODEL_ID,
    prepare_parakeet_model,
)
from ..services.subtitle_cleaner import clean_subtitles, undo_last_clean
from ..services.subtitle_translator import translate_subtitles
from ..services.subtitle_exporter import (
    export_srt, export_vtt, export_ass,
    get_subtitle_path
)
from ..services.video_renderer import burn_subtitles
from ..services.video_thumbnail import generate_video_thumbnail
from ..services.local_models import scan_models, register_model, get_imported, validate_imported, remove_imported

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
TRANSCRIPTION_LOCK = threading.Lock()

RUNTIME_LABELS = {
    "cpu": ("CPU", "faster-whisper / sherpa-onnx"),
    "mlx": ("Apple GPU", "MLX Whisper · Metal"),
    "coreml": ("Apple Neural Engine / GPU", "sherpa-onnx · Core ML"),
    "external_coreml": ("外部 Core ML", "Memo Parakeet CLI"),
}

def _runtime_ids(model_id: str, imported: dict | None = None) -> list[str]:
    if imported: return list(imported.get("runtimes") or [])
    if model_id in {"small", "medium", "large-v3"}: return ["cpu", "mlx"]
    if model_id == PARAKEET_MODEL_ID: return ["external_coreml"]
    if model_id == PARAKEET_ONNX_MODEL_ID: return ["cpu", "coreml"]
    return ["cpu"]

def _runtime_available(runtime_id: str) -> tuple[bool, str]:
    if runtime_id == "mlx":
        ok = importlib.util.find_spec("mlx_whisper") is not None
        return ok, "MLX Whisper 已随 App 提供" if ok else "当前运行包缺少 MLX Whisper"
    if runtime_id == "coreml":
        try:
            import onnxruntime
            ok = "CoreMLExecutionProvider" in onnxruntime.get_available_providers()
        except Exception:
            ok = False
        return ok, "Core ML Execution Provider 可用" if ok else "当前 ONNX Runtime 不支持 Core ML"
    return True, "可用"

def _runtime_options(model_id: str, imported: dict | None = None, model_ready: bool = True) -> list[dict]:
    result=[]
    for runtime_id in _runtime_ids(model_id, imported):
        available, reason = _runtime_available(runtime_id)
        if (runtime_id == "external_coreml" or imported) and not model_ready:
            available, reason = False, "外部模型路径或配套 CLI 需要重新校验"
        label, engine = RUNTIME_LABELS.get(runtime_id, (runtime_id, runtime_id))
        result.append({"id":runtime_id,"name":label,"engine":engine,"available":available,"reason":reason})
    return result

def _select_runtime(model_id: str, requested: str | None, settings: dict, imported: dict | None = None) -> str:
    remembered=(settings.get("transcription_runtime_by_model") or {}).get(model_id)
    selected=requested or remembered
    model_ready=True
    if imported:
        model_ready=bool(validate_imported(model_id).get("ready"))
    elif model_id==PARAKEET_MODEL_ID:
        model_ready=bool(get_transcription_model_status(model_id,coreml_model_path=settings.get("coreml_model_path"),coreml_cli_path=settings.get("coreml_cli_path")).get("ready"))
    options=_runtime_options(model_id, imported, model_ready); allowed={item["id"]:item for item in options}
    if not selected:
        raise HTTPException(409, detail={"code":"RUNTIME_SELECTION_REQUIRED","message":"首次使用此模型前请选择运行设备","model_id":model_id,"runtimes":options})
    if selected not in allowed:
        raise HTTPException(400, detail={"code":"RUNTIME_NOT_SUPPORTED","message":"所选运行设备不支持当前模型","model_id":model_id,"runtimes":options})
    if not allowed[selected]["available"]:
        raise HTTPException(409, detail={"code":"RUNTIME_UNAVAILABLE","message":allowed[selected]["reason"],"model_id":model_id,"runtimes":options})
    return selected


def _project_row(project_id: str):
    db = get_db()
    try:
        return db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    finally:
        db.close()


def _active_task_conflict(project_id: str) -> list[str]:
    return task_manager.active_task_ids(project_id)


def _raise_active_task_conflict(task_ids: list[str]) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "code": "ACTIVE_TASKS",
            "message": "项目正在处理；确认终止任务后再移入回收站",
            "task_ids": task_ids,
        },
    )


def _path_is_managed(path: Path) -> bool:
    """Only remove files below App-owned or user-selected storage roots."""
    roots = {
        Path(DATA_DIR), Path(PROJECTS_DIR), Path(DOWNLOADS_DIR), Path(AUDIO_DIR),
        Path(SUBTITLES_DIR), Path(EXPORTS_DIR),
    }
    try:
        custom_download_root = get_app_settings().get("download_directory")
    except Exception:
        custom_download_root = None
    if custom_download_root:
        roots.add(Path(custom_download_root))
    resolved = path.resolve(strict=False)
    for root in roots:
        resolved_root = root.expanduser().resolve(strict=False)
        if resolved == resolved_root:
            continue
        try:
            resolved.relative_to(resolved_root)
            return True
        except ValueError:
            continue
    return False


def _remove_managed_path(path: Path) -> None:
    if not _path_is_managed(path) or not path.exists():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _purge_project_files(row) -> None:
    """Delete only App-managed media, subtitles, thumbnails, and exports."""
    project_id = row["id"]
    candidates = {
        Path(PROJECTS_DIR) / project_id,
        Path(DOWNLOADS_DIR) / project_id,
        Path(AUDIO_DIR) / project_id,
        Path(SUBTITLES_DIR) / project_id,
        Path(EXPORTS_DIR) / project_id,
    }
    try:
        custom_download_root = get_app_settings().get("download_directory")
    except Exception:
        custom_download_root = None
    if custom_download_root:
        candidates.add(Path(custom_download_root).expanduser() / project_id)
    for column in ("video_path", "audio_path", "thumbnail_path"):
        value = row[column] if column in row.keys() else None
        if value:
            candidates.add(Path(value))
    for root in (Path(SUBTITLES_DIR), Path(EXPORTS_DIR)):
        if root.is_dir():
            candidates.update(
                item for item in root.iterdir()
                if item.name.startswith(f"{project_id}_")
            )
    for path in sorted(candidates, key=lambda item: len(item.parts), reverse=True):
        _remove_managed_path(path)


def _purge_project_record(project_id: str) -> None:
    db = get_db()
    try:
        # Explicit task deletion is required because the legacy tasks table has
        # no foreign key. Other rows are listed for old databases whose foreign
        # key definitions may predate the current schema.
        db.execute(
            "DELETE FROM transcription_segments WHERE project_id=?", (project_id,)
        )
        db.execute("DELETE FROM transcription_runs WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM segment_revisions WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM segments WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM tasks WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM projects WHERE id=?", (project_id,))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _resolve_model(model: str, language: str) -> str:
    # Preserve the established API contract: an explicitly unknown model is a
    # client error, while only automatic/unavailable configured choices fall
    # back to the safe release model.
    if model != "auto" and model not in SUPPORTED_TRANSCRIPTION_MODELS:
        return model
    if model != "auto":
        return model
    try:
        settings = get_app_settings()
    except Exception:
        settings = {}
    resolution = resolve_transcription_model(
        model,
        language,
        default_model=settings.get("default_model") or "small",
        custom_model_path=settings.get("custom_model_path"),
        coreml_model_path=settings.get("coreml_model_path"),
        coreml_cli_path=settings.get("coreml_cli_path"),
    )
    return resolution.model_id


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
    try:
        settings = get_app_settings()
    except Exception:
        settings = {}
    audio = None
    if project_id:
        db = get_db()
        row = db.execute("SELECT audio_path FROM projects WHERE id=?", (project_id,)).fetchone()
        db.close()
        audio = _audio_preflight(row["audio_path"] if row else None)
    recommended = _resolve_model("auto", language)
    model_definitions = [
        ("small", "Whisper Small", ["*"]),
        ("medium", "Whisper Medium", ["*"]),
        ("large-v3", "Whisper Large V3", ["*"]),
        (PARAKEET_MODEL_ID, "Parakeet V3 Core ML", sorted(PARAKEET_SUPPORTED_LANGUAGES)),
        (PARAKEET_ONNX_MODEL_ID, "Parakeet V3 ONNX", sorted(PARAKEET_SUPPORTED_LANGUAGES)),
    ]
    model_items = []
    for model_id, name, languages in model_definitions:
        status = get_transcription_model_status(
            model_id,
            custom_model_path=settings.get("custom_model_path"),
            coreml_model_path=settings.get("coreml_model_path"),
            coreml_cli_path=settings.get("coreml_cli_path"),
        )
        model_items.append({
            "id": model_id,
            "name": name,
            "languages": languages,
            **status,
            "status": status.get("state"),
            # Old clients read ``runtime_error`` while v0.2 uses ``error``.
            "runtime_error": status.get("error") or None,
            "runtimes": _runtime_options(model_id, model_ready=bool(status.get("ready") or model_id != PARAKEET_MODEL_ID)),
            "selected_runtime": (settings.get("transcription_runtime_by_model") or {}).get(model_id),
        })
    if settings.get("custom_model_path"):
        status = get_transcription_model_status(
            "custom", custom_model_path=settings.get("custom_model_path")
        )
        model_items.append({
            "id": "custom", "name": "自定义 Whisper", "languages": ["*"],
            **status, "status": status.get("state"),
            "runtime_error": status.get("error") or None,
            "runtimes": _runtime_options("custom", model_ready=bool(status.get("ready"))),
            "selected_runtime": (settings.get("transcription_runtime_by_model") or {}).get("custom"),
        })
    for imported in get_imported():
        checked = validate_imported(imported["id"])
        model_items.append({"id": imported["id"], "name": imported["display_name"], "languages": ["*"],
                            "ready": checked["ready"], "source": "imported_reference", "state": checked["status"],
                            "status": checked["status"], "download_required": False, "runtime_error": checked.get("last_error") or None,
                            "runtimes": _runtime_options(imported["id"], imported, checked["ready"]),
                            "selected_runtime": (settings.get("transcription_runtime_by_model") or {}).get(imported["id"]),
                            "format": imported["format"], "version": imported["version"]})
    return {
        "recommended_model": recommended,
        "audio": audio,
        "models": model_items,
    }


@router.post("/transcription/models/scan")
def scan_local_models(request: ModelScanRequest):
    try:
        models=scan_models(request.root_path)
        return {"models":models,"candidates":models}
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc


@router.post("/transcription/models/import")
def import_local_model(request: ModelImportRequest):
    try: return {"model": register_model(request.path, request.cli_path, request.display_name)}
    except ValueError as exc: raise HTTPException(400, str(exc)) from exc


@router.get("/transcription/models/imported")
def imported_models(): return {"models": get_imported()}


@router.post("/transcription/models/imported/{model_id:path}/validate")
def validate_imported_model(model_id: str):
    try: return validate_imported(model_id)
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc


@router.delete("/transcription/models/imported/{model_id:path}")
def delete_imported_model(model_id: str):
    remove_imported(model_id); return {"message": "已从字幕工厂移除登记，源模型未被删除"}


@router.get("/transcription/models/{model_id}/validate")
def validate_transcription_model(model_id: str):
    try:
        settings = get_app_settings()
    except Exception:
        settings = {}
    status = get_transcription_model_status(
        model_id,
        custom_model_path=settings.get("custom_model_path"),
        coreml_model_path=settings.get("coreml_model_path"),
        coreml_cli_path=settings.get("coreml_cli_path"),
    )
    names = {
        "small": "Whisper Small",
        "medium": "Whisper Medium",
        "large-v3": "Whisper Large V3",
        "custom": "自定义 Whisper",
        PARAKEET_MODEL_ID: "Parakeet V3 Core ML",
        PARAKEET_ONNX_MODEL_ID: "Parakeet V3 ONNX",
    }
    languages = (
        sorted(PARAKEET_SUPPORTED_LANGUAGES)
        if model_id in PARAKEET_MODEL_IDS else ["*"]
    )
    return {
        "id": model_id,
        "name": names.get(model_id, model_id),
        "languages": languages,
        **status,
        "status": status.get("state"),
        "runtime_error": status.get("error") or None,
    }


def _do_prepare_transcription_model(
    task_id: str, model_id: str, repair: bool, settings: dict,
):
    prepare_parakeet_model(
        task_id,
        model_id,
        repair=repair,
        coreml_model_dir=settings.get("coreml_model_path"),
        coreml_cli_path=settings.get("coreml_cli_path"),
    )


@router.post("/transcription/models/{model_id}/prepare")
def prepare_transcription_model(model_id: str, request: ModelPrepareRequest):
    if model_id not in PARAKEET_MODEL_IDS:
        raise HTTPException(
            400,
            detail={
                "code": "MODEL_PREPARE_UNSUPPORTED",
                "message": "Whisper 模型会在首次转写时自动下载",
            },
        )
    try:
        settings = get_app_settings()
    except Exception:
        settings = {}
    task_id = task_manager.create_task(None, "prepare_model")
    task_manager.run_background(
        task_id, _do_prepare_transcription_model, model_id, request.repair, settings,
    )
    return {
        "task_id": task_id,
        "model_id": model_id,
        "message": "正在修复模型" if request.repair else "正在准备模型",
    }


# ============================
# 项目 CRUD
# ============================

@router.get("/projects")
def list_projects(deleted: bool = False):
    """获取项目列表；默认隐藏回收站项目。"""
    init_db()
    db = get_db()
    rows = db.execute(
        "SELECT p.*, (SELECT COUNT(*) FROM segments s WHERE s.project_id = p.id) as segments_count "
        f"FROM projects p WHERE p.deleted_at IS {'NOT ' if deleted else ''}NULL "
        "ORDER BY COALESCE(p.deleted_at, p.updated_at) DESC"
    ).fetchall()
    db.close()
    return {
        "projects": [
            {**project_to_dict(r), "segments_count": r["segments_count"]}
            for r in rows
        ]
    }


@router.delete("/projects/trash")
def empty_project_trash(confirm: bool = Query(False)):
    """清空回收站；必须显式确认，且不会删除回收站外的项目。"""
    if not confirm:
        raise HTTPException(
            400,
            detail={"code": "CONFIRMATION_REQUIRED", "message": "清空回收站需要显式确认"},
        )
    db = get_db()
    rows = db.execute(
        "SELECT * FROM projects WHERE deleted_at IS NOT NULL ORDER BY deleted_at"
    ).fetchall()
    db.close()

    active = {
        row["id"]: _active_task_conflict(row["id"])
        for row in rows
    }
    active = {project_id: ids for project_id, ids in active.items() if ids}
    if active:
        raise HTTPException(
            409,
            detail={
                "code": "ACTIVE_TASKS",
                "message": "回收站中仍有项目任务正在结束，请稍后重试",
                "projects": active,
            },
        )

    for row in rows:
        try:
            _purge_project_files(row)
        except OSError as exc:
            raise HTTPException(
                500,
                detail={
                    "code": "FILE_CLEANUP_FAILED",
                    "message": "项目文件清理失败，未删除数据库记录",
                    "project_id": row["id"],
                    "reason": str(exc),
                },
            ) from exc

    db = get_db()
    try:
        project_ids = [row["id"] for row in rows]
        for project_id in project_ids:
            db.execute(
                "DELETE FROM transcription_segments WHERE project_id=?", (project_id,)
            )
            db.execute("DELETE FROM transcription_runs WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM segment_revisions WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM segments WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM tasks WHERE project_id=?", (project_id,))
        db.execute("DELETE FROM projects WHERE deleted_at IS NOT NULL")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {
        "deleted_count": len(rows),
        "project_ids": [row["id"] for row in rows],
        "message": "回收站已清空",
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


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def update_project(project_id: str, update: ProjectUpdate):
    """Update project metadata without disturbing media or subtitles."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    try:
        fields=[]; values=[]
        if update.title is not None: fields.append("title=?"); values.append(update.title)
        if update.target_language is not None: fields.append("target_language=?"); values.append(update.target_language.strip())
        if not fields: raise HTTPException(400, "没有可更新的项目字段")
        fields.append("updated_at=?"); values.extend([now, project_id])
        cursor = db.execute(f"UPDATE projects SET {', '.join(fields)} WHERE id=? AND deleted_at IS NULL", values)
        if cursor.rowcount == 0:
            db.rollback()
            raise HTTPException(404, "项目不存在")
        row = db.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM segments s WHERE s.project_id=p.id) segments_count "
            "FROM projects p WHERE p.id=?",
            (project_id,),
        ).fetchone()
        db.commit()
    finally:
        db.close()
    return {**project_to_dict(row), "segments_count": row["segments_count"]}


@router.post("/projects/{project_id}/trash")
def trash_project(project_id: str, terminate: bool = Query(False)):
    """将项目移入回收站，保留所有媒体、字幕、任务和导出文件。"""
    row = _project_row(project_id)
    if not row:
        raise HTTPException(404, "项目不存在")
    if row["deleted_at"]:
        return {
            "project": project_to_dict(row),
            "terminated_task_ids": [],
            "message": "项目已在回收站",
        }

    active_task_ids = _active_task_conflict(project_id)
    if active_task_ids and not terminate:
        _raise_active_task_conflict(active_task_ids)
    terminated_task_ids = (
        task_manager.cancel_project_tasks(project_id) if active_task_ids else []
    )

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    try:
        db.execute(
            "UPDATE projects SET deleted_at=?, updated_at=? WHERE id=?",
            (now, now, project_id),
        )
        db.commit()
        updated = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    finally:
        db.close()
    return {
        "project": project_to_dict(updated),
        "terminated_task_ids": terminated_task_ids,
        "message": "项目已移入回收站",
    }


@router.post("/projects/{project_id}/restore")
def restore_project(project_id: str):
    """从回收站恢复项目。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    try:
        row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "项目不存在")
        db.execute(
            "UPDATE projects SET deleted_at=NULL, updated_at=? WHERE id=?",
            (now, project_id),
        )
        db.commit()
        restored = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    finally:
        db.close()
    return {"project": project_to_dict(restored), "message": "项目已恢复"}


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: str,
    permanent: bool = Query(False),
    terminate: bool = Query(False),
):
    """默认移入回收站；显式 permanent=true 才彻底删除。"""
    if not permanent:
        return trash_project(project_id, terminate=terminate)

    row = _project_row(project_id)
    if not row:
        raise HTTPException(404, "项目不存在")
    active_task_ids = _active_task_conflict(project_id)
    if active_task_ids:
        # Permanent cleanup must not race a worker that may still be returning
        # from FFmpeg/yt-dlp/ML inference. Move to trash first to terminate it,
        # then retry permanent deletion once no active task remains.
        _raise_active_task_conflict(active_task_ids)
    try:
        _purge_project_files(row)
    except OSError as exc:
        raise HTTPException(
            500,
            detail={
                "code": "FILE_CLEANUP_FAILED",
                "message": "项目文件清理失败，数据库记录已保留",
                "reason": str(exc),
            },
        ) from exc
    _purge_project_record(project_id)
    return {"project_id": project_id, "message": "项目已永久删除"}


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

    normalized_url = normalize_youtube_url(url)
    # 保存规范化 URL，避免 t=110s 等播放定位参数被当作下载范围。
    db.execute("UPDATE projects SET source_url = ?, updated_at = ? WHERE id = ?",
               (normalized_url, time.strftime("%Y-%m-%d %H:%M:%S"), project_id))
    db.commit()
    db.close()

    task_id = task_manager.create_task(project_id, "download")
    task_manager.run_background(task_id, _do_download, project_id, normalized_url)
    return {"task_id": task_id, "message": "下载任务已创建"}


def _do_download(task_id: str, project_id: str, url: str):
    """后台执行下载"""
    try:
        app_settings = get_app_settings()
    except Exception:
        app_settings = {}
    video_path = download_video(
        task_id,
        url,
        project_id,
        ffmpeg_path=app_settings.get("ffmpeg_path"),
        download_dir=app_settings.get("download_directory"),
        quality=app_settings.get("download_quality") or "best",
        container=app_settings.get("download_container") or "mp4",
    )
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
    autostart: bool = Form(False), model: str = Form("auto"), language: str = Form("auto"), runtime: Optional[str] = Form(None),
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
        settings=get_app_settings()
        imported=get_imported(resolved_model) if resolved_model.startswith("local:") else None
        selected_runtime=_select_runtime(resolved_model, runtime, settings, imported)
        task_id = task_manager.create_task(project_id, "workflow")
        task_manager.run_background(
            task_id, _do_workflow, project_id, resolved_model, language, None, selected_runtime,
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
def start_transcribe(project_id: str, language: str = Form("auto"), model: str = Form("small"), runtime: Optional[str] = Form(None)):
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
        (language, time.strftime("%Y-%m-%d %H:%M:%S"), project_id)
    )
    db.commit()
    db.close()

    from ..services.app_settings import save_app_settings
    settings=get_app_settings(); imported=get_imported(model) if model.startswith("local:") else None
    if direct_call_sentinel and not runtime:
        runtime=_runtime_ids(model,imported)[0]
    runtime=_select_runtime(model,runtime,settings,imported)
    mapping=dict(settings.get("transcription_runtime_by_model") or {}); mapping[model]=runtime
    save_app_settings({"transcription_runtime_by_model":mapping})
    task_id = task_manager.create_task(project_id, "transcribe")
    task_manager.run_background(task_id, _do_transcribe, project_id, row["audio_path"], language, model, runtime)
    return {"task_id": task_id, "message": "转写任务已创建"}


def _do_transcribe(task_id: str, project_id: str, audio_path: str, language: str, model: str, runtime: str | None = None):
    task_manager.update_task(task_id, message="等待本地转写引擎")
    while not TRANSCRIPTION_LOCK.acquire(timeout=0.25):
        task_manager.checkpoint(task_id)
    try:
        task_manager.checkpoint(task_id)
        try:
            transcribe_audio(task_id, audio_path, project_id, language, model, runtime)
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
            transcribe_audio(task_id, audio_path, project_id, language, model, runtime)
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
    settings=get_app_settings(); imported=get_imported(model) if model.startswith("local:") else None
    runtime=_select_runtime(model,request.runtime,settings,imported)
    from ..services.app_settings import save_app_settings
    mapping=dict(settings.get("transcription_runtime_by_model") or {});mapping[model]=runtime
    save_app_settings({"transcription_runtime_by_model":mapping})
    task_id = task_manager.create_task(project_id, "workflow")
    task_manager.run_background(
        task_id, _do_workflow, project_id, model, request.language,
        source_url if not row["video_path"] else None, runtime,
    )
    return {"task_id": task_id, "message": "自动字幕工作流已创建", "model": model}


def _do_workflow(
    task_id: str, project_id: str, model: str, language: str, source_url: str | None, runtime: str,
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
    _do_transcribe(task_id, project_id, audio_path, language, model, runtime)
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
    settings=get_app_settings(); imported=get_imported(model) if model.startswith("local:") else None
    runtime=_select_runtime(model,request.runtime,settings,imported)
    task_id = task_manager.create_task(project_id, "transcribe")
    task_manager.run_background(task_id, _do_transcribe, project_id, row["audio_path"], request.language, model, runtime)
    return {"task_id": task_id, "message": "转写重试任务已创建", "model": model}


# ============================
# AI 整理
# ============================

@router.post("/projects/{project_id}/clean")
def start_clean(project_id: str, target_length: int = Form(42), provider_id: Optional[str] = Form(None), model: Optional[str] = Form(None)):
    """开始 AI 整理字幕（后台任务）"""
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()

    if not row:
        raise HTTPException(404, "项目不存在")

    if target_length < 16 or target_length > 100:
        raise HTTPException(400, "目标单句长度必须在 16 到 100 个字符之间")
    db=get_db(); existing=db.execute("SELECT id FROM tasks WHERE project_id=? AND type='clean' AND status IN ('pending','running','paused') ORDER BY created_at DESC LIMIT 1",(project_id,)).fetchone(); db.close()
    if existing: return {"task_id":existing["id"],"message":"AI 整理已在运行","existing":True}
    task_id = task_manager.create_task(project_id, "clean")
    task_manager.run_background(task_id, _do_clean, project_id, target_length, provider_id, model)
    return {"task_id": task_id, "message": "AI 整理任务已创建"}


def _do_clean(task_id: str, project_id: str, target_length: int, provider_id: str | None = None, model: str | None = None):
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


# ============================
# AI 翻译
# ============================

@router.post("/projects/{project_id}/translate")
def start_translate(project_id: str, target_language: str = Form("zh"), provider_id: Optional[str] = Form(None), model: Optional[str] = Form(None)):
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
    task_manager.run_background(task_id, _do_translate, project_id, target_language, provider_id, model)
    return {"task_id": task_id, "message": "AI 翻译任务已创建"}


def _do_translate(task_id: str, project_id: str, target_language: str, provider_id: str | None = None, model: str | None = None):
    translate_subtitles(task_id, project_id, target_language, provider_id, model)


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
