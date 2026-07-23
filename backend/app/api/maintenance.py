"""Backup and privacy-safe diagnostics endpoints."""

from __future__ import annotations

import json
import re
import tempfile
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..services.backups import backup_directory, create_backup, list_backups, restore_backup
from ..services.app_settings import get_app_settings
from ..utils.config import LOGS_DIR


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
    return {"directory": str(backup_directory()), "backups": list_backups()}


@router.post("/maintenance/backups", status_code=201)
def backup_now():
    return create_backup("manual")


@router.post("/maintenance/backups/restore")
def restore(request: RestoreRequest):
    if not request.confirm:
        raise HTTPException(400, detail={"code": "CONFIRMATION_REQUIRED", "message": "恢复备份需要显式确认"})
    try:
        return restore_backup(request.name)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/maintenance/diagnostics")
def diagnostics():
    """Export configuration shape and redacted log tail, never subtitle/media text."""
    target = Path(tempfile.gettempdir()) / f"subtitle-factory-diagnostics-{int(time.time())}.zip"
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
    return FileResponse(target, filename=target.name, media_type="application/zip")
