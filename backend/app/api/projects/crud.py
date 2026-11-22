"""
字幕工厂 - 项目 CRUD、回收站、分组与媒体模式路由
"""

import os
import time
import uuid

from fastapi import APIRouter, HTTPException, Query

from ...models.database import init_db, project_to_dict
from ...models.schemas import (
    ProjectCreate,
    ProjectGroupUpdate,
    ProjectMediaModeUpdate,
    ProjectResponse,
    ProjectUpdate,
)
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
    get_task_manager,
    normalize_youtube_url,
    projects_dir,
)

router = APIRouter()


@router.get("/projects")
def list_projects(
    deleted: bool = False,
    page: int = 1,
    page_size: int = 100,
    search: str = "",
    source_type: str = "",
    group: str = "",
    status: str = "",
    sort: str = "updated_desc",
    include_playlist_items: bool = False,
    readiness: str = "",
):
    """Paginated/searchable project library; defaults preserve the legacy response."""
    init_db()
    page = max(1, int(page))
    page_size = max(1, min(200, int(page_size)))
    conditions = [f"p.deleted_at IS {'NOT ' if deleted else ''}NULL"]
    values: list = []
    if not distribution_capabilities().youtube:
        if source_type == "youtube":
            require_youtube_feature()
        conditions.append("p.source_type<>'youtube'")
    if not deleted and not include_playlist_items:
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM batch_items bi JOIN batches b ON b.id=bi.batch_id "
            "WHERE bi.project_id=p.id AND b.kind='youtube_playlist')"
        )
    if search:
        conditions.append("p.title LIKE ? ESCAPE '\\'")
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        values.append(f"%{escaped}%")
    if source_type in {"local", "youtube"}:
        conditions.append("p.source_type=?")
        values.append(source_type)
    if group:
        conditions.append("COALESCE(p.group_name,'')=?")
        values.append(group)
    if readiness == "subtitles":
        conditions.append("EXISTS (SELECT 1 FROM segments sr WHERE sr.project_id=p.id)")
    elif readiness in {"transcribe", "media"}:
        conditions.append("NOT EXISTS (SELECT 1 FROM segments sr WHERE sr.project_id=p.id)")
        media_present = "(media_exists(p.video_path) OR media_exists(p.audio_path))"
        conditions.append(media_present if readiness == "transcribe" else "NOT " + media_present)
    if status:
        conditions.append("EXISTS (SELECT 1 FROM tasks tf WHERE tf.project_id=p.id AND tf.status=?)")
        values.append(status)
    order = {
        "updated_desc": "COALESCE(p.deleted_at,p.updated_at) DESC",
        "updated_asc": "COALESCE(p.deleted_at,p.updated_at) ASC",
        "created_desc": "p.created_at DESC",
        "created_asc": "p.created_at ASC",
        "name_asc": "p.title COLLATE NOCASE ASC",
        "name_desc": "p.title COLLATE NOCASE DESC",
    }.get(sort, "COALESCE(p.deleted_at,p.updated_at) DESC")
    where = " AND ".join(conditions)
    db = get_db()
    db.create_function("media_exists", 1, lambda path: int(bool(path) and os.path.isfile(path)))
    total = db.execute(f"SELECT COUNT(*) FROM projects p WHERE {where}", values).fetchone()[0]
    rows = db.execute(
        """SELECT p.*, (SELECT COUNT(*) FROM segments s WHERE s.project_id=p.id) segments_count,
           (SELECT status FROM tasks t WHERE t.project_id=p.id ORDER BY updated_at DESC,t.rowid DESC LIMIT 1) latest_task_status,
           (SELECT message FROM tasks t WHERE t.project_id=p.id ORDER BY updated_at DESC,t.rowid DESC LIMIT 1) latest_task_message
           FROM projects p WHERE """
        + where
        + f" ORDER BY {order},p.id ASC LIMIT ? OFFSET ?",
        [*values, page_size, (page - 1) * page_size],
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


@router.delete("/projects/trash")
def empty_project_trash(confirm: bool = Query(False)):
    """清空回收站；必须显式确认，且不会删除回收站外的项目。"""
    if not confirm:
        raise HTTPException(
            400,
            detail={"code": "CONFIRMATION_REQUIRED", "message": "清空回收站需要显式确认"},
        )
    db = get_db()
    project_scope = (
        " AND source_type<>'youtube'"
        if not distribution_capabilities().youtube
        else ""
    )
    rows = db.execute(
        "SELECT * FROM projects WHERE deleted_at IS NOT NULL"
        f"{project_scope} ORDER BY deleted_at"
    ).fetchall()
    db.close()

    active = {row["id"]: _active_task_conflict(row["id"]) for row in rows}
    active = {project_id: ids for project_id, ids in active.items() if ids}
    if active:
        raise HTTPException(
            409,
            detail={
                "code": "ACTIVE_TASKS",
                "message": "回收站中仍有项目任务正在结束，请稍后重试",
                "projects": active,
            },
        )

    for row in rows:
        try:
            _purge_project_files(row)
        except OSError as exc:
            raise HTTPException(
                500,
                detail={
                    "code": "FILE_CLEANUP_FAILED",
                    "message": "项目文件清理失败，未删除数据库记录",
                    "project_id": row["id"],
                    "reason": str(exc),
                },
            ) from exc

    db = get_db()
    try:
        project_ids = [row["id"] for row in rows]
        # One statement per table/chunk instead of five statements per project.
        for table in (
            "transcription_segments",
            "transcription_runs",
            "segment_revisions",
            "segments",
            "tasks",
        ):
            for start in range(0, len(project_ids), 400):
                chunk = project_ids[start : start + 400]
                placeholders = ",".join("?" for _ in chunk)
                db.execute(
                    f"DELETE FROM {table} WHERE project_id IN ({placeholders})", chunk
                )
        db.execute("DELETE FROM projects WHERE deleted_at IS NOT NULL" + project_scope)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {
        "deleted_count": len(rows),
        "project_ids": [row["id"] for row in rows],
        "message": "回收站已清空",
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
    media_mode = "local"

    db = get_db()
    db.execute(
        """INSERT INTO projects
           (id, title, source_type, source_url, media_mode, language,
            target_language, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            project_id,
            req.title or "未命名项目",
            req.source_type,
            source_url,
            media_mode,
            req.language,
            req.target_language,
            now,
            now,
        ),
    )
    db.commit()
    db.close()

    # 创建项目目录
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


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def update_project(project_id: str, update: ProjectUpdate):
    """Update project metadata without disturbing media or subtitles."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    try:
        fields = []
        values = []
        if update.title is not None:
            fields.append("title=?")
            values.append(update.title)
        if update.target_language is not None:
            fields.append("target_language=?")
            values.append(update.target_language.strip())
        if not fields:
            raise HTTPException(400, "没有可更新的项目字段")
        fields.append("updated_at=?")
        values.extend([now, project_id])
        cursor = db.execute(
            f"UPDATE projects SET {', '.join(fields)} WHERE id=? AND deleted_at IS NULL",
            values,
        )
        if cursor.rowcount == 0:
            db.rollback()
            raise HTTPException(404, "项目不存在")
        row = db.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM segments s WHERE s.project_id=p.id) segments_count "
            "FROM projects p WHERE p.id=?",
            (project_id,),
        ).fetchone()
        db.commit()
    finally:
        db.close()
    return {**project_to_dict(row), "segments_count": row["segments_count"]}


@router.post("/projects/{project_id}/trash")
def trash_project(project_id: str, terminate: bool = Query(False)):
    """将项目移入回收站，保留所有媒体、字幕、任务和导出文件。"""
    row = _project_row(project_id)
    if not row:
        raise HTTPException(404, "项目不存在")
    if row["deleted_at"]:
        return {
            "project": project_to_dict(row),
            "terminated_task_ids": [],
            "message": "项目已在回收站",
        }

    active_task_ids = _active_task_conflict(project_id)
    if active_task_ids and not terminate:
        _raise_active_task_conflict(active_task_ids)
    terminated_task_ids = (
        get_task_manager().cancel_project_tasks(project_id) if active_task_ids else []
    )

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    try:
        db.execute(
            "UPDATE projects SET deleted_at=?, updated_at=? WHERE id=?",
            (now, now, project_id),
        )
        db.commit()
        updated = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    finally:
        db.close()
    return {
        "project": project_to_dict(updated),
        "terminated_task_ids": terminated_task_ids,
        "message": "项目已移入回收站",
    }


@router.post("/projects/{project_id}/restore")
def restore_project(project_id: str):
    """从回收站恢复项目。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    try:
        row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "项目不存在")
        db.execute(
            "UPDATE projects SET deleted_at=NULL, updated_at=? WHERE id=?",
            (now, project_id),
        )
        db.commit()
        restored = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    finally:
        db.close()
    return {"project": project_to_dict(restored), "message": "项目已恢复"}


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: str,
    permanent: bool = Query(False),
    terminate: bool = Query(False),
):
    """默认移入回收站；显式 permanent=true 才彻底删除。"""
    if not permanent:
        return trash_project(project_id, terminate=terminate)

    row = _project_row(project_id)
    if not row:
        raise HTTPException(404, "项目不存在")
    active_task_ids = _active_task_conflict(project_id)
    if active_task_ids:
        # Permanent cleanup must not race a worker that may still be returning
        # from FFmpeg/yt-dlp/ML inference. Move to trash first to terminate it,
        # then retry permanent deletion once no active task remains.
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


@router.patch("/projects/{project_id}/group", response_model=ProjectResponse)
def update_project_group(project_id: str, update: ProjectGroupUpdate):
    """设置项目分组；null、空字符串或纯空白表示未分组。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    try:
        cursor = db.execute(
            "UPDATE projects SET group_name = ?, updated_at = ? WHERE id = ?",
            (update.group_name, now, project_id),
        )
        if cursor.rowcount == 0:
            db.rollback()
            raise HTTPException(404, "项目不存在")
        row = db.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM segments s WHERE s.project_id = p.id) as segments_count "
            "FROM projects p WHERE p.id = ?",
            (project_id,),
        ).fetchone()
        db.commit()
    finally:
        db.close()

    return {**project_to_dict(row), "segments_count": row["segments_count"]}


