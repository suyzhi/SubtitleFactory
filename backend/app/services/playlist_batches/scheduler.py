"""Stage queuing, resource-aware batch dispatch, execution, and status rollup."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from ...models.database import get_db
from ...utils.task_manager import TaskCancelled, task_manager
from ..app_settings import get_effective_app_settings as get_app_settings
from ..audio_extractor import extract_audio
from ..subtitle_cleaner import clean_subtitles
from ..subtitle_translator import translate_subtitles
from ..transcriber import transcribe_audio
from .common import (
    STAGE_ORDER,
    PlaylistBatchError,
    _now,
    download_video,
    facade_attr,
    remove_managed_download_file,
)


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
        if not context or context["paused"] or context["status"] not in {
            "waiting",
            "failed",
            "partial",
            "blocked",
            "cancelled",
        }:
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
        db.execute(
            "UPDATE batch_items SET status='running',updated_at=? WHERE id=?", (_now(), item_id)
        )
        db.execute(
            "UPDATE batches SET status='running',updated_at=? WHERE id=?",
            (_now(), context["batch_id"]),
        )
        db.commit()
    finally:
        db.close()
    task_id = task_manager.create_task(
        context["project_id"],
        stage,
        max_attempts=3 if stage == "download" else None,
    )
    task_manager.update_task(
        task_id,
        details={
            "batch_id": context["batch_id"],
            "batch_item_id": item_id,
            "batch_title": context["batch_title"],
            "batch_item_title": context["item_title"],
            "batch_position": context["position"],
            "batch_stage": stage,
        },
    )
    db = get_db()
    try:
        db.execute(
            "UPDATE batch_item_stages SET task_id=?,updated_at=? WHERE item_id=? AND stage=?",
            (task_id, _now(), item_id, stage),
        )
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
               WHERE i.id=?""",
            (item_id,),
        ).fetchone()
        if not item or item["paused"] or item["source_state"] != "active":
            return None
        stages = db.execute(
            "SELECT * FROM batch_item_stages WHERE item_id=? ORDER BY CASE stage "
            "WHEN 'download' THEN 1 WHEN 'extract_audio' THEN 2 WHEN 'transcribe' THEN 3 "
            "WHEN 'clean' THEN 4 ELSE 5 END",
            (item_id,),
        ).fetchall()
    finally:
        db.close()
    queue_fn = facade_attr("_queue_stage", _queue_stage)
    for stage in stages:
        if stage["status"] in {"queued", "running", "paused", "failed", "partial", "cancelled"}:
            return None
        if stage["status"] in {"waiting", "blocked"}:
            return queue_fn(item_id, stage["stage"])
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
               AND s.status IN ('queued','running','paused') GROUP BY s.stage""",
            (batch_id,),
        ).fetchall()
        items = [
            row[0]
            for row in db.execute(
                "SELECT id FROM batch_items WHERE batch_id=? AND source_state='active' ORDER BY position,created_at",
                (batch_id,),
            ).fetchall()
        ]
    finally:
        db.close()
    counts = {row["stage"]: int(row["count"]) for row in active_rows}
    resource_counts = {
        "io": counts.get("download", 0) + counts.get("extract_audio", 0),
        "ml": counts.get("transcribe", 0),
        "network_ai": counts.get("clean", 0) + counts.get("translate", 0),
    }
    limits = {"io": 2, "ml": 1, "network_ai": 2}
    resources = {
        "download": "io",
        "extract_audio": "io",
        "transcribe": "ml",
        "clean": "network_ai",
        "translate": "network_ai",
    }
    queue_fn = facade_attr("_queue_stage", _queue_stage)
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
        if queue_fn(item_id, candidate):
            resource_counts[resource] += 1


def _dispatch_when_task_terminal(task_id: str, batch_id: str) -> None:
    def watch() -> None:
        for _ in range(240):
            task = task_manager.get_task(task_id) or {}
            if task.get("status") in {"failed", "cancelled"}:
                facade_attr("_dispatch_batch", _dispatch_batch)(batch_id)
                return
            if task.get("status") == "success":
                return
            time.sleep(0.25)

    threading.Thread(
        target=watch, name=f"playlist-dispatch-{task_id[:8]}", daemon=True
    ).start()


def _mark_later_stages(item_id: str, current_stage: str, status: str) -> None:
    current_index = STAGE_ORDER.index(current_stage)
    later = STAGE_ORDER[current_index + 1 :]
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
    project_id: str,
    item_id: str,
    stage: str,
    current_task_id: str,
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
    configuration = json.loads(
        context["configuration_json"] or context["batch_configuration"] or "{}"
    )
    db = get_db()
    try:
        db.execute(
            "UPDATE batch_item_stages SET status='running',updated_at=? WHERE item_id=? AND stage=?",
            (_now(), item_id, stage),
        )
        current_index = STAGE_ORDER.index(stage)
        later = STAGE_ORDER[current_index + 1 :]
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
                context["project_id"],
                item_id,
                stage,
                task_id,
            )
            facade_attr("_dispatch_batch", _dispatch_batch)(context["batch_id"])
    except TaskCancelled:
        db = get_db()
        try:
            db.execute(
                "UPDATE batch_item_stages SET status='cancelled',updated_at=? WHERE item_id=? AND stage=?",
                (_now(), item_id, stage),
            )
            db.commit()
        finally:
            db.close()
        _mark_later_stages(item_id, stage, "cancelled")
        _refresh_item(item_id)
        _dispatch_when_task_terminal(task_id, context["batch_id"])
        raise
    except Exception as exc:
        suggestion = str(getattr(exc, "suggestion", "") or "")
        display_error = str(exc)[:500]
        if suggestion:
            display_error = f"{display_error}｜建议：{suggestion}"[:500]
        db = get_db()
        try:
            db.execute(
                "UPDATE batch_item_stages SET status='failed',error_code=?,error=?,updated_at=? WHERE item_id=? AND stage=?",
                (
                    str(getattr(exc, "error_code", "UNEXPECTED_ERROR")),
                    display_error,
                    _now(),
                    item_id,
                    stage,
                ),
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
        raise PlaylistBatchError(
            "不可用视频没有项目", "PLAYLIST_ITEM_UNAVAILABLE", recoverable=False
        )
    db = get_db()
    try:
        project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    finally:
        db.close()
    if not project:
        raise PlaylistBatchError("关联项目不存在", "BATCH_PROJECT_MISSING", recoverable=False)
    if stage == "download":
        settings = facade_attr("get_app_settings", get_app_settings)()
        video_path = facade_attr("download_video", download_video)(
            task_id,
            context["source_url"],
            project_id,
            ffmpeg_path=settings.get("ffmpeg_path"),
            download_dir=settings.get("download_directory"),
            quality=configuration.get("download_quality")
            or settings.get("download_quality")
            or "best",
            container=configuration.get("download_container")
            or settings.get("download_container")
            or "mp4",
        )
        details = (task_manager.get_task(task_id) or {}).get("details", {})
        candidate_thumbnail = details.get("thumbnail_path")
        committed = False
        remove_fn = facade_attr("remove_managed_download_file", remove_managed_download_file)
        db = get_db()
        try:
            db.execute(
                """UPDATE projects SET video_path=?,title=?,thumbnail_url=?,thumbnail_path=?,updated_at=? WHERE id=?""",
                (
                    video_path,
                    details.get("title") or context["item_title"],
                    details.get("thumbnail_url"),
                    details.get("thumbnail_path"),
                    _now(),
                    project_id,
                ),
            )
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
                    download_dir=settings.get("download_directory"),
                )
                remove_fn(
                    candidate_thumbnail,
                    project_id=project_id,
                    download_dir=settings.get("download_directory"),
                )
        if project["video_path"] and project["video_path"] != video_path:
            remove_fn(
                project["video_path"],
                project_id=project_id,
                download_dir=settings.get("download_directory"),
            )
        if project["thumbnail_path"] and project["thumbnail_path"] != candidate_thumbnail:
            remove_fn(
                project["thumbnail_path"],
                project_id=project_id,
                download_dir=settings.get("download_directory"),
            )
        return
    if stage == "extract_audio":
        if not project["video_path"] or not os.path.isfile(project["video_path"]):
            raise PlaylistBatchError("视频文件不存在", "VIDEO_MISSING")
        audio_path = extract_audio(
            task_id,
            project["video_path"],
            project_id,
            int(project["audio_track_index"] or 0),
            project["range_start"],
            project["range_end"],
        )
        db = get_db()
        try:
            db.execute(
                "UPDATE projects SET audio_path=?,updated_at=? WHERE id=?",
                (audio_path, _now(), project_id),
            )
            db.commit()
        finally:
            db.close()
    elif stage == "transcribe":
        if not project["audio_path"] or not os.path.isfile(project["audio_path"]):
            raise PlaylistBatchError("音频文件不存在", "AUDIO_MISSING")
        transcribe_audio(
            task_id,
            project["audio_path"],
            project_id,
            configuration.get("language", "auto"),
            configuration.get("model", "small"),
            configuration.get("runtime") or None,
        )
    elif stage == "clean":
        clean_subtitles(
            task_id,
            project_id,
            int(configuration.get("clean_target_length") or 42),
            configuration.get("clean_provider_id"),
            configuration.get("clean_model"),
        )
    elif stage == "translate":
        target = configuration.get("target_language") or "zh"
        if target == "none":
            raise PlaylistBatchError(
                "批量翻译需要目标语言", "TARGET_LANGUAGE_REQUIRED", recoverable=False
            )
        translate_subtitles(
            task_id,
            project_id,
            target,
            configuration.get("translate_provider_id"),
            configuration.get("translate_model"),
        )


def _refresh_item(item_id: str) -> None:
    db = get_db()
    try:
        item = db.execute("SELECT * FROM batch_items WHERE id=?", (item_id,)).fetchone()
        if not item:
            return
        if item["source_state"] != "active":
            status = "unavailable"
        else:
            states = [
                row[0]
                for row in db.execute(
                    "SELECT status FROM batch_item_stages WHERE item_id=?", (item_id,)
                ).fetchall()
            ]
            if any(value in {"running", "queued"} for value in states):
                status = "running"
            elif any(value == "paused" for value in states):
                status = "paused"
            elif any(value == "partial" for value in states):
                status = "partial"
            elif any(value == "failed" for value in states):
                status = "failed"
            elif any(value == "cancelled" for value in states):
                status = "cancelled"
            elif all(value in {"success", "skipped"} for value in states):
                status = "success"
            else:
                status = "pending"
        db.execute(
            "UPDATE batch_items SET status=?,updated_at=? WHERE id=?", (status, _now(), item_id)
        )
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
        states = [
            row[0]
            for row in db.execute(
                "SELECT status FROM batch_items WHERE batch_id=? AND source_state<>'removed'",
                (batch_id,),
            ).fetchall()
        ]
        if batch["paused"]:
            status = "paused"
        elif any(value in {"running", "pending"} for value in states):
            status = "running"
        elif states and all(value == "success" for value in states):
            status = "success"
        elif states and all(value in {"failed", "unavailable"} for value in states):
            status = "failed"
        elif any(value in {"failed", "partial", "unavailable", "cancelled"} for value in states):
            status = "partial"
        else:
            status = "pending"
        db.execute("UPDATE batches SET status=?,updated_at=? WHERE id=?", (status, _now(), batch_id))
        db.commit()
    finally:
        db.close()
