"""Speaker labels, offline diarization, and explicit cloud consent."""

import os
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..models.database import get_db
from ..services.diarization import diarize_project
from ..services.speaker_models import prepare as prepare_speaker_models, status as speaker_model_status
from ..utils.task_manager import task_manager


router = APIRouter(prefix="/api")


class SpeakerInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(default="#5b8cff", pattern=r"^#[0-9a-fA-F]{6}$")


class DiarizationRequest(BaseModel):
    segmentation_model: str
    embedding_model: str
    num_speakers: int | None = Field(default=None, ge=1, le=20)


class CloudAuthorizationRequest(BaseModel):
    granted: bool
    provider_id: str | None = None
    disclosure_version: str = "1.0"


@router.get("/speaker-models")
def get_speaker_models():
    return speaker_model_status()


@router.post("/speaker-models/prepare")
def prepare_managed_speaker_models():
    current = speaker_model_status()
    if current["ready"]:
        return {"ready": True, **current}
    task_id = task_manager.create_task(None, "prepare_speaker_models", resource_class="io", max_attempts=3)
    task_manager.run_background(task_id, prepare_speaker_models)
    return {"ready": False, "task_id": task_id}


@router.get("/projects/{project_id}/speakers")
def list_speakers(project_id: str):
    db = get_db()
    try: return {"speakers": [dict(row) for row in db.execute("SELECT * FROM speakers WHERE project_id=? ORDER BY created_at", (project_id,))]}
    finally: db.close()


@router.post("/projects/{project_id}/speakers", status_code=201)
def create_speaker(project_id: str, request: SpeakerInput):
    identifier, now = str(uuid.uuid4()), time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    try:
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone(): raise HTTPException(404, "项目不存在")
        db.execute("INSERT INTO speakers VALUES (?,?,?,?,NULL,?,?)", (identifier, project_id, request.name, request.color, now, now)); db.commit()
        return {"id": identifier, **request.model_dump()}
    finally: db.close()


@router.put("/projects/{project_id}/speakers/{speaker_id}")
def update_speaker(project_id: str, speaker_id: str, request: SpeakerInput):
    db = get_db()
    try:
        cursor = db.execute("UPDATE speakers SET name=?,color=?,updated_at=datetime('now','localtime') WHERE id=? AND project_id=?", (request.name, request.color, speaker_id, project_id))
        db.execute("UPDATE segments SET speaker=? WHERE speaker_id=? AND project_id=?", (request.name, speaker_id, project_id)); db.commit()
        if not cursor.rowcount: raise HTTPException(404, "说话人不存在")
        return {"id": speaker_id, **request.model_dump()}
    finally: db.close()


@router.post("/projects/{project_id}/speakers/{source_id}/merge/{target_id}")
def merge_speakers(project_id: str, source_id: str, target_id: str):
    if source_id == target_id: raise HTTPException(422, "不能合并同一说话人")
    db = get_db()
    try:
        target = db.execute("SELECT name FROM speakers WHERE id=? AND project_id=?", (target_id, project_id)).fetchone()
        if not target: raise HTTPException(404, "目标说话人不存在")
        db.execute("UPDATE segments SET speaker_id=?,speaker=? WHERE speaker_id=? AND project_id=?", (target_id, target["name"], source_id, project_id))
        db.execute("DELETE FROM speakers WHERE id=? AND project_id=?", (source_id, project_id)); db.commit()
        return {"merged_into": target_id}
    finally: db.close()


@router.post("/projects/{project_id}/speakers/diarize")
def start_diarization(project_id: str, request: DiarizationRequest):
    for path in (request.segmentation_model, request.embedding_model):
        if not os.path.isfile(os.path.expanduser(path)): raise HTTPException(422, "说话人模型文件不存在")
    task_id = task_manager.create_task(project_id, "speaker_diarization", resource_class="ml")
    task_manager.run_background(task_id, diarize_project, project_id, os.path.expanduser(request.segmentation_model), os.path.expanduser(request.embedding_model), request.num_speakers)
    return {"task_id": task_id, "local": True}


@router.get("/cloud-authorizations")
def list_cloud_authorizations():
    db = get_db()
    try: return {"authorizations": [dict(row) for row in db.execute("SELECT * FROM cloud_authorizations ORDER BY capability")]}
    finally: db.close()


@router.put("/cloud-authorizations/{capability}")
def set_cloud_authorization(capability: str, request: CloudAuthorizationRequest):
    if capability not in {"ocr", "speaker", "quality"}: raise HTTPException(422, "未知云端能力")
    now = time.strftime("%Y-%m-%d %H:%M:%S"); db = get_db()
    try:
        db.execute("""INSERT INTO cloud_authorizations(capability,provider_id,granted,disclosure_version,granted_at,revoked_at)
                      VALUES (?,?,?,?,?,?) ON CONFLICT(capability) DO UPDATE SET provider_id=excluded.provider_id,
                      granted=excluded.granted,disclosure_version=excluded.disclosure_version,
                      granted_at=excluded.granted_at,revoked_at=excluded.revoked_at""",
                   (capability, request.provider_id, int(request.granted), request.disclosure_version, now if request.granted else None, None if request.granted else now)); db.commit()
        return {"capability": capability, **request.model_dump()}
    finally: db.close()
