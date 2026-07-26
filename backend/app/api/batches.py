"""Multi-file production batches."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..models.database import get_db
from ..utils.task_manager import task_manager
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


@router.delete("/batches/{batch_id}")
def delete_playlist_batch(
    batch_id: str,
    confirm: bool = Query(False),
    terminate: bool = Query(False),
):
    """Permanently remove a playlist batch, its child projects and managed files."""
    if not confirm:
        raise HTTPException(
            400,
            detail={"code": "CONFIRMATION_REQUIRED", "message": "永久删除播放列表需要显式确认"},
        )

    db = get_db()
    try:
        batch = db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise HTTPException(404, "批次不存在")
        if batch["kind"] != "youtube_playlist":
            raise HTTPException(
                422,
                detail={"code": "BATCH_NOT_PLAYLIST", "message": "只能用此操作删除播放列表批次"},
            )
        project_rows = db.execute(
            """SELECT DISTINCT p.* FROM projects p
               JOIN batch_items i ON i.project_id=p.id WHERE i.batch_id=?""",
            (batch_id,),
        ).fetchall()
        db.execute(
            "UPDATE batches SET paused=1,status='paused',updated_at=datetime('now','localtime') WHERE id=?",
            (batch_id,),
        )
        db.commit()
    finally:
        db.close()

    active = {
        row["id"]: task_manager.active_task_ids(row["id"])
        for row in project_rows
    }
    active = {project_id: ids for project_id, ids in active.items() if ids}
    if active and not terminate:
        raise HTTPException(
            409,
            detail={
                "code": "ACTIVE_TASKS",
                "message": "播放列表仍有任务运行；确认终止后再永久删除",
                "projects": active,
            },
        )
    if active:
        for project_id in active:
            task_manager.cancel_project_tasks(project_id)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            remaining = {
                row["id"]: task_manager.active_task_ids(row["id"])
                for row in project_rows
            }
            remaining = {project_id: ids for project_id, ids in remaining.items() if ids}
            if not remaining:
                break
            time.sleep(.1)
        else:
            raise HTTPException(
                409,
                detail={
                    "code": "BATCH_TASKS_STOPPING",
                    "message": "关联任务正在安全退出，请稍后再次删除",
                    "projects": remaining,
                },
            )

    # Keep the file safety policy in one place: this helper only removes paths
    # underneath application-managed roots (including an optional custom root).
    from .projects import _purge_project_files

    for row in project_rows:
        try:
            _purge_project_files(row)
        except OSError as exc:
            raise HTTPException(
                500,
                detail={
                    "code": "FILE_CLEANUP_FAILED",
                    "message": "播放列表文件清理失败，数据库记录已保留，可安全重试",
                    "project_id": row["id"],
                    "reason": str(exc),
                },
            ) from exc

    project_ids = [row["id"] for row in project_rows]
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM batches WHERE id=?", (batch_id,)).fetchone():
            db.rollback()
            raise HTTPException(404, "批次不存在")
        db.execute("DELETE FROM batches WHERE id=?", (batch_id,))
        for project_id in project_ids:
            db.execute("DELETE FROM tasks WHERE project_id=?", (project_id,))
            db.execute("DELETE FROM projects WHERE id=?", (project_id,))
        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {
        "batch_id": batch_id,
        "deleted_projects": len(project_ids),
        "message": "播放列表、关联项目和本地缓存已永久删除",
    }


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