@router.patch("/projects/{project_id}/media-mode")
def update_project_media_mode(project_id: str, update: ProjectMediaModeUpdate):
    from .media import _do_download

    row = _project_row(project_id)
    if not row:
        raise HTTPException(404, "项目不存在")
    require_youtube_feature()
    if row["source_type"] != "youtube":
        raise HTTPException(400, "本地导入项目只能使用本地模式")
    if update.media_mode == row["media_mode"]:
        return {"project": get_project(project_id), "message": "项目已使用该媒体模式"}
    active = _active_task_conflict(project_id)
    if active:
        raise HTTPException(
            409,
            detail={
                "code": "MEDIA_TASK_ACTIVE",
                "message": "项目已有任务正在运行，完成后再切换媒体模式",
                "task_ids": active,
            },
        )
    if row["video_path"] and os.path.isfile(row["video_path"]):
        db = get_db()
        db.execute(
            "UPDATE projects SET media_mode='local',updated_at=? WHERE id=?",
            (time.strftime("%Y-%m-%d %H:%M:%S"), project_id),
        )
        db.commit()
        db.close()
        return {"project": get_project(project_id), "message": "已改用本地视频"}
    if not row["source_url"]:
        raise HTTPException(400, "项目没有可下载的视频链接")
    tm = get_task_manager()
    task_id = tm.create_task(project_id, "switch_media_mode")
    tm.update_task(
        task_id,
        details={"materialization_reason": "mode_switch"},
    )
    tm.run_background(
        task_id,
        facade_attr("_do_download", _do_download),
        project_id,
        row["source_url"],
        set_media_mode="local",
        preserve_metadata=True,
        materialization_reason="mode_switch",
    )
    return {
        "project": get_project(project_id),
        "task_id": task_id,
        "message": "正在下载本地视频；成功后自动切换",
    }


