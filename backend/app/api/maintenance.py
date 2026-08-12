"""Backup and privacy-safe diagnostics endpoints."""

from __future__ import annotations

import json
import re
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..models.database import get_db
from ..services.backups import (
    backup_directory,
    create_backup,
    last_restore,
    list_backups,
    pending_restore,
    restore_backup,
)
from ..services.app_settings import get_app_settings
from ..services.search_index import rebuild_search_index, search_index_status
from ..utils.config import EXPORTS_DIR, LOGS_DIR
from ..utils.task_manager import task_manager


router = APIRouter(prefix="/api")


class RestoreRequest(BaseModel):
    name: str
    confirm: bool = False


def _redact(value: str) -> str:
    value = re.sub(r"(?i)(authorization:\s*bearer|api[_ -]?key[=: ]+)\s*\S+", r"\1 [REDACTED]", value)
    value = re.sub(r"/(?:Users|home)/[^/\s]+", "~/", value)
    return value


@router.get("/maintenance/backups")
def backups():
    pending = pending_restore()
    completed = last_restore()
    return {
        "directory": str(backup_directory()),
        "backups": list_backups(),
        "pending_restore": _public_restore_record(pending),
        "last_restore": _public_restore_record(completed),
    }


def _public_restore_record(record: dict | None) -> dict | None:
    if not record:
        return None
    return {
        key: record.get(key)
        for key in (
            "status", "source_name", "source_hash", "staged_at", "applied_at",
        )
        if record.get(key) is not None
    }


@router.post("/maintenance/backups", status_code=201)
def backup_now():
    return create_backup("manual")


@router.post("/maintenance/backups/restore")
def restore(request: RestoreRequest):
    if not request.confirm:
        raise HTTPException(400, detail={"code": "CONFIRMATION_REQUIRED", "message": "恢复备份需要显式确认"})
    acquired, live_tasks = task_manager.begin_exclusive_maintenance("database_restore")
    if not acquired:
        raise _restore_busy(live_tasks)
    try:
        db = get_db()
        try:
            active = db.execute(
                """SELECT id,type FROM tasks
                     WHERE status IN ('pending','running','paused')
                     ORDER BY updated_at DESC LIMIT 10"""
            ).fetchall()
        finally:
            db.close()
        if active:
            raise _restore_busy([
                {"id": row["id"], "type": row["type"]} for row in active
            ])
        return restore_backup(request.name)
    except FileNotFoundError as error:
        task_manager.end_exclusive_maintenance("database_restore")
        raise HTTPException(404, str(error)) from error
    except HTTPException:
        task_manager.end_exclusive_maintenance("database_restore")
        raise
    except (OSError, RuntimeError, ValueError) as error:
        task_manager.end_exclusive_maintenance("database_restore")
        raise HTTPException(422, str(error)) from error
    except Exception:
        task_manager.end_exclusive_maintenance("database_restore")
        raise


def _restore_busy(active_tasks: list[dict]) -> HTTPException:
    return HTTPException(409, detail={
        "code": "BACKUP_RESTORE_BUSY",
        "message": "仍有后台任务在运行、暂停或退出，现在不能恢复数据库",
        "suggestion": "请先等待任务完成或安全终止，再重试恢复",
        "details": {"active_tasks": active_tasks[:10]},
        "recoverable": True,
    })


def _create_diagnostics_bundle() -> Path:
    """Export configuration shape and redacted log tail, never subtitle/media text."""
    target = Path(EXPORTS_DIR) / "diagnostics" / "subtitle-factory-diagnostics.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    settings = get_app_settings().copy()
    for key in list(settings):
        if "path" in key or "directory" in key or "key" in key:
            settings[key] = "[REDACTED]" if settings[key] else ""
    log_path = Path(LOGS_DIR) / "app.log"
    log_tail = ""
    if log_path.is_file():
        log_tail = _redact(log_path.read_text(encoding="utf-8", errors="replace")[-200_000:])
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json.dumps({
            "product": "字幕工厂", "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "contains_media": False, "contains_subtitle_text": False,
        }, ensure_ascii=False, indent=2))
        bundle.writestr("settings-shape.json", json.dumps(settings, ensure_ascii=False, indent=2))
        bundle.writestr("app.log", log_tail)
    return target


@router.post("/maintenance/diagnostics/prepare")
def prepare_diagnostics():
    target = _create_diagnostics_bundle()
    return {"path": str(target), "filename": target.name, "size": target.stat().st_size}


@router.post("/maintenance/diagnostics")
def diagnostics():
    target = _create_diagnostics_bundle()
    return FileResponse(target, filename=target.name, media_type="application/zip")


@router.get("/maintenance/search-index")
def inspect_search_index():
    return search_index_status()


@router.post("/maintenance/search-index/rebuild")
def rebuild_full_text_search_index():
    return rebuild_search_index()
