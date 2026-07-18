"""Multi-file production batches."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..models.database import get_db
from ..utils.config import PROJECTS_DIR
from ..services.playlist_batches import (
    PlaylistBatchError, cancel_pending_batch, create_or_sync_playlist,
    enable_batch_stage, get_batch_detail, list_playlist_batches, pause_batch,
    preview_playlist, resume_batch, retry_failed,
    sync_playlist_batch,
)


router = APIRouter(prefix="/api")
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}


class BatchCreate(BaseModel):
    name: str = "批量导入"
    paths: list[str] = Field(min_length=1)
    configuration: dict = Field(default_factory=dict)


class PlaylistPreviewRequest(BaseModel):
    url: str = Field(min_length=1)


class PlaylistBatchCreate(BaseModel):
    url: str = Field(min_length=1)
    configuration: dict = Field(default_factory=dict)


class PlaylistStageRun(BaseModel):
    configuration: dict = Field(default_factory=dict)


def _playlist_call(function, *args):
    try:
        return function(*args)
    except PlaylistBatchError as exc:
        raise HTTPException(
            404 if exc.error_code == "BATCH_NOT_FOUND" else 422,
            detail={"code": exc.error_code, "message": str(exc), "recoverable": exc.recoverable},
        ) from exc


@router.post("/batches/playlist/preview")
def preview_playlist_batch(request: PlaylistPreviewRequest):
    return _playlist_call(preview_playlist, request.url)


@router.post("/batches/playlist", status_code=201)
def create_playlist_batch(request: PlaylistBatchCreate):
    preview = _playlist_call(preview_playlist, request.url)
    return _playlist_call(create_or_sync_playlist, preview, request.configuration)


@router.get("/batches")
def list_batches(kind: str = ""):
    if kind == "youtube_playlist":
        return list_playlist_batches()
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM batches ORDER BY updated_at DESC").fetchall()
        return {"batches": [dict(row) for row in rows]}
    finally:
        db.close()


@router.post("/batches", status_code=201)
def create_batch(request: BatchCreate):
    sources = [Path(value).expanduser().resolve() for value in request.paths]
    invalid = [str(path) for path in sources if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS]
    if invalid:
        raise HTTPException(422, detail={"code": "BATCH_SOURCE_INVALID", "message": "部分视频路径无效", "details": {"paths": invalid}})
    batch_id, now = str(uuid.uuid4()), time.strftime("%Y-%m-%d %H:%M:%S")
    imported = []
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO batches VALUES (?,?,?,'pending',?,?)", (batch_id, request.name, json.dumps(request.configuration, ensure_ascii=False), now, now))
        for source in sources:
            project_id, item_id = str(uuid.uuid4()), str(uuid.uuid4())
            destination_dir = Path(PROJECTS_DIR) / project_id
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / f"video{source.suffix.lower()}"
            shutil.copy2(source, destination)
            db.execute(
                """INSERT INTO projects(id,title,source_type,video_path,language,target_language,created_at,updated_at)
                   VALUES (?,?, 'local',?,?,?,?,?)""",
                (project_id, source.stem, str(destination), request.configuration.get("language", "auto"), request.configuration.get("target_language", "zh"), now, now),
            )
            db.execute("INSERT INTO batch_items VALUES (?,?,?,?, 'ready',NULL,?,?)", (item_id, batch_id, project_id, str(source), now, now))
            imported.append({"item_id": item_id, "project_id": project_id, "title": source.stem})
        db.execute("UPDATE batches SET status='ready',updated_at=? WHERE id=?", (now, batch_id)); db.commit()
    except Exception:
        db.rollback(); raise
    finally: db.close()
    return {"batch_id": batch_id, "items": imported, "configuration": request.configuration}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str):
    db = get_db()
    try:
        batch = db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch: raise HTTPException(404, "批次不存在")
        if "kind" in batch.keys() and batch["kind"] == "youtube_playlist":
            db.close()
            return _playlist_call(get_batch_detail, batch_id)
        items = db.execute("SELECT * FROM batch_items WHERE batch_id=? ORDER BY created_at", (batch_id,)).fetchall()
        return {"batch": {**dict(batch), "configuration": json.loads(batch["configuration_json"])}, "items": [dict(row) for row in items]}
    finally:
        try: db.close()
        except Exception: pass


@router.post("/batches/{batch_id}/cancel-pending")
def cancel_pending_batch_items(batch_id: str):
    db = get_db()
    kind_row = db.execute("SELECT kind FROM batches WHERE id=?", (batch_id,)).fetchone()
    db.close()
    if kind_row and kind_row["kind"] == "youtube_playlist":
        return cancel_pending_batch(batch_id)
    db = get_db()
    try:
        cursor = db.execute("UPDATE batch_items SET status='cancelled',updated_at=datetime('now','localtime') WHERE batch_id=? AND status IN ('pending','ready')", (batch_id,)); db.execute("UPDATE batches SET status='cancelled',updated_at=datetime('now','localtime') WHERE id=?", (batch_id,)); db.commit()
        return {"cancelled_count": cursor.rowcount}
    finally: db.close()


@router.post("/batches/{batch_id}/pause")
def pause_playlist_batch(batch_id: str):
    return _playlist_call(pause_batch, batch_id)


@router.post("/batches/{batch_id}/resume")
def resume_playlist_batch(batch_id: str):
    return _playlist_call(resume_batch, batch_id)


@router.post("/batches/{batch_id}/retry-failed")
def retry_failed_playlist_items(batch_id: str):
    return _playlist_call(retry_failed, batch_id, None)


@router.post("/batches/{batch_id}/sync")
def sync_existing_playlist_batch(batch_id: str):
    return _playlist_call(sync_playlist_batch, batch_id)


@router.post("/batches/{batch_id}/items/{item_id}/retry")
def retry_playlist_item(batch_id: str, item_id: str):
    return _playlist_call(retry_failed, batch_id, item_id)


@router.post("/batches/{batch_id}/stages/{stage}/run")
def run_playlist_stage(batch_id: str, stage: str, request: PlaylistStageRun):
    return _playlist_call(enable_batch_stage, batch_id, stage, request.configuration)
