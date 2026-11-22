"""
字幕工厂（简洁版）- 项目基础 CRUD 路由
"""

import os
import time
import uuid

from fastapi import APIRouter, HTTPException, Query

from ...models.database import init_db, project_to_dict
from ...models.schemas import ProjectCreate
from ...services.distribution import distribution_capabilities, require_youtube_feature
from .common import (
    _active_task_conflict,
    _project_row,
    _purge_project_files,
    _purge_project_record,
    _raise_active_task_conflict,
    facade_attr,
    get_db,
    get_logger,
    normalize_youtube_url,
    projects_dir,
)

router = APIRouter()


@router.get("/projects")
def list_projects(page: int = 1, page_size: int = 100):
    """返回项目列表，按最近更新时间倒序排列。"""
    init_db()
    page = max(1, int(page))
    page_size = max(1, min(200, int(page_size)))
    where = (
        "p.source_type<>'youtube'"
        if not distribution_capabilities().youtube
        else "1=1"
    )
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM projects p WHERE {where}").fetchone()[0]
    rows = db.execute(
        """SELECT p.*, (SELECT COUNT(*) FROM segments s WHERE s.project_id=p.id) segments_count,
           (SELECT status FROM tasks t WHERE t.project_id=p.id ORDER BY updated_at DESC,t.rowid DESC LIMIT 1) latest_task_status,
           (SELECT message FROM tasks t WHERE t.project_id=p.id ORDER BY updated_at DESC,t.rowid DESC LIMIT 1) latest_task_message
           FROM projects p WHERE """
        + where
        + " ORDER BY p.updated_at DESC, p.id ASC LIMIT ? OFFSET ?",
        (page_size, (page - 1) * page_size),
    ).fetchall()
    db.close()
    return {
        "projects": [
            {
                **project_to_dict(r),
                "segments_count": r["segments_count"],
                "latest_task_status": r["latest_task_status"],
                "latest_task_message": r["latest_task_message"],
            }
            for r in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


@router.post("/projects", status_code=201)
def create_project(req: ProjectCreate):
    """创建新项目"""
    if req.source_type == "youtube":
        require_youtube_feature()
    init_db()
    project_id = str(uuid.uuid4())
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    normalize_fn = facade_attr("normalize_youtube_url", normalize_youtube_url)
    source_url = (
        normalize_fn(req.source_url or "")
        if req.source_type == "youtube" and req.source_url
        else req.source_url
    )

    db = get_db()
    db.execute(
        """INSERT INTO projects
           (id, title, source_type, source_url, language,
            target_language, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            project_id,
            req.title or "未命名项目",
            req.source_type,
            source_url,
            req.language,
            req.target_language,
            now,
            now,
        ),
    )
    db.commit()
    db.close()

    os.makedirs(os.path.join(projects_dir(), project_id), exist_ok=True)

    get_logger().info(f"[API] 创建项目: {project_id}")
    return {"project_id": project_id, "message": "项目创建成功"}


@router.get("/projects/{project_id}")
def get_project(project_id: str):
    """获取项目详情"""
    db = get_db()
    row = db.execute(
        "SELECT p.*, (SELECT COUNT(*) FROM segments s WHERE s.project_id = p.id) as segments_count "
        "FROM projects p WHERE p.id = ?",
        (project_id,),
    ).fetchone()
    db.close()

    if not row:
        raise HTTPException(404, "项目不存在")

    return {**project_to_dict(row), "segments_count": row["segments_count"]}


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, permanent: bool = Query(True)):
    """永久删除项目及其关联的受管媒体、字幕与导出文件。"""
    _ = permanent
    row = _project_row(project_id)
    if not row:
        raise HTTPException(404, "项目不存在")
    active_task_ids = _active_task_conflict(project_id)
    if active_task_ids:
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
