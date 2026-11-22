"""
字幕工厂（简洁版）- 本地视频导入、下载与音频提取模块
"""

import os
import time

from fastapi import APIRouter, File, HTTPException, UploadFile

from ...services.audio_extractor import extract_audio
from .common import (
    VIDEO_EXTENSIONS,
    download_video,
    facade_attr,
    get_db,
    get_logger,
    get_task_manager,
    projects_dir,
    remove_managed_download_file,
)

router = APIRouter()


def _do_download(
    task_id: str,
    project_id: str,
    url: str,
    *,
    progress_start: float = 10,
    progress_end: float = 95,
    completed_progress: float = 100,
):
    """Download a video and atomically attach it to the project."""
    tm = get_task_manager()
    download_fn = facade_attr("download_video", download_video)
    remove_fn = facade_attr("remove_managed_download_file", remove_managed_download_file)
    video_path = download_fn(
        task_id,
        url,
        project_id,
        progress_start=progress_start,
        progress_end=progress_end,
    )
    tm.checkpoint(task_id)

    old_video_path = None
    committed = False
    db = get_db()
    try:
        details = (tm.get_task(task_id) or {}).get("details", {})
        existing = db.execute(
            "SELECT title, video_path FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        old_video_path = existing["video_path"] if existing else None
        title = details.get("title") or os.path.basename(video_path)
        db.execute(
            "UPDATE projects SET video_path = ?, title = ?, updated_at = ? WHERE id = ?",
            (video_path, title, time.strftime("%Y-%m-%d %H:%M:%S"), project_id),
        )
        tm.checkpoint(task_id)
        db.commit()
        committed = True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        if not committed:
            remove_fn(video_path, project_id=project_id, download_dir=None)

    if old_video_path and old_video_path != video_path:
        remove_fn(old_video_path, project_id=project_id, download_dir=None)

    tm.update_task(
        task_id,
        step="downloaded",
        progress=completed_progress,
        message="视频下载完成",
        details={"local_video_ready": True},
    )


@router.post("/projects/{project_id}/import-local")
async def import_local_video(
    project_id: str,
    file: UploadFile = File(...),
):
    """导入本地视频文件"""
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "项目不存在")

    project_dir = os.path.join(projects_dir(), project_id)
    os.makedirs(project_dir, exist_ok=True)

    extension = os.path.splitext(file.filename or "")[1].lower()
    if extension not in VIDEO_EXTENSIONS:
        db.close()
        raise HTTPException(
            400, "不支持的视频格式；请选择 MP4、MKV、MOV、WebM 或 AVI"
        )
    video_filename = f"local_video{extension}"
    video_path = os.path.join(project_dir, video_filename)

    with open(video_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)

    db.execute(
        """UPDATE projects
           SET video_path = ?, source_type = 'local', title = ?, updated_at = ?
           WHERE id = ?""",
        (
            video_path,
            file.filename or "本地视频",
            time.strftime("%Y-%m-%d %H:%M:%S"),
            project_id,
        ),
    )
    db.commit()
    db.close()

    get_logger().info(f"[API] 导入本地视频: {video_path}")
    return {
        "message": "本地视频导入成功",
        "video_path": video_path,
    }


def _do_extract_audio(
    task_id: str,
    project_id: str,
    video_path: str,
    progress_start: float = 5,
    progress_end: float = 100,
):
    tm = get_task_manager()
    audio_path = extract_audio(
        task_id,
        video_path,
        project_id,
        0,
        None,
        None,
        progress_start=progress_start,
        progress_end=progress_end,
    )
    tm.checkpoint(task_id)

    db = get_db()
    try:
        db.execute(
            "UPDATE projects SET audio_path = ?, updated_at = ? WHERE id = ?",
            (audio_path, time.strftime("%Y-%m-%d %H:%M:%S"), project_id),
        )
        tm.checkpoint(task_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