@router.post("/projects/{project_id}/materialize-video")
def materialize_project_video(
    project_id: str,
    reason: str = Query("manual", pattern="^(manual|player_fallback|offline)$"),
):
    from .media import _do_download

    require_youtube_feature()
    row = _project_row(project_id)
    if not row:
        raise HTTPException(404, "项目不存在")
    if row["video_path"] and os.path.isfile(row["video_path"]):
        return {"project": get_project(project_id), "message": "本地视频已存在"}
    if row["source_type"] != "youtube" or not row["source_url"]:
        raise HTTPException(400, "项目没有可下载的视频链接")
    active = _active_task_conflict(project_id)
    if active:
        raise HTTPException(
            409,
            detail={
                "code": "MEDIA_TASK_ACTIVE",
                "message": "项目已有媒体或处理任务正在运行",
                "task_ids": active,
            },
        )
    tm = get_task_manager()
    task_id = tm.create_task(project_id, "materialize_video")
    tm.update_task(
        task_id,
        details={"materialization_reason": reason},
    )
    tm.run_background(
        task_id,
        facade_attr("_do_download", _do_download),
        project_id,
        row["source_url"],
        preserve_metadata=True,
        materialization_reason=reason,
    )
    return {"task_id": task_id, "message": "正在下载并保留本地视频"}
