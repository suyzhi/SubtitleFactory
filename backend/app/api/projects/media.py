"""
字幕工厂 - 视频下载、本地导入、音轨选择、音频提取与媒体文件访问路由
"""

import os
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ...models.schemas import MediaSelectionUpdate
from ...services.audio_extractor import extract_audio
from ...services.audio_preview import generate_track_preview
from ...services.distribution import require_youtube_feature
from ...services.local_models import get_imported
from ...services.playback_info import get_playback_info
from ...services.video_thumbnail import generate_video_thumbnail
from .common import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    _active_task_conflict,
    _project_row,
    download_audio_source,
    download_video,
    facade_attr,
    get_app_settings,
    get_db,
    get_logger,
    get_task_manager,
    normalize_youtube_url,
    projects_dir,
    remove_managed_download_file,
)
from .models import _resolve_model, _select_runtime

router = APIRouter()


@router.post("/projects/{project_id}/download")
def start_download(project_id: str, url: str = Form(...)):
    """开始下载 YouTube 视频（后台任务）"""
    require_youtube_feature()
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "项目不存在")

    normalized_url = facade_attr("normalize_youtube_url", normalize_youtube_url)(url)
    # 保存规范化 URL，避免 t=110s 等播放定位参数被当作下载范围。
    db.execute(
        "UPDATE projects SET source_url = ?, updated_at = ? WHERE id = ?",
        (normalized_url, time.strftime("%Y-%m-%d %H:%M:%S"), project_id),
    )
    db.commit()
    db.close()

    tm = get_task_manager()
    task_id = tm.create_task(project_id, "download")
    tm.run_background(
        task_id, facade_attr("_do_download", _do_download), project_id, normalized_url
    )
    return {"task_id": task_id, "message": "下载任务已创建"}


