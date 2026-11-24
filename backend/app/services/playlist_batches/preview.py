"""YouTube playlist URL parsing, flat preview extraction, validation, and sync."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ...models.database import get_db
from ...utils.config import PROJECTS_DIR
from ..ai_providers import assigned_provider
from ..ai_settings import get_ai_settings
from ..download_errors import DownloadServiceError
from .common import (
    PLAYLIST_KIND,
    STAGE_ORDER,
    PlaylistBatchError,
    _now,
    extract_youtube_info,
    facade_attr,
)


def is_youtube_playlist_url(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (host == "youtube.com" or host.endswith(".youtube.com")) and bool(
        parse_qs(parsed.query).get("list")
    )


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
        raise PlaylistBatchError(
            "请输入有效的 YouTube 播放列表链接", "PLAYLIST_URL_INVALID", recoverable=False
        )
    options = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "noplaylist": False,
    }

    def contains_permission_entries(result: dict) -> bool:
        for raw in result.get("entries") or []:
            entry = raw if isinstance(raw, dict) else {}
            availability = str(entry.get("availability") or "").lower()
            title = str(entry.get("title") or "").lower()
            if availability in {"private", "premium_only", "subscriber_only", "needs_auth"}:
                return True
            if any(marker in title for marker in ("private video", "members-only", "members only")):
                return True
        return False

    extract_fn = facade_attr("extract_youtube_info", extract_youtube_info)
    try:
        info, attempt_details = extract_fn(
            value,
            options=options,
            task_id=f"playlist-preview-{uuid.uuid4().hex[:8]}",
            stage="playlist_preview",
            auth_if_result=contains_permission_entries,
        )
    except DownloadServiceError as exc:
        raise PlaylistBatchError(
            str(exc),
            exc.error_code,
            recoverable=exc.recoverable,
            suggestion=exc.suggestion,
            actions=exc.available_actions,
            automatic_retry=exc.automatic_retry,
        ) from exc
    if not isinstance(info, dict) or info.get("_type") != "playlist" or not info.get("id"):
        raise PlaylistBatchError(
            "链接未解析为 YouTube 播放列表", "PLAYLIST_NOT_FOUND", recoverable=False
        )

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    unavailable = 0
    for source_position, raw in enumerate(info.get("entries") or [], 1):
        entry = raw if isinstance(raw, dict) else {}
        video_id = str(entry.get("id") or "").strip()
        raw_availability = str(entry.get("availability") or "").lower()
        permission_required = raw_availability in {
            "private",
            "premium_only",
            "subscriber_only",
            "needs_auth",
        }
        available = bool(video_id) and not permission_required
        state = (
            "active"
            if available
            else "permission_required"
            if video_id and permission_required
            else "unavailable"
        )
        error_code = None
        suggestion = None
        if permission_required:
            error_code = (
                "PRIVATE_VIDEO"
                if raw_availability == "private"
                else "MEMBERSHIP_REQUIRED"
                if raw_availability in {"premium_only", "subscriber_only"}
                else "AUTH_REQUIRED"
            )
            suggestion = "请在 Google Chrome 中切换到有权限的账号后重新预览"
        source_id = video_id or f"unavailable:{source_position}"
        if source_id in seen:
            continue
        seen.add(source_id)
        if state != "active":
            unavailable += 1
        entries.append(
            {
                "source_id": source_id,
                "video_id": video_id or None,
                "position": len(entries) + 1,
                "source_position": source_position,
                "title": str(entry.get("title") or ("不可用视频" if not available else video_id)),
                "url": f"https://www.youtube.com/watch?v={video_id}" if available else None,
                "duration": float(entry.get("duration") or 0),
                "thumbnail_url": _entry_thumbnail(entry),
                "availability": state,
                "error_code": error_code,
                "suggestion": suggestion,
            }
        )
    if not entries:
        raise PlaylistBatchError("播放列表中没有可处理的视频", "PLAYLIST_EMPTY", recoverable=False)
    thumbnail = _entry_thumbnail(info) or next(
        (item["thumbnail_url"] for item in entries if item["thumbnail_url"]), None
    )
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
        "download": attempt_details,
        "warnings": (
            [f"有 {unavailable} 个条目不可用，将保留状态但不会创建项目"] if unavailable else []
        ),
    }


def _selected_stages(configuration: dict[str, Any]) -> list[str]:
    stages = dict(configuration.get("stages") or {})
    wants_transcribe = bool(
        stages.get("transcribe") or stages.get("clean") or stages.get("translate")
    )
    selected = ["download", "extract_audio"]
    if wants_transcribe:
        selected.append("transcribe")
    if stages.get("clean"):
        selected.append("clean")
    if stages.get("translate"):
        selected.append("translate")
    return selected


def _validate_configuration(configuration: dict[str, Any]) -> None:
    provider_fn = facade_attr("assigned_provider", assigned_provider)
    stages = dict(configuration.get("stages") or {})
    wants_ai = bool(stages.get("clean") or stages.get("translate"))
    wants_transcribe = bool(stages.get("transcribe") or wants_ai)
    if wants_transcribe and not str(configuration.get("runtime") or "").strip():
        raise PlaylistBatchError(
            "批量转写需要选择运行设备", "RUNTIME_SELECTION_REQUIRED", recoverable=False
        )
    if stages.get("translate") and (configuration.get("target_language") or "none") == "none":
        raise PlaylistBatchError(
            "批量翻译需要目标语言", "TARGET_LANGUAGE_REQUIRED", recoverable=False
        )
    if wants_ai:
        if configuration.get("ai_authorized") is not True:
            raise PlaylistBatchError(
                "批量 AI 处理需要明确确认内容授权",
                "AI_AUTHORIZATION_REQUIRED",
                recoverable=False,
            )
        for operation in ("clean", "translate"):
            if not stages.get(operation):
                continue
            try:
                settings = provider_fn(operation)
            except ValueError:
                settings = get_ai_settings(include_secret=True)
            if not settings.get("api_key"):
                raise PlaylistBatchError(
                    "AI 服务尚未配置可用的 API Key", "AI_API_KEY_REQUIRED", recoverable=False
                )


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
    from .controls import get_batch_detail
    from .scheduler import _dispatch_batch, _refresh_batch

    _validate_configuration(configuration)
    playlist = preview["playlist"]
    media_mode = "local"
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
                (
                    playlist["title"],
                    playlist["title"],
                    playlist["url"],
                    playlist["channel"],
                    playlist["thumbnail_url"],
                    json.dumps(configuration, ensure_ascii=False),
                    now,
                    now,
                    batch_id,
                ),
            )
        else:
            created = True
            batch_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO batches
                   (id,name,configuration_json,status,created_at,updated_at,kind,source_url,
                    source_external_id,title,channel,thumbnail_url,last_synced_at,paused)
                   VALUES (?,?,?,'pending',?,?,?,?,?,?,?,?,?,0)""",
                (
                    batch_id,
                    playlist["title"],
                    json.dumps(configuration, ensure_ascii=False),
                    now,
                    now,
                    PLAYLIST_KIND,
                    playlist["url"],
                    playlist["id"],
                    playlist["title"],
                    playlist["channel"],
                    playlist["thumbnail_url"],
                    now,
                ),
            )

        existing = {
            row["source_id"]: row
            for row in db.execute(
                "SELECT * FROM batch_items WHERE batch_id=?", (batch_id,)
            ).fetchall()
            if row["source_id"]
        }
        db.execute(
            "UPDATE batch_items SET source_state='removed',updated_at=? WHERE batch_id=?",
            (now, batch_id),
        )
        for entry in preview["items"]:
            old = existing.get(entry["source_id"])
            if old:
                source_error = (
                    f"{entry.get('error_code')}：{entry.get('suggestion')}"
                    if entry.get("error_code")
                    else None
                )
                db.execute(
                    """UPDATE batch_items SET position=?,title=?,duration=?,thumbnail_url=?,source_url=?,
                       source_state=?,error=CASE WHEN ? IS NOT NULL THEN ? ELSE error END,
                       updated_at=? WHERE id=?""",
                    (
                        entry["position"],
                        entry["title"],
                        entry["duration"],
                        entry["thumbnail_url"],
                        entry["url"],
                        entry["availability"],
                        source_error,
                        source_error,
                        now,
                        old["id"],
                    ),
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
                    (
                        project_id,
                        entry["title"],
                        entry["url"],
                        entry["thumbnail_url"],
                        media_mode,
                        configuration.get("language", "auto"),
                        configuration.get("target_language", "zh"),
                        now,
                        now,
                    ),
                )
                project_dirs.append(project_id)
            db.execute(
                """INSERT INTO batch_items
                   (id,batch_id,project_id,source_path,status,error,created_at,updated_at,
                    source_id,source_url,position,title,duration,thumbnail_url,source_state)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item_id,
                    batch_id,
                    project_id,
                    entry["url"] or playlist["url"],
                    "pending" if available else "unavailable",
                    (
                        f"{entry.get('error_code')}：{entry.get('suggestion')}"
                        if entry.get("error_code")
                        else None
                    ),
                    now,
                    now,
                    entry["source_id"],
                    entry["url"],
                    entry["position"],
                    entry["title"],
                    entry["duration"],
                    entry["thumbnail_url"],
                    entry["availability"],
                ),
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

    projects_dir = facade_attr("PROJECTS_DIR", PROJECTS_DIR)
    for project_id in project_dirs:
        (Path(projects_dir) / project_id).mkdir(parents=True, exist_ok=True)
    facade_attr("_dispatch_batch", _dispatch_batch)(batch_id)
    _refresh_batch(batch_id)
    return {
        "action": "created" if created else "synced",
        "batch_id": batch_id,
        "added_count": len(new_items),
        "existing_count": max(0, len(preview["items"]) - len(new_items)),
        "batch": get_batch_detail(batch_id)["batch"],
    }
