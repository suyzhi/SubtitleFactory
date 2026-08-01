"""Short-clip recommendation, layout and render routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..models.database import get_db
from ..services.clips import (
    ASPECT_DIMENSIONS,
    ClipError,
    create_clip_set,
    create_render_batch,
    delete_render,
    get_clip_set,
    get_render,
    list_clip_sets,
    update_candidate,
    update_layout,
)
from ..services.content_packs import ContentPackError


router = APIRouter(prefix="/api")


class ClipSetCreate(BaseModel):
    name: str = Field(default="短片候选", min_length=1, max_length=160)
    desired_count: int = Field(default=5)
    min_duration: float = Field(default=30, ge=15, le=179)
    max_duration: float = Field(default=90, ge=16, le=180)


class ClipCandidateUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    selected: bool = False
    expected_revision: int = Field(ge=0)
    confirm_current_source: bool = False


class ClipLayoutUpdate(BaseModel):
    enabled: bool
    composition: str = Field(pattern="^(blur|crop)$")
    focal_x: float = Field(default=.5, ge=0, le=1)
    focal_y: float = Field(default=.5, ge=0, le=1)
    subtitle_mode: str = Field(default="original", pattern="^(off|original|translated|bilingual)$")
    style: dict = Field(default_factory=dict)
    expected_revision: int = Field(ge=0)


class ClipRenderItem(BaseModel):
    candidate_id: str
    aspect_ratio: str


class ClipRenderCreate(BaseModel):
    items: list[ClipRenderItem] = Field(min_length=1, max_length=30)
    confirm_stale: bool = False


def _require_content_authorization() -> None:
    db = get_db()
    try:
        row = db.execute(
            "SELECT granted FROM cloud_authorizations WHERE capability='content'"
        ).fetchone()
    finally:
        db.close()
    if not row or not row["granted"]:
        raise HTTPException(
            403,
            detail={
                "code": "CLOUD_AUTHORIZATION_REQUIRED",
                "message": "请先授权内容生成云端处理",
                "suggestion": "短片推荐只上传字幕、时间码、说话人标签和项目标题",
                "recoverable": True,
                "available_actions": ["authorize_content"],
            },
        )


def _handle(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ClipError, ContentPackError) as exc:
        status = 409 if exc.error_code in {
            "CLIP_REVISION_CONFLICT", "CLIP_SOURCE_STALE",
        } else 422
        raise HTTPException(
            status,
            detail={
                "code": exc.error_code,
                "message": str(exc),
                "recoverable": exc.recoverable,
                "available_actions": exc.available_actions,
                "details": getattr(exc, "details", {}),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            422,
            detail={
                "code": "CONTENT_PROVIDER_NOT_CONFIGURED",
                "message": str(exc),
                "suggestion": "请在 AI 服务设置中单独配置内容生成服务商和模型",
                "recoverable": True,
                "available_actions": ["open_settings"],
            },
        ) from exc


@router.get("/projects/{project_id}/clip-sets")
def project_clip_sets(project_id: str):
    return {"clip_sets": list_clip_sets(project_id)}


@router.post("/projects/{project_id}/clip-sets", status_code=201)
def create_project_clip_set(project_id: str, request: ClipSetCreate):
    _require_content_authorization()
    clip_set_id, task_id = _handle(
        create_clip_set, project_id, request.name, request.desired_count,
        request.min_duration, request.max_duration,
    )
    return {"clip_set_id": clip_set_id, "task_id": task_id}


@router.get("/clip-sets/{clip_set_id}")
def clip_set_detail(clip_set_id: str):
    return _handle(get_clip_set, clip_set_id)


@router.patch("/clip-candidates/{candidate_id}")
def patch_clip_candidate(candidate_id: str, request: ClipCandidateUpdate):
    return _handle(
        update_candidate,
        candidate_id,
        title=request.title,
        start=request.start,
        end=request.end,
        selected=request.selected,
        expected_revision=request.expected_revision,
        confirm_current_source=request.confirm_current_source,
    )


@router.put("/clip-candidates/{candidate_id}/layouts/{aspect_ratio}")
def put_clip_layout(candidate_id: str, aspect_ratio: str, request: ClipLayoutUpdate):
    return _handle(
        update_layout,
        candidate_id,
        aspect_ratio,
        enabled=request.enabled,
        composition=request.composition,
        focal_x=request.focal_x,
        focal_y=request.focal_y,
        subtitle_mode=request.subtitle_mode,
        style=request.style,
        expected_revision=request.expected_revision,
    )


@router.post("/projects/{project_id}/clip-renders", status_code=201)
def render_project_clips(project_id: str, request: ClipRenderCreate):
    for item in request.items:
        if item.aspect_ratio not in ASPECT_DIMENSIONS:
            raise HTTPException(422, "不支持的画面比例")
    task_id, render_ids = _handle(
        create_render_batch,
        project_id,
        [item.model_dump() for item in request.items],
        request.confirm_stale,
    )
    return {"task_id": task_id or None, "render_ids": render_ids, "reused": not bool(task_id)}


@router.get("/clip-renders/{render_id}")
def clip_render(render_id: str):
    return _handle(get_render, render_id)


@router.get("/clip-renders/{render_id}/download")
def download_clip_render(render_id: str):
    render = _handle(get_render, render_id)
    from pathlib import Path

    path = Path(render.get("path") or "")
    if render.get("status") != "success" or not path.is_file():
        raise HTTPException(404, "短片输出尚未准备完成")
    return FileResponse(path, filename=path.name, media_type="video/mp4")


@router.delete("/clip-renders/{render_id}")
def remove_clip_render(render_id: str, confirm: bool = False):
    if not confirm:
        raise HTTPException(400, detail={"code": "CONFIRMATION_REQUIRED", "message": "删除短片输出需要确认"})
    if not delete_render(render_id):
        raise HTTPException(404, "短片输出不存在")
    return {"deleted": True}