def _do_download(
    task_id: str,
    project_id: str,
    url: str,
    *,
    set_media_mode: str | None = None,
    preserve_metadata: bool = False,
    progress_start: float = 10,
    progress_end: float = 95,
    completed_progress: float = 100,
    materialization_reason: str = "",
):
    """Download a complete video and atomically attach it to the project."""
    try:
        app_settings = get_app_settings()
    except Exception:
        app_settings = {}
    tm = get_task_manager()
    download_fn = facade_attr("download_video", download_video)
    remove_fn = facade_attr("remove_managed_download_file", remove_managed_download_file)
    video_path = download_fn(
        task_id,
        url,
        project_id,
        ffmpeg_path=app_settings.get("ffmpeg_path"),
        download_dir=app_settings.get("download_directory"),
        quality=app_settings.get("download_quality") or "best",
        container=app_settings.get("download_container") or "mp4",
        progress_start=progress_start,
        progress_end=progress_end,
    )
    tm.checkpoint(task_id)

    old_video_path = None
    old_thumbnail_path = None
    candidate_thumbnail_path = None
    committed = False
    db = get_db()
    try:
        details = (tm.get_task(task_id) or {}).get("details", {})
        existing = db.execute(
            "SELECT title,video_path,thumbnail_url,thumbnail_path FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        old_video_path = existing["video_path"] if existing else None
        old_thumbnail_path = existing["thumbnail_path"] if existing else None
        candidate_thumbnail_path = details.get("thumbnail_path")
        title = (
            existing["title"]
            if preserve_metadata and existing
            else details.get("title") or os.path.basename(video_path)
        )
        thumbnail_url = (
            existing["thumbnail_url"]
            if preserve_metadata and existing
            else details.get("thumbnail_url")
        )
        thumbnail_path = (
            existing["thumbnail_path"]
            if preserve_metadata and existing
            else details.get("thumbnail_path")
        )
        mode_sql = ", media_mode = ?" if set_media_mode in {"local", "web"} else ""
        values = [video_path, title, thumbnail_url, thumbnail_path]
        if mode_sql:
            values.append(set_media_mode)
        values.extend([time.strftime("%Y-%m-%d %H:%M:%S"), project_id])
        db.execute(
            """UPDATE projects
               SET video_path = ?, title = ?, thumbnail_url = ?, thumbnail_path = ?
               """
            + mode_sql
            + """, updated_at = ?
               WHERE id = ?""",
            values,
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
            remove_fn(
                video_path,
                project_id=project_id,
                download_dir=app_settings.get("download_directory"),
            )
            remove_fn(
                candidate_thumbnail_path,
                project_id=project_id,
                download_dir=app_settings.get("download_directory"),
            )

    if old_video_path and old_video_path != video_path:
        remove_fn(
            old_video_path,
            project_id=project_id,
            download_dir=app_settings.get("download_directory"),
        )
    if (
        not preserve_metadata
        and old_thumbnail_path
        and old_thumbnail_path != candidate_thumbnail_path
    ):
        remove_fn(
            old_thumbnail_path,
            project_id=project_id,
            download_dir=app_settings.get("download_directory"),
        )
    if preserve_metadata and candidate_thumbnail_path != old_thumbnail_path:
        remove_fn(
            candidate_thumbnail_path,
            project_id=project_id,
            download_dir=app_settings.get("download_directory"),
        )

    tm.update_task(
        task_id,
        step="downloaded",
        progress=completed_progress,
        message="视频下载完成",
        details={
            "materialization_reason": materialization_reason,
            "local_video_ready": True,
            **({"media_mode": set_media_mode} if set_media_mode else {}),
        },
    )


def _do_prepare_audio(task_id: str, project_id: str, url: str):
    """Download a YouTube audio stream and safely replace the recognition WAV."""
    try:
        app_settings = get_app_settings()
    except Exception:
        app_settings = {}
    tm = get_task_manager()
    download_audio_fn = facade_attr("download_audio_source", download_audio_source)
    source_path = download_audio_fn(
        task_id,
        url,
        project_id,
        download_dir=app_settings.get("download_directory"),
    )
    staging_dir = Path(source_path).parent
    try:
        audio_path = extract_audio(
            task_id,
            source_path,
            project_id,
            progress_start=45,
            progress_end=78,
        )
        tm.checkpoint(task_id)
        details = (tm.get_task(task_id) or {}).get("details", {})
        db = get_db()
        try:
            row = db.execute("SELECT title FROM projects WHERE id=?", (project_id,)).fetchone()
            current_title = row["title"] if row else ""
            downloaded_title = details.get("title") or ""
            use_downloaded_title = (
                not current_title
                or current_title == "未命名项目"
                or current_title.startswith("YouTube - ")
            )
            db.execute(
                """UPDATE projects
                   SET audio_path=?, title=?, thumbnail_url=COALESCE(?,thumbnail_url),
                       updated_at=?
                   WHERE id=?""",
                (
                    audio_path,
                    downloaded_title if use_downloaded_title and downloaded_title else current_title,
                    details.get("thumbnail_url"),
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    project_id,
                ),
            )
            tm.checkpoint(task_id)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        tm.update_task(
            task_id,
            step="audio_ready",
            progress=80,
            message="音频已准备完成",
            details={"audio_path": audio_path, "audio_ready": True},
        )
        return audio_path
    finally:
        if staging_dir.name == f".audio-{task_id}" and staging_dir.is_dir():
            shutil.rmtree(staging_dir, ignore_errors=True)


@router.post("/projects/{project_id}/import-local")
async def import_local_video(
    project_id: str,
    file: UploadFile = File(...),
    autostart: bool = Form(False),
    model: str = Form("auto"),
    language: str = Form("auto"),
    runtime: Optional[str] = Form(None),
):
    """导入本地视频文件"""
    from .workflow import _do_workflow

    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "项目不存在")

    # 保存上传文件
    project_dir = os.path.join(projects_dir(), project_id)
    os.makedirs(project_dir, exist_ok=True)

    extension = os.path.splitext(file.filename or "")[1].lower()
    if extension not in VIDEO_EXTENSIONS:
        db.close()
        raise HTTPException(400, "不支持的视频格式；请选择 MP4、MKV、MOV、WebM 或 AVI")
    video_filename = f"local_video{extension}"
    video_path = os.path.join(project_dir, video_filename)

    with open(video_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)

    # 封面提取失败不影响视频导入；数据库会清空旧封面并回退默认图标。
    thumb_fn = facade_attr("generate_video_thumbnail", generate_video_thumbnail)
    try:
        thumbnail_path = thumb_fn(video_path, project_dir)
    except Exception as exc:
        get_logger().warning("[API] 本地视频封面提取异常，继续导入: %s", exc, exc_info=True)
        thumbnail_path = None

    # 更新项目
    db.execute(
        """UPDATE projects
           SET video_path = ?, source_type = 'local', media_mode = 'local', title = ?,
               thumbnail_url = NULL, thumbnail_path = ?, updated_at = ?
           WHERE id = ?""",
        (
            video_path,
            file.filename or "本地视频",
            thumbnail_path,
            time.strftime("%Y-%m-%d %H:%M:%S"),
            project_id,
        ),
    )
    db.commit()
    db.close()

    get_logger().info(f"[API] 导入本地视频: {video_path}")
    result = {
        "message": "本地视频导入成功",
        "video_path": video_path,
        "thumbnail_url": f"/api/projects/{project_id}/thumbnail" if thumbnail_path else None,
    }
    if autostart is True:
        resolved_model = _resolve_model(model, language)
        settings = get_app_settings()
        imported = get_imported(resolved_model) if resolved_model.startswith("local:") else None
        selected_runtime = _select_runtime(resolved_model, runtime, settings, imported)
        tm = get_task_manager()
        task_id = tm.create_task(project_id, "workflow")
        tm.update_task(
            task_id,
            details={
                "resume_payload": {
                    "project_id": project_id,
                    "model": resolved_model,
                    "language": language,
                    "source_url": None,
                    "runtime": selected_runtime,
                }
            },
        )
        tm.run_background(
            task_id,
            facade_attr("_do_workflow", _do_workflow),
            project_id,
            resolved_model,
            language,
            None,
            selected_runtime,
        )
        result.update({"task_id": task_id, "message": "视频已导入，正在自动生成字幕"})
    return result


@router.get("/projects/{project_id}/media-info")
def get_media_info(project_id: str):
    """Enumerate playable audio tracks via the bundled FFprobe runtime."""
    from ...services.media_tools import parse_duration, probe_media

    db = get_db()
    row = db.execute(
        "SELECT video_path,audio_track_index,range_start,range_end FROM projects WHERE id=?",
        (project_id,),
    ).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "项目不存在")
    if not row["video_path"] or not os.path.isfile(row["video_path"]):
        raise HTTPException(404, "视频文件不存在")
    info = probe_media(row["video_path"])
    audio_streams = [
        stream
        for stream in (info.get("streams") or [])
        if isinstance(stream, dict) and stream.get("codec_type") == "audio"
    ]
    tracks = []
    for index, stream in enumerate(audio_streams):
        tags = stream.get("tags") or {}
        if not isinstance(tags, dict):
            tags = {}
        try:
            sample_rate = int(stream["sample_rate"]) if stream.get("sample_rate") else None
        except (TypeError, ValueError):
            sample_rate = None
        tracks.append(
            {
                "index": index,
                "codec": stream.get("codec_name") or "",
                "channels": stream.get("channels"),
                "sample_rate": sample_rate,
                "language": tags.get("language") or tags.get("LANGUAGE") or "und",
                "title": tags.get("title") or tags.get("TITLE") or f"音轨 {index + 1}",
                "duration": parse_duration(stream.get("duration")),
            }
        )
    duration = parse_duration((info.get("format") or {}).get("duration"))
    return {
        "audio_tracks": tracks,
        "duration": duration,
        "selection": {
            "audio_track_index": row["audio_track_index"] or 0,
            "range_start": row["range_start"],
            "range_end": row["range_end"],
        },
    }


