"""Preview-first hard-subtitle OCR."""

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..models.database import get_db
from ..services.editor import EditorServiceError, import_segment_snapshot
from ..services.ocr import run_ocr
from ..utils.task_manager import task_manager


router = APIRouter(prefix="/api")


class Region(BaseModel):
    x: float = Field(ge=0, le=1); y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1); height: float = Field(gt=0, le=1)


class OCRRequest(BaseModel):
    region: Region
    start: float = Field(default=0, ge=0)
    end: float = Field(gt=0)
    interval: float = Field(default=.5, ge=.2, le=3)


class OCRCommit(BaseModel):
    expected_revision: int = Field(ge=0)
    cues: list[dict]


@router.post("/projects/{project_id}/ocr")
def start_ocr(project_id: str, request: OCRRequest):
    if request.end <= request.start: raise HTTPException(422, "OCR 结束时间必须晚于开始时间")
    db = get_db(); row = db.execute("SELECT video_path FROM projects WHERE id=?", (project_id,)).fetchone(); db.close()
    if not row or not row["video_path"] or not os.path.isfile(row["video_path"]): raise HTTPException(404, "项目视频不存在")
    task_id = task_manager.create_task(project_id, "ocr", resource_class="ffmpeg")
    task_manager.run_background(task_id, run_ocr, row["video_path"], request.region.model_dump(), request.start, request.end, request.interval)
    return {"task_id": task_id, "local": True, "requires_preview_commit": True}


@router.post("/projects/{project_id}/ocr/commit")
def commit_ocr(project_id: str, request: OCRCommit):
    cues = [{"start": float(item["start"]), "end": float(item["end"]), "text": str(item["text"])} for item in request.cues]
    try: return import_segment_snapshot(project_id, request.expected_revision, cues)
    except (KeyError, ValueError) as error: raise HTTPException(422, "OCR 预览数据无效") from error
    except EditorServiceError as error: raise HTTPException(error.status_code, detail=error.as_detail()) from error
