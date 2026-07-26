"""Watch-folder configuration and stable-file discovery."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..models.database import get_db


router = APIRouter(prefix="/api")
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}


class WatchFolderInput(BaseModel):
    path: str
    enabled: bool = True
    workflow: dict = Field(default_factory=dict)


class WatchImportMark(BaseModel):
    path: str
    project_id: str


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        digest.update(source.read(1024 * 1024))
        if path.stat().st_size > 1024 * 1024:
            source.seek(max(0, path.stat().st_size - 1024 * 1024)); digest.update(source.read())
    return digest.hexdigest()


@router.get("/watch-folders")
def list_watch_folders():
    db = get_db()
    try: return {"watch_folders": [{**dict(row), "enabled": bool(row["enabled"]), "workflow": json.loads(row["workflow_json"])} for row in db.execute("SELECT * FROM watch_folders ORDER BY path")]}
    finally: db.close()


@router.post("/watch-folders", status_code=201)
def add_watch_folder(request: WatchFolderInput):
    path = Path(request.path).expanduser().resolve()
    if not path.is_dir(): raise HTTPException(422, "监听目录不存在")
    identifier, now = str(uuid.uuid4()), time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    try:
        db.execute("INSERT INTO watch_folders(id,path,enabled,workflow_json,created_at,updated_at) VALUES (?,?,?,?,?,?)", (identifier, str(path), int(request.enabled), json.dumps(request.workflow, ensure_ascii=False), now, now)); db.commit()
        return {"id": identifier, "path": str(path), "enabled": request.enabled, "workflow": request.workflow}
    except Exception as error:
        db.rollback()
        if "UNIQUE" in str(error): raise HTTPException(409, "该目录已在监听") from error
        raise
    finally: db.close()


@router.delete("/watch-folders/{folder_id}")
def remove_watch_folder(folder_id: str):
    db = get_db()
    try: db.execute("DELETE FROM watch_folders WHERE id=?", (folder_id,)); db.commit(); return {"deleted": True}
    finally: db.close()


@router.post("/watch-folders/scan")
def scan_watch_folders():
    """Two scans with unchanged size/mtime are required before a file is ready."""
    db = get_db(); ready = []; now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        folders = db.execute("SELECT * FROM watch_folders WHERE enabled=1").fetchall()
        for folder in folders:
            root = Path(folder["path"])
            if not root.is_dir(): continue
            for path in root.iterdir():
                if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS: continue
                stat = path.stat()
                previous = db.execute("SELECT * FROM watch_folder_files WHERE watch_folder_id=? AND path=?", (folder["id"], str(path))).fetchone()
                stable = bool(previous and previous["size"] == stat.st_size and previous["modified_ns"] == stat.st_mtime_ns)
                if stable and not previous["imported_project_id"]:
                    fingerprint = previous["fingerprint"] or _fingerprint(path)
                    duplicate = db.execute("SELECT 1 FROM watch_folder_files WHERE fingerprint=? AND imported_project_id IS NOT NULL", (fingerprint,)).fetchone()
                    if not duplicate: ready.append({"watch_folder_id": folder["id"], "path": str(path), "fingerprint": fingerprint, "workflow": json.loads(folder["workflow_json"] or "{}")})
                    db.execute("UPDATE watch_folder_files SET fingerprint=? WHERE watch_folder_id=? AND path=?", (fingerprint, folder["id"], str(path)))
                db.execute("""INSERT INTO watch_folder_files(watch_folder_id,path,size,modified_ns,stable_since)
                              VALUES (?,?,?,?,?) ON CONFLICT(watch_folder_id,path) DO UPDATE SET
                              size=excluded.size,modified_ns=excluded.modified_ns,
                              stable_since=CASE WHEN size=excluded.size AND modified_ns=excluded.modified_ns THEN stable_since ELSE excluded.stable_since END""",
                           (folder["id"], str(path), stat.st_size, stat.st_mtime_ns, now))
            db.execute("UPDATE watch_folders SET last_scan_at=?,updated_at=? WHERE id=?", (now, now, folder["id"]))
        db.commit(); return {"ready": ready, "count": len(ready)}
    finally: db.close()


@router.post("/watch-folders/{folder_id}/mark-imported")
def mark_watch_file_imported(folder_id: str, request: WatchImportMark):
    db = get_db()
    try:
        cursor = db.execute(
            "UPDATE watch_folder_files SET imported_project_id=? WHERE watch_folder_id=? AND path=?",
            (request.project_id, folder_id, str(Path(request.path).expanduser().resolve())),
        )
        db.commit()
        if not cursor.rowcount: raise HTTPException(404, "监听文件记录不存在")
        return {"marked": True}
    finally: db.close()
