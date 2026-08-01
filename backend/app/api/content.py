"""Editable content publication pack routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..models.database import get_db
from ..services.content_packs import (
    ContentPackError,
    create_content_pack,
    delete_pack,
    export_content_pack,
    list_content_packs,
    regenerate_section,
    update_pack,
    update_section,
    _load_pack,
)


router = APIRouter(prefix="/api")


class ContentPackCreate(BaseModel):
    name: str = Field(default="内容发布包", min_length=1, max_length=160)
    input_mode: str = Field(default="original", pattern="^(original|translated|bilingual)$")
    output_language: str = Field(default="auto", min_length=1, max_length=40)
    allow_translation_fallback: bool = False


class ContentPackUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=0)


class ContentSectionUpdate(BaseModel):
    content: dict
    expected_revision: int = Field(ge=0)


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
                "suggestion": "授权仅允许上传所选字幕文本、时间码、说话人标签和项目标题",
                "recoverable": True,
                "available_actions": ["authorize_content"],
            },
        )


def _handle(function, *args):
    try:
        return function(*args)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ContentPackError as exc:
        status = 409 if exc.error_code in {"CONTENT_REVISION_CONFLICT", "TRANSLATION_INCOMPLETE"} else 422
        raise HTTPException(
            status,
            detail={
                "code": exc.error_code,
                "message": str(exc),
                "recoverable": exc.recoverable,
                "available_actions": exc.available_actions,
                "details": exc.details,
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


@router.get("/projects/{project_id}/content-packs")
def content_packs(project_id: str):
    return {"packs": list_content_packs(project_id)}


@router.post("/projects/{project_id}/content-packs", status_code=201)
def create_project_content_pack(project_id: str, request: ContentPackCreate):
    _require_content_authorization()
    pack, task_id = _handle(
        create_content_pack,
        project_id,
        request.name,
        request.input_mode,
        request.output_language,
        request.allow_translation_fallback,
    )
    return {"pack": pack, "task_id": task_id}


@router.get("/content-packs/{pack_id}")
def get_content_pack(pack_id: str):
    return _handle(_load_pack, pack_id)


@router.patch("/content-packs/{pack_id}")
def patch_content_pack(pack_id: str, request: ContentPackUpdate):
    return _handle(update_pack, pack_id, request.name, request.expected_revision)


@router.delete("/content-packs/{pack_id}")
def remove_content_pack(pack_id: str, confirm: bool = False):
    if not confirm:
        raise HTTPException(400, detail={"code": "CONFIRMATION_REQUIRED", "message": "删除内容发布包需要确认"})
    if not delete_pack(pack_id):
        raise HTTPException(404, "内容发布包不存在")
    return {"deleted": True}


@router.put("/content-packs/{pack_id}/sections/{kind}")
def put_content_section(pack_id: str, kind: str, request: ContentSectionUpdate):
    return _handle(update_section, pack_id, kind, request.content, request.expected_revision)


@router.post("/content-packs/{pack_id}/sections/{kind}/regenerate")
def regenerate_content_section(pack_id: str, kind: str):
    _require_content_authorization()
    return {"task_id": _handle(regenerate_section, pack_id, kind)}


@router.post("/content-packs/{pack_id}/export")
def export_publication_pack(pack_id: str):
    path = _handle(export_content_pack, pack_id)
    return {"export_id": path.stem, "filename": path.name}


@router.get("/content-pack-exports/{export_id}/download")
def download_publication_pack(export_id: str):
    import tempfile
    from pathlib import Path

    path = Path(tempfile.gettempdir()) / f"{export_id}.zip"
    if not path.is_file() or not path.name.startswith("subtitle-factory-content-"):
        raise HTTPException(404, "内容发布包导出不存在或已过期")
    return FileResponse(path, filename=path.name, media_type="application/zip")