@router.get("/projects/{project_id}/media-track-preview")
def preview_media_track(
    project_id: str,
    track: int = Query(0, ge=0),
    start: float = Query(0, ge=0),
):
    db = get_db()
    row = db.execute("SELECT video_path FROM projects WHERE id=?", (project_id,)).fetchone()
    db.close()
    if not row or not row["video_path"] or not os.path.isfile(row["video_path"]):
        raise HTTPException(404, "项目视频不存在")
    try:
        path = generate_track_preview(project_id, row["video_path"], track, start)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return FileResponse(path, media_type="audio/wav", filename=f"track-{track + 1}-preview.wav")


@router.put("/projects/{project_id}/media-selection")
def update_media_selection(project_id: str, request: MediaSelectionUpdate):
    if get_task_manager().active_task_ids(project_id):
        raise HTTPException(409, "当前项目正在处理，请先完成或取消任务再修改音轨和范围")
    db = get_db()
    try:
        row = db.execute(
            "SELECT video_path,audio_track_index,range_start,range_end FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "项目不存在")
        changed = any(
            row[key] != getattr(request, key)
            for key in ("audio_track_index", "range_start", "range_end")
        )
        if not changed:
            return {
                "selection": request.model_dump(),
                "audio_reextract_required": False,
                "affected_results": [],
            }
        if row["video_path"] and os.path.isfile(row["video_path"]):
            from ...services.media_tools import parse_duration, probe_media

            info = probe_media(row["video_path"])
            audio_streams = [
                stream
                for stream in (info.get("streams") or [])
                if isinstance(stream, dict) and stream.get("codec_type") == "audio"
            ]
            if request.audio_track_index >= len(audio_streams):
                raise HTTPException(422, "所选音轨不存在")
            duration = parse_duration((info.get("format") or {}).get("duration"))
            if duration and (
                float(request.range_start or 0) >= duration
                or (request.range_end is not None and request.range_end > duration + 0.1)
            ):
                raise HTTPException(422, "截取范围超出视频时长")
        db.execute(
            """UPDATE projects SET audio_track_index=?,range_start=?,range_end=?,audio_path=NULL,
               updated_at=datetime('now','localtime') WHERE id=?""",
            (request.audio_track_index, request.range_start, request.range_end, project_id),
        )
        db.commit()
        return {
            "selection": request.model_dump(),
            "audio_reextract_required": True,
            "affected_results": ["audio", "transcription_candidates"],
            "preserved_results": [
                "source_media",
                "current_subtitles",
                "translations",
                "edit_history",
            ],
        }
    finally:
        db.close()


@router.post("/projects/{project_id}/extract-audio")
def start_extract_audio(project_id: str):
    """开始提取音频（后台任务）"""
    db = get_db()
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()

    if not row:
        raise HTTPException(404, "项目不存在")
    if not row["video_path"] or not os.path.exists(row["video_path"]):
        raise HTTPException(400, "视频文件不存在，请先下载或导入视频")

    tm = get_task_manager()
    task_id = tm.create_task(project_id, "extract_audio")
    tm.run_background(
        task_id, facade_attr("_do_extract_audio", _do_extract_audio), project_id, row["video_path"]
    )
    return {"task_id": task_id, "message": "音频提取任务已创建"}


@router.post("/projects/{project_id}/prepare-audio")
def start_prepare_audio(project_id: str):
    require_youtube_feature()
    row = _project_row(project_id)
    if not row:
        raise HTTPException(404, "项目不存在")
    if row["source_type"] != "youtube" or not row["source_url"]:
        raise HTTPException(400, "只有 YouTube 项目可以准备网页模式音频")
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
    task_id = tm.create_task(project_id, "prepare_audio")
    tm.run_background(
        task_id, facade_attr("_do_prepare_audio", _do_prepare_audio), project_id, row["source_url"]
    )
    return {"task_id": task_id, "message": "音频准备任务已创建"}


def _do_extract_audio(
    task_id: str,
    project_id: str,
    video_path: str,
    progress_start: float = 5,
    progress_end: float = 100,
):
    tm = get_task_manager()
    db = get_db()
    selection = db.execute(
        "SELECT audio_track_index,range_start,range_end FROM projects WHERE id=?", (project_id,)
    ).fetchone()
    db.close()
    audio_path = extract_audio(
        task_id,
        video_path,
        project_id,
        int(selection["audio_track_index"] or 0) if selection else 0,
        selection["range_start"] if selection else None,
        selection["range_end"] if selection else None,
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


@router.get("/projects/{project_id}/video")
def get_video_file(project_id: str):
    """获取视频文件（供前端视频播放器使用）"""
    db = get_db()
    row = db.execute("SELECT video_path FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()
    path = (
        Path(row["video_path"]).expanduser().resolve(strict=False)
        if row and row["video_path"]
        else None
    )
    if not path or not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(404, "视频文件不存在")
    return FileResponse(path)


@router.get("/projects/{project_id}/playback-info")
def playback_info(project_id: str):
    """Return read-only frame timing metadata; never mutates project state."""
    db = get_db()
    row = db.execute("SELECT video_path FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()
    if not row or not row["video_path"] or not os.path.isfile(row["video_path"]):
        raise HTTPException(404, "视频文件不存在")
    try:
        return get_playback_info(row["video_path"])
    except ValueError as exc:
        raise HTTPException(
            422,
            detail={"code": "VIDEO_METADATA_INVALID", "message": str(exc)},
        ) from exc


@router.get("/projects/{project_id}/thumbnail")
def get_thumbnail_file(project_id: str):
    """获取项目的持久化视频封面。"""
    db = get_db()
    row = db.execute("SELECT thumbnail_path FROM projects WHERE id = ?", (project_id,)).fetchone()
    db.close()
    path = (
        Path(row["thumbnail_path"]).expanduser().resolve(strict=False)
        if row and row["thumbnail_path"]
        else None
    )
    if not path or not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise HTTPException(404, "视频封面不存在")
    return FileResponse(
        path,
        headers={"Cache-Control": "no-cache"},
    )
