"""YouTube playlist discovery and durable per-video batch orchestration."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yt_dlp

from ..models.database import get_db, project_to_dict
from ..utils.config import PROJECTS_DIR
from ..utils.task_manager import TaskCancelled, task_manager
from .app_settings import get_app_settings
from .ai_settings import get_ai_settings
from .ai_providers import assigned_provider
from .audio_extractor import extract_audio
from .downloader import download_audio_source, download_video
from .subtitle_cleaner import clean_subtitles
from .subtitle_translator import translate_subtitles
from .transcriber import transcribe_audio


PLAYLIST_KIND = "youtube_playlist"
STAGE_ORDER = ("download", "extract_audio", "transcribe", "clean", "translate")
TERMINAL_STAGE_STATES = {"success", "partial", "failed", "cancelled", "skipped"}


class PlaylistBatchError(RuntimeError):
    def __init__(self, message: str, code: str, *, recoverable: bool = True):
        super().__init__(message)
        self.error_code = code
        self.recoverable = recoverable
        self.available_actions = ["retry"] if recoverable else []


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def is_youtube_playlist_url(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (host == "youtube.com" or host.endswith(".youtube.com")) and bool(parse_qs(parsed.query).get("list"))


def _entry_thumbnail(entry: dict[str, Any]) -> str | None:
    value = entry.get("thumbnail")
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    for thumbnail in reversed(entry.get("thumbnails") or []):
        candidate = thumbnail.get("url") if isinstance(thumbnail, dict) else None
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            return candidate
    return None


def preview_playlist(url: str) -> dict[str, Any]:
    value = (url or "").strip()
    if not is_youtube_playlist_url(value):
        raise PlaylistBatchError("请输入有效的 YouTube 播放列表链接", "PLAYLIST_URL_INVALID", recoverable=False)
    options = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "noplaylist": False,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(value, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise PlaylistBatchError(f"播放列表解析失败：{str(exc)[:300]}", "PLAYLIST_PARSE_FAILED") from exc
    if not isinstance(info, dict) or info.get("_type") != "playlist" or not info.get("id"):
        raise PlaylistBatchError("链接未解析为 YouTube 播放列表", "PLAYLIST_NOT_FOUND", recoverable=False)

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    unavailable = 0
    for source_position, raw in enumerate(info.get("entries") or [], 1):
        entry = raw if isinstance(raw, dict) else {}
        video_id = str(entry.get("id") or "").strip()
        available = bool(video_id)
        source_id = video_id or f"unavailable:{source_position}"
        if source_id in seen:
            continue
        seen.add(source_id)
        if not available:
            unavailable += 1
        entries.append({
            "source_id": source_id,
            "video_id": video_id or None,
            "position": len(entries) + 1,
            "source_position": source_position,
            "title": str(entry.get("title") or ("不可用视频" if not available else video_id)),
            "url": f"https://www.youtube.com/watch?v={video_id}" if available else None,
            "duration": float(entry.get("duration") or 0),
            "thumbnail_url": _entry_thumbnail(entry),
            "availability": "active" if available else "unavailable",
        })
    if not entries:
        raise PlaylistBatchError("播放列表中没有可处理的视频", "PLAYLIST_EMPTY", recoverable=False)
    thumbnail = _entry_thumbnail(info) or next((item["thumbnail_url"] for item in entries if item["thumbnail_url"]), None)
    return {
        "playlist": {
            "id": str(info["id"]),
            "title": str(info.get("title") or "YouTube 播放列表"),
            "url": str(info.get("webpage_url") or value),
            "channel": str(info.get("channel") or info.get("uploader") or ""),
            "thumbnail_url": thumbnail,
            "item_count": len(entries),
            "available_count": len(entries) - unavailable,
            "unavailable_count": unavailable,
            "total_duration": round(sum(float(item["duration"]) for item in entries), 2),
        },
        "items": entries,
        "warnings": ([f"有 {unavailable} 个条目不可用，将保留状态但不会创建项目"] if unavailable else []),
    }


def _selected_stages(configuration: dict[str, Any]) -> list[str]:
    stages = dict(configuration.get("stages") or {})
    wants_transcribe = bool(stages.get("transcribe") or stages.get("clean") or stages.get("translate"))
    selected = ["download", "extract_audio"]
    if wants_transcribe:
        selected.append("transcribe")
    if stages.get("clean"):
        selected.append("clean")
    if stages.get("translate"):
        selected.append("translate")
    return selected


def _validate_configuration(configuration: dict[str, Any]) -> None:
    stages = dict(configuration.get("stages") or {})
    wants_ai = bool(stages.get("clean") or stages.get("translate"))
    wants_transcribe = bool(stages.get("transcribe") or wants_ai)
    if wants_transcribe and not str(configuration.get("runtime") or "").strip():
        raise PlaylistBatchError("批量转写需要选择运行设备", "RUNTIME_SELECTION_REQUIRED", recoverable=False)
    if stages.get("translate") and (configuration.get("target_language") or "none") == "none":
        raise PlaylistBatchError("批量翻译需要目标语言", "TARGET_LANGUAGE_REQUIRED", recoverable=False)
    if wants_ai:
        if configuration.get("ai_authorized") is not True:
            raise PlaylistBatchError("批量 AI 处理需要明确确认内容授权", "AI_AUTHORIZATION_REQUIRED", recoverable=False)
        for operation in ("clean", "translate"):
            if not stages.get(operation):
                continue
            try:
                settings = assigned_provider(operation)
            except ValueError:
                settings = get_ai_settings(include_secret=True)
            if not settings.get("api_key"):
                raise PlaylistBatchError("AI 服务尚未配置可用的 API Key", "AI_API_KEY_REQUIRED", recoverable=False)


def _insert_stage_rows(db, item_id: str, configuration: dict[str, Any], available: bool) -> None:
    now = _now()
    selected = set(_selected_stages(configuration)) if available else set()
    snapshot = json.dumps(configuration, ensure_ascii=False)
    for stage in STAGE_ORDER:
        db.execute(
            """INSERT INTO batch_item_stages
               (item_id,stage,status,configuration_json,created_at,updated_at)
               VALUES (?,?,?,?,?,?)""",
            (item_id, stage, "waiting" if stage in selected else "skipped", snapshot, now, now),
        )


def create_or_sync_playlist(preview: dict[str, Any], configuration: dict[str, Any]) -> dict[str, Any]:
    _validate_configuration(configuration)
    playlist = preview["playlist"]
    configured_media_mode = configuration.get("media_mode") or get_app_settings().get("youtube_media_mode")
    media_mode = configured_media_mode if configured_media_mode in {"local", "web"} else "local"
    now = _now()
    created = False
    new_items: list[str] = []
    project_dirs: list[str] = []
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        batch = db.execute(
            "SELECT * FROM batches WHERE kind=? AND source_external_id=?",
            (PLAYLIST_KIND, playlist["id"]),
        ).fetchone()
        if batch:
            batch_id = batch["id"]
            db.execute(
                """UPDATE batches SET name=?,title=?,source_url=?,channel=?,thumbnail_url=?,
                   configuration_json=?,last_synced_at=?,updated_at=? WHERE id=?""",
                (playlist["title"], playlist["title"], playlist["url"], playlist["channel"],
                 playlist["thumbnail_url"], json.dumps(configuration, ensure_ascii=False), now, now, batch_id),
            )
        else:
            created = True
            batch_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO batches
                   (id,name,configuration_json,status,created_at,updated_at,kind,source_url,
                    source_external_id,title,channel,thumbnail_url,last_synced_at,paused)
                   VALUES (?,?,?,'pending',?,?,?,?,?,?,?,?,?,0)""",
                (batch_id, playlist["title"], json.dumps(configuration, ensure_ascii=False), now, now,
                 PLAYLIST_KIND, playlist["url"], playlist["id"], playlist["title"],
                 playlist["channel"], playlist["thumbnail_url"], now),
            )

        existing = {
            row["source_id"]: row
            for row in db.execute("SELECT * FROM batch_items WHERE batch_id=?", (batch_id,)).fetchall()
            if row["source_id"]
        }
        db.execute("UPDATE batch_items SET source_state='removed',updated_at=? WHERE batch_id=?", (now, batch_id))
        for entry in preview["items"]:
            old = existing.get(entry["source_id"])
            if old:
                db.execute(
                    """UPDATE batch_items SET position=?,title=?,duration=?,thumbnail_url=?,source_url=?,
                       source_state=?,updated_at=? WHERE id=?""",
                    (entry["position"], entry["title"], entry["duration"], entry["thumbnail_url"],
                     entry["url"], entry["availability"], now, old["id"]),
                )
                continue
            item_id = str(uuid.uuid4())
            available = entry["availability"] == "active" and bool(entry["url"])
            project_id = str(uuid.uuid4()) if available else None
            if project_id:
                db.execute(
                    """INSERT INTO projects
                       (id,title,source_type,source_url,thumbnail_url,media_mode,language,target_language,created_at,updated_at)
                       VALUES (?,?,'youtube',?,?,?,?,?,?,?)""",
                    (project_id, entry["title"], entry["url"], entry["thumbnail_url"],
                     media_mode, configuration.get("language", "auto"),
                     configuration.get("target_language", "zh"), now, now),
                )
                project_dirs.append(project_id)
            db.execute(
                """INSERT INTO batch_items
                   (id,batch_id,project_id,source_path,status,error,created_at,updated_at,
                    source_id,source_url,position,title,duration,thumbnail_url,source_state)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item_id, batch_id, project_id, entry["url"] or playlist["url"],
                 "pending" if available else "unavailable", None, now, now, entry["source_id"],
                 entry["url"], entry["position"], entry["title"], entry["duration"],
                 entry["thumbnail_url"], entry["availability"]),
            )
            _insert_stage_rows(db, item_id, configuration, available)
            if available:
                new_items.append(item_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    for project_id in project_dirs:
        (Path(PROJECTS_DIR) / project_id).mkdir(parents=True, exist_ok=True)
    _dispatch_batch(batch_id)
    _refresh_batch(batch_id)
    return {
        "action": "created" if created else "synced",
        "batch_id": batch_id,
        "added_count": len(new_items),
        "existing_count": max(0, len(preview["items"]) - len(new_items)),
        "batch": get_batch_detail(batch_id)["batch"],
    }


def _stage_context(item_id: str, stage: str):
    db = get_db()
    try:
        return db.execute(
            """SELECT s.*,i.batch_id,i.project_id,i.source_url,i.title item_title,i.position,
                      b.paused,b.configuration_json batch_configuration
               FROM batch_item_stages s JOIN batch_items i ON i.id=s.item_id
               JOIN batches b ON b.id=i.batch_id WHERE s.item_id=? AND s.stage=?""",
            (item_id, stage),
        ).fetchone()
    finally:
        db.close()


def _queue_stage(item_id: str, stage: str) -> str | None:
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        context = db.execute(
            """SELECT s.*,i.batch_id,i.project_id,i.source_url,i.title item_title,i.position,b.paused,b.title batch_title
               FROM batch_item_stages s JOIN batch_items i ON i.id=s.item_id
               JOIN batches b ON b.id=i.batch_id WHERE s.item_id=? AND s.stage=?""",
            (item_id, stage),
        ).fetchone()
        if not context or context["paused"] or context["status"] not in {"waiting", "failed", "partial", "blocked", "cancelled"}:
            db.rollback()
            return None
        claimed = db.execute(
            """UPDATE batch_item_stages SET status='queued',task_id=NULL,attempt=attempt+1,
               error_code=NULL,error=NULL,updated_at=? WHERE item_id=? AND stage=? AND status=?""",
            (_now(), item_id, stage, context["status"]),
        )
        if claimed.rowcount != 1:
            db.rollback()
            return None
        db.execute("UPDATE batch_items SET status='running',updated_at=? WHERE id=?", (_now(), item_id))
        db.execute("UPDATE batches SET status='running',updated_at=? WHERE id=?", (_now(), context["batch_id"]))
        db.commit()
    finally:
        db.close()
    task_id = task_manager.create_task(
        context["project_id"], stage,
        max_attempts=5 if stage == "download" else None,
    )
    task_manager.update_task(task_id, details={
        "batch_id": context["batch_id"], "batch_item_id": item_id,
        "batch_title": context["batch_title"], "batch_item_title": context["item_title"],
        "batch_position": context["position"], "batch_stage": stage,
    })
    db = get_db()
    try:
        db.execute("UPDATE batch_item_stages SET task_id=?,updated_at=? WHERE item_id=? AND stage=?", (task_id, _now(), item_id, stage))
        db.commit()
    finally:
        db.close()
    task_manager.run_background(task_id, _run_stage, item_id, stage)
    return task_id


def _queue_next_stage(item_id: str) -> str | None:
    db = get_db()
    try:
        item = db.execute(
            """SELECT i.*,b.paused FROM batch_items i JOIN batches b ON b.id=i.batch_id
               WHERE i.id=?""", (item_id,),
        ).fetchone()
        if not item or item["paused"] or item["source_state"] != "active":
            return None
        stages = db.execute(
            "SELECT * FROM batch_item_stages WHERE item_id=? ORDER BY CASE stage "
            "WHEN 'download' THEN 1 WHEN 'extract_audio' THEN 2 WHEN 'transcribe' THEN 3 "
            "WHEN 'clean' THEN 4 ELSE 5 END", (item_id,),
        ).fetchall()
    finally:
        db.close()
    for stage in stages:
        if stage["status"] in {"queued", "running", "paused", "failed", "partial", "cancelled"}:
            return None
        if stage["status"] in {"waiting", "blocked"}:
            return _queue_stage(item_id, stage["stage"])
    _refresh_item(item_id)
    return None


def _dispatch_batch(batch_id: str) -> None:
    """Submit only enough work to fill each resource class without pool head-of-line blocking."""
    db = get_db()
    try:
        batch = db.execute("SELECT paused FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch or batch["paused"]:
            return
        active_rows = db.execute(
            """SELECT s.stage,COUNT(*) count FROM batch_item_stages s
               JOIN batch_items i ON i.id=s.item_id WHERE i.batch_id=?
               AND s.status IN ('queued','running','paused') GROUP BY s.stage""", (batch_id,),
        ).fetchall()
        items = [row[0] for row in db.execute(
            "SELECT id FROM batch_items WHERE batch_id=? AND source_state='active' ORDER BY position,created_at",
            (batch_id,),
        ).fetchall()]
    finally:
        db.close()
    counts = {row["stage"]: int(row["count"]) for row in active_rows}
    resource_counts = {
        "io": counts.get("download", 0) + counts.get("extract_audio", 0),
        "ml": counts.get("transcribe", 0),
        "network_ai": counts.get("clean", 0) + counts.get("translate", 0),
    }
    limits = {"io": 2, "ml": 1, "network_ai": 2}
    resources = {"download": "io", "extract_audio": "io", "transcribe": "ml", "clean": "network_ai", "translate": "network_ai"}
    for item_id in items:
        db = get_db()
        try:
            stages = db.execute(
                "SELECT stage,status FROM batch_item_stages WHERE item_id=? ORDER BY CASE stage WHEN 'download' THEN 1 WHEN 'extract_audio' THEN 2 WHEN 'transcribe' THEN 3 WHEN 'clean' THEN 4 ELSE 5 END",
                (item_id,),
            ).fetchall()
        finally:
            db.close()
        candidate = None
        for row in stages:
            if row["status"] in {"queued", "running", "paused", "failed", "partial", "cancelled"}:
                candidate = None
                break
            if row["status"] in {"waiting", "blocked"}:
                candidate = row["stage"]
                break
        if not candidate:
            continue
        resource = resources[candidate]
        if resource_counts[resource] >= limits[resource]:
            continue
        if _queue_stage(item_id, candidate):
            resource_counts[resource] += 1


def _dispatch_when_task_terminal(task_id: str, batch_id: str) -> None:
    def watch() -> None:
        for _ in range(240):
            task = task_manager.get_task(task_id) or {}
            if task.get("status") in {"failed", "cancelled"}:
                _dispatch_batch(batch_id)
                return
            if task.get("status") == "success":
                return
            time.sleep(.25)
    threading.Thread(target=watch, name=f"playlist-dispatch-{task_id[:8]}", daemon=True).start()


def _mark_later_stages(item_id: str, current_stage: str, status: str) -> None:
    current_index = STAGE_ORDER.index(current_stage)
    later = STAGE_ORDER[current_index + 1:]
    if not later:
        return
    placeholders = ",".join("?" for _ in later)
    db = get_db()
    try:
        db.execute(
            f"UPDATE batch_item_stages SET status=?,updated_at=? WHERE item_id=? AND stage IN ({placeholders}) AND status IN ('waiting','blocked')",
            (status, _now(), item_id, *later),
        )
        db.commit()
    finally:
        db.close()


def _resolve_previous_stage_attempts(
    project_id: str, item_id: str, stage: str, current_task_id: str,
) -> int:
    """Keep the task center from showing failures already fixed by a retry."""
    db = get_db()
    try:
        cursor = db.execute(
            """UPDATE tasks SET status='success',progress=100,message='后续重试已成功完成',
                      error=NULL,error_code=NULL,recoverable=0,available_actions='[]',
                      updated_at=?
               WHERE project_id=? AND type=? AND id<>?
                 AND status IN ('failed','cancelled')
                 AND json_extract(details,'$.batch_item_id')=?""",
            (_now(), project_id, stage, current_task_id, item_id),
        )
        resolved = cursor.rowcount
        if stage == "transcribe":
            # Interrupted retries leave run-scoped draft rows behind.  Once a
            # later run is published successfully they have no user-visible
            # value; removing the old run cascades its staging segments.
            resolved += db.execute(
                """DELETE FROM transcription_runs
                   WHERE project_id=? AND coalesce(task_id,'')<>? AND status<>'success'""",
                (project_id, current_task_id),
            ).rowcount
        db.commit()
        return resolved
    finally:
        db.close()


def _run_stage(task_id: str, item_id: str, stage: str) -> None:
    context = _stage_context(item_id, stage)
    if not context:
        raise PlaylistBatchError("批次阶段不存在", "BATCH_STAGE_MISSING", recoverable=False)
    configuration = json.loads(context["configuration_json"] or context["batch_configuration"] or "{}")
    db = get_db()
    try:
        db.execute("UPDATE batch_item_stages SET status='running',updated_at=? WHERE item_id=? AND stage=?", (_now(), item_id, stage))
        # A task-manager retry reopens downstream stages that were temporarily blocked.
        current_index = STAGE_ORDER.index(stage)
        later = STAGE_ORDER[current_index + 1:]
        if later:
            placeholders = ",".join("?" for _ in later)
            db.execute(
                f"UPDATE batch_item_stages SET status='waiting',updated_at=? WHERE item_id=? AND stage IN ({placeholders}) AND status='blocked'",
                (_now(), item_id, *later),
            )
        db.commit()
    finally:
        db.close()
    try:
        _execute_stage(task_id, context, stage, configuration)
        public_task = task_manager.get_task(task_id) or {}
        result_status = "partial" if public_task.get("status") == "partial" else "success"
        db = get_db()
        try:
            db.execute(
                "UPDATE batch_item_stages SET status=?,error_code=NULL,error=NULL,updated_at=? WHERE item_id=? AND stage=?",
                (result_status, _now(), item_id, stage),
            )
            db.commit()
        finally:
            db.close()
        if result_status == "partial":
            _mark_later_stages(item_id, stage, "blocked")
        else:
            _resolve_previous_stage_attempts(
                context["project_id"], item_id, stage, task_id,
            )
            _dispatch_batch(context["batch_id"])
    except TaskCancelled:
        db = get_db()
        try:
            db.execute("UPDATE batch_item_stages SET status='cancelled',updated_at=? WHERE item_id=? AND stage=?", (_now(), item_id, stage))
            db.commit()
        finally:
            db.close()
        _mark_later_stages(item_id, stage, "cancelled")
        _refresh_item(item_id)
        _dispatch_when_task_terminal(task_id, context["batch_id"])
        raise
    except Exception as exc:
        db = get_db()
        try:
            db.execute(
                "UPDATE batch_item_stages SET status='failed',error_code=?,error=?,updated_at=? WHERE item_id=? AND stage=?",
                (str(getattr(exc, "error_code", "UNEXPECTED_ERROR")), str(exc)[:500], _now(), item_id, stage),
            )
            db.commit()
        finally:
            db.close()
        _mark_later_stages(item_id, stage, "blocked")
        _refresh_item(item_id)
        _dispatch_when_task_terminal(task_id, context["batch_id"])
        raise
    finally:
        _refresh_item(item_id)


def _execute_stage(task_id: str, context, stage: str, configuration: dict[str, Any]) -> None:
    project_id = context["project_id"]
    if not project_id:
        raise PlaylistBatchError("不可用视频没有项目", "PLAYLIST_ITEM_UNAVAILABLE", recoverable=False)
    db = get_db()
    try:
        project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    finally:
        db.close()
    if not project:
        raise PlaylistBatchError("关联项目不存在", "BATCH_PROJECT_MISSING", recoverable=False)
    if stage == "download":
        settings = get_app_settings()
        if project["media_mode"] == "web":
            audio_source = download_audio_source(
                task_id, context["source_url"], project_id,
                download_dir=settings.get("download_directory"),
            )
            staging_dir = Path(audio_source).parent
            try:
                audio_path = extract_audio(task_id, audio_source, project_id)
                details = (task_manager.get_task(task_id) or {}).get("details", {})
                db = get_db()
                try:
                    db.execute(
                        """UPDATE projects SET audio_path=?,title=?,thumbnail_url=COALESCE(?,thumbnail_url),
                           updated_at=? WHERE id=?""",
                        (audio_path, details.get("title") or context["item_title"],
                         details.get("thumbnail_url"), _now(), project_id),
                    )
                    db.commit()
                finally:
                    db.close()
            finally:
                if staging_dir.name == f".audio-{task_id}" and staging_dir.is_dir():
                    shutil.rmtree(staging_dir, ignore_errors=True)
            return
        video_path = download_video(
            task_id, context["source_url"], project_id,
            ffmpeg_path=settings.get("ffmpeg_path"), download_dir=settings.get("download_directory"),
            quality=configuration.get("download_quality") or settings.get("download_quality") or "best",
            container=configuration.get("download_container") or settings.get("download_container") or "mp4",
        )
        details = (task_manager.get_task(task_id) or {}).get("details", {})
        db = get_db()
        try:
            db.execute(
                """UPDATE projects SET video_path=?,title=?,thumbnail_url=?,thumbnail_path=?,updated_at=? WHERE id=?""",
                (video_path, details.get("title") or context["item_title"], details.get("thumbnail_url"),
                 details.get("thumbnail_path"), _now(), project_id),
            )
            db.commit()
        finally:
            db.close()
        return
    if stage == "extract_audio":
        if project["media_mode"] == "web" and project["audio_path"] and os.path.isfile(project["audio_path"]):
            return
        if not project["video_path"] or not os.path.isfile(project["video_path"]):
            raise PlaylistBatchError("视频文件不存在", "VIDEO_MISSING")
        audio_path = extract_audio(
            task_id, project["video_path"], project_id,
            int(project["audio_track_index"] or 0), project["range_start"], project["range_end"],
        )
        db = get_db()
        try:
            db.execute("UPDATE projects SET audio_path=?,updated_at=? WHERE id=?", (audio_path, _now(), project_id))
            db.commit()
        finally:
            db.close()
    elif stage == "transcribe":
        if not project["audio_path"] or not os.path.isfile(project["audio_path"]):
            raise PlaylistBatchError("音频文件不存在", "AUDIO_MISSING")
        transcribe_audio(
            task_id, project["audio_path"], project_id,
            configuration.get("language", "auto"), configuration.get("model", "small"),
            configuration.get("runtime") or None,
        )
    elif stage == "clean":
        clean_subtitles(
            task_id, project_id, int(configuration.get("clean_target_length") or 42),
            configuration.get("clean_provider_id"), configuration.get("clean_model"),
        )
    elif stage == "translate":
        target = configuration.get("target_language") or "zh"
        if target == "none":
            raise PlaylistBatchError("批量翻译需要目标语言", "TARGET_LANGUAGE_REQUIRED", recoverable=False)
        translate_subtitles(
            task_id, project_id, target,
            configuration.get("translate_provider_id"), configuration.get("translate_model"),
        )


def _refresh_item(item_id: str) -> None:
    db = get_db()
    try:
        item = db.execute("SELECT * FROM batch_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            return
        if item["source_state"] == "unavailable":
            status = "unavailable"
        else:
            states = [row[0] for row in db.execute("SELECT status FROM batch_item_stages WHERE item_id=?", (item_id,)).fetchall()]
            if any(value in {"running", "queued"} for value in states): status = "running"
            elif any(value == "paused" for value in states): status = "paused"
            elif any(value == "partial" for value in states): status = "partial"
            elif any(value == "failed" for value in states): status = "failed"
            elif any(value == "cancelled" for value in states): status = "cancelled"
            elif all(value in {"success", "skipped"} for value in states): status = "success"
            else: status = "pending"
        db.execute("UPDATE batch_items SET status=?,updated_at=? WHERE id=?", (status, _now(), item_id))
        db.commit()
        batch_id = item["batch_id"]
    finally:
        db.close()
    _refresh_batch(batch_id)


def _refresh_batch(batch_id: str) -> None:
    db = get_db()
    try:
        batch = db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            return
        states = [row[0] for row in db.execute(
            "SELECT status FROM batch_items WHERE batch_id=? AND source_state<>'removed'", (batch_id,),
        ).fetchall()]
        if batch["paused"]: status = "paused"
        elif any(value in {"running", "pending"} for value in states): status = "running"
        elif states and all(value == "success" for value in states): status = "success"
        elif states and all(value in {"failed", "unavailable"} for value in states): status = "failed"
        elif any(value in {"failed", "partial", "unavailable", "cancelled"} for value in states): status = "partial"
        else: status = "pending"
        db.execute("UPDATE batches SET status=?,updated_at=? WHERE id=?", (status, _now(), batch_id))
        db.commit()
    finally:
        db.close()


def _task_progress(task_id: str | None) -> float:
    if not task_id:
        return 0.0
    task = task_manager.get_task(task_id)
    return float(task.get("progress") or 0) if task else 0.0


def get_batch_detail(batch_id: str) -> dict[str, Any]:
    db = get_db()
    try:
        batch = db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise PlaylistBatchError("批次不存在", "BATCH_NOT_FOUND", recoverable=False)
        rows = db.execute(
            """SELECT i.*,p.title project_title,p.source_type,p.source_url project_source_url,
                      p.video_path,p.audio_path,p.thumbnail_path,p.language,p.target_language,
                      p.created_at project_created_at,p.updated_at project_updated_at,p.deleted_at,
                      p.edit_revision,p.media_status
               FROM batch_items i LEFT JOIN projects p ON p.id=i.project_id
               WHERE i.batch_id=? ORDER BY i.position,i.created_at""", (batch_id,),
        ).fetchall()
        stages = db.execute(
            "SELECT * FROM batch_item_stages WHERE item_id IN (SELECT id FROM batch_items WHERE batch_id=?)",
            (batch_id,),
        ).fetchall()
    finally:
        db.close()
    stage_map: dict[str, dict[str, Any]] = {}
    for row in stages:
        stage_map.setdefault(row["item_id"], {})[row["stage"]] = {
            "status": row["status"], "task_id": row["task_id"], "attempt": row["attempt"],
            "error_code": row["error_code"], "error": row["error"],
            "progress": _task_progress(row["task_id"]),
        }
    items = []
    for row in rows:
        project = None
        if row["project_id"]:
            project_row = get_db()
            try:
                raw_project = project_row.execute(
                    "SELECT p.*,(SELECT COUNT(*) FROM segments s WHERE s.project_id=p.id) segments_count FROM projects p WHERE p.id=?",
                    (row["project_id"],),
                ).fetchone()
            finally:
                project_row.close()
            if raw_project:
                project = {**project_to_dict(raw_project), "segments_count": raw_project["segments_count"]}
        items.append({
            "id": row["id"], "project_id": row["project_id"], "source_id": row["source_id"],
            "source_url": row["source_url"], "position": row["position"], "title": row["title"],
            "duration": row["duration"], "thumbnail_url": row["thumbnail_url"],
            "source_state": row["source_state"], "status": row["status"], "error": row["error"],
            "project": project, "stages": stage_map.get(row["id"], {}),
        })
    active = [item for item in items if item["source_state"] != "removed"]
    selected_units = [stage for item in active for stage in item["stages"].values() if stage["status"] != "skipped"]
    completed = sum(1 for item in active if item["status"] == "success")
    failed = sum(1 for item in active if item["status"] in {"failed", "partial", "unavailable"})
    progress = 0.0
    if selected_units:
        progress = sum(100 if stage["status"] == "success" else stage["progress"] if stage["status"] in {"running", "queued"} else 0 for stage in selected_units) / len(selected_units)
    payload = dict(batch)
    payload["configuration"] = json.loads(payload.pop("configuration_json") or "{}")
    payload.update({"item_count": len(active), "completed_count": completed, "failed_count": failed, "progress": round(progress, 1)})
    return {"batch": payload, "items": items}


def list_playlist_batches() -> dict[str, Any]:
    db = get_db()
    try:
        ids = [row[0] for row in db.execute(
            "SELECT id FROM batches WHERE kind=? ORDER BY updated_at DESC", (PLAYLIST_KIND,),
        ).fetchall()]
    finally:
        db.close()
    return {"batches": [get_batch_detail(batch_id) for batch_id in ids]}


def sync_playlist_batch(batch_id: str) -> dict[str, Any]:
    detail = get_batch_detail(batch_id)
    batch = detail["batch"]
    if batch.get("kind") != PLAYLIST_KIND or not batch.get("source_url"):
        raise PlaylistBatchError("批次不是可同步的 YouTube 播放列表", "BATCH_NOT_PLAYLIST", recoverable=False)
    return create_or_sync_playlist(preview_playlist(batch["source_url"]), dict(batch.get("configuration") or {}))


def pause_batch(batch_id: str) -> dict[str, Any]:
    db = get_db()
    try:
        db.execute("UPDATE batches SET paused=1,status='paused',updated_at=? WHERE id=?", (_now(), batch_id))
        task_ids = [row[0] for row in db.execute(
            "SELECT task_id FROM batch_item_stages WHERE item_id IN (SELECT id FROM batch_items WHERE batch_id=?) AND status IN ('queued','running') AND task_id IS NOT NULL",
            (batch_id,),
        ).fetchall()]
        db.execute("UPDATE batch_item_stages SET status='paused',updated_at=? WHERE task_id IN (SELECT task_id FROM batch_item_stages WHERE item_id IN (SELECT id FROM batch_items WHERE batch_id=?) AND status IN ('queued','running'))", (_now(), batch_id))
        db.commit()
    finally:
        db.close()
    for task_id in task_ids:
        task_manager.pause_task(task_id)
    return get_batch_detail(batch_id)


def resume_batch(batch_id: str) -> dict[str, Any]:
    db = get_db()
    try:
        db.execute("UPDATE batches SET paused=0,status='running',updated_at=? WHERE id=?", (_now(), batch_id))
        paused = db.execute(
            "SELECT item_id,stage,task_id FROM batch_item_stages WHERE item_id IN (SELECT id FROM batch_items WHERE batch_id=?) AND status='paused'",
            (batch_id,),
        ).fetchall()
        item_ids = [row[0] for row in db.execute("SELECT id FROM batch_items WHERE batch_id=? AND source_state='active'", (batch_id,)).fetchall()]
        db.commit()
    finally:
        db.close()
    for row in paused:
        if row["task_id"] and task_manager.resume_task(row["task_id"]):
            db = get_db(); db.execute("UPDATE batch_item_stages SET status='running',updated_at=? WHERE item_id=? AND stage=?", (_now(), row["item_id"], row["stage"])); db.commit(); db.close()
    _dispatch_batch(batch_id)
    return get_batch_detail(batch_id)


def cancel_pending_batch(batch_id: str) -> dict[str, Any]:
    db = get_db()
    try:
        task_ids = [row[0] for row in db.execute(
            "SELECT task_id FROM batch_item_stages WHERE item_id IN (SELECT id FROM batch_items WHERE batch_id=?) AND status IN ('queued','running','paused') AND task_id IS NOT NULL",
            (batch_id,),
        ).fetchall()]
        db.execute("UPDATE batches SET paused=1,status='cancelled',updated_at=? WHERE id=?", (_now(), batch_id))
        db.execute("UPDATE batch_item_stages SET status='cancelled',updated_at=? WHERE item_id IN (SELECT id FROM batch_items WHERE batch_id=?) AND status IN ('waiting','queued','running','paused','blocked')", (_now(), batch_id))
        db.execute("UPDATE batch_items SET status='cancelled',updated_at=? WHERE batch_id=? AND status IN ('pending','running','paused')", (_now(), batch_id))
        db.commit()
    finally:
        db.close()
    for task_id in task_ids:
        task_manager.cancel_task(task_id)
    return get_batch_detail(batch_id)


def retry_failed(batch_id: str, item_id: str | None = None) -> dict[str, Any]:
    db = get_db()
    try:
        params: list[Any] = [batch_id]
        item_sql = "SELECT id FROM batch_items WHERE batch_id=? AND source_state='active'"
        if item_id:
            item_sql += " AND id=?"; params.append(item_id)
        item_ids = [row[0] for row in db.execute(item_sql, params).fetchall()]
        for current_item_id in item_ids:
            stages = db.execute(
                "SELECT stage,status FROM batch_item_stages WHERE item_id=? ORDER BY CASE stage WHEN 'download' THEN 1 WHEN 'extract_audio' THEN 2 WHEN 'transcribe' THEN 3 WHEN 'clean' THEN 4 ELSE 5 END",
                (current_item_id,),
            ).fetchall()
            failed_index = next((index for index, row in enumerate(stages) if row["status"] in {"failed", "partial", "cancelled"}), None)
            if failed_index is None:
                continue
            for row in stages[failed_index:]:
                if row["status"] != "skipped":
                    db.execute("UPDATE batch_item_stages SET status='waiting',task_id=NULL,error_code=NULL,error=NULL,updated_at=? WHERE item_id=? AND stage=?", (_now(), current_item_id, row["stage"]))
            db.execute("UPDATE batch_items SET status='pending',error=NULL,updated_at=? WHERE id=?", (_now(), current_item_id))
        db.execute("UPDATE batches SET paused=0,status='running',updated_at=? WHERE id=?", (_now(), batch_id))
        db.commit()
    finally:
        db.close()
    _dispatch_batch(batch_id)
    return get_batch_detail(batch_id)


def enable_batch_stage(batch_id: str, stage: str, configuration: dict[str, Any]) -> dict[str, Any]:
    if stage not in {"transcribe", "clean", "translate"}:
        raise PlaylistBatchError("不支持的批量阶段", "BATCH_STAGE_INVALID", recoverable=False)
    db = get_db()
    try:
        batch = db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise PlaylistBatchError("批次不存在", "BATCH_NOT_FOUND", recoverable=False)
        merged = json.loads(batch["configuration_json"] or "{}")
        merged.update(configuration)
        flags = dict(merged.get("stages") or {})
        flags["transcribe"] = True
        if stage in {"clean", "translate"}: flags[stage] = True
        merged["stages"] = flags
        _validate_configuration(merged)
        snapshot = json.dumps(merged, ensure_ascii=False)
        needed = ["transcribe", stage] if stage != "transcribe" else ["transcribe"]
        for needed_stage in dict.fromkeys(needed):
            db.execute(
                """UPDATE batch_item_stages SET status=CASE WHEN status='success' THEN status ELSE 'waiting' END,
                   task_id=CASE WHEN status='success' THEN task_id ELSE NULL END,
                   configuration_json=?,error_code=NULL,error=NULL,updated_at=?
                   WHERE stage=? AND item_id IN (SELECT id FROM batch_items WHERE batch_id=? AND source_state='active')""",
                (snapshot, _now(), needed_stage, batch_id),
            )
        db.execute("UPDATE batches SET configuration_json=?,paused=0,status='running',updated_at=? WHERE id=?", (snapshot, _now(), batch_id))
        item_ids = [row[0] for row in db.execute("SELECT id FROM batch_items WHERE batch_id=? AND source_state='active'", (batch_id,)).fetchall()]
        db.commit()
    finally:
        db.close()
    _dispatch_batch(batch_id)
    return get_batch_detail(batch_id)


def recover_playlist_batches(interrupted_tasks: list[dict[str, Any]]) -> None:
    interrupted_ids = {task["id"] for task in interrupted_tasks}
    if interrupted_ids:
        db = get_db()
        try:
            placeholders = ",".join("?" for _ in interrupted_ids)
            db.execute(
                f"UPDATE batch_item_stages SET status='failed',error_code='APP_INTERRUPTED',error='应用在任务执行期间退出',updated_at=? WHERE task_id IN ({placeholders})",
                (_now(), *interrupted_ids),
            )
            db.commit()
            batch_ids = [row[0] for row in db.execute("SELECT id FROM batches WHERE kind=?", (PLAYLIST_KIND,)).fetchall()]
        finally:
            db.close()
        for batch_id in batch_ids:
            _refresh_batch(batch_id)
