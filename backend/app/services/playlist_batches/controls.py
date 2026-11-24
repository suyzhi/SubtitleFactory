"""Query, pause/resume, cancellation, retry, stage enabling, and recovery for playlist batches."""

from __future__ import annotations

import json
from typing import Any

from ...models.database import get_db, project_to_dict
from ...utils.task_manager import task_manager
from .common import PLAYLIST_KIND, PlaylistBatchError, _now, facade_attr
from .preview import _validate_configuration, create_or_sync_playlist, preview_playlist
from .scheduler import _dispatch_batch, _refresh_item


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
               WHERE i.batch_id=? ORDER BY i.position,i.created_at""",
            (batch_id,),
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
            "status": row["status"],
            "task_id": row["task_id"],
            "attempt": row["attempt"],
            "error_code": row["error_code"],
            "error": row["error"],
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
                project = {
                    **project_to_dict(raw_project),
                    "segments_count": raw_project["segments_count"],
                }
        items.append(
            {
                "id": row["id"],
                "project_id": row["project_id"],
                "source_id": row["source_id"],
                "source_url": row["source_url"],
                "position": row["position"],
                "title": row["title"],
                "duration": row["duration"],
                "thumbnail_url": row["thumbnail_url"],
                "source_state": row["source_state"],
                "status": row["status"],
                "error": row["error"],
                "project": project,
                "stages": stage_map.get(row["id"], {}),
            }
        )
    active = [item for item in items if item["source_state"] != "removed"]
    selected_units = [
        stage for item in active for stage in item["stages"].values() if stage["status"] != "skipped"
    ]
    completed = sum(1 for item in active if item["status"] == "success")
    failed = sum(1 for item in active if item["status"] in {"failed", "partial", "unavailable"})
    progress = 0.0
    if selected_units:
        progress = sum(
            100
            if stage["status"] == "success"
            else stage["progress"]
            if stage["status"] in {"running", "queued"}
            else 0
            for stage in selected_units
        ) / len(selected_units)
    payload = dict(batch)
    payload["configuration"] = json.loads(payload.pop("configuration_json") or "{}")
    payload.update(
        {
            "item_count": len(active),
            "completed_count": completed,
            "failed_count": failed,
            "progress": round(progress, 1),
        }
    )
    return {"batch": payload, "items": items}


def list_playlist_batches() -> dict[str, Any]:
    db = get_db()
    try:
        ids = [
            row[0]
            for row in db.execute(
                "SELECT id FROM batches WHERE kind=? ORDER BY updated_at DESC", (PLAYLIST_KIND,)
            ).fetchall()
        ]
    finally:
        db.close()
    return {"batches": [get_batch_detail(batch_id) for batch_id in ids]}


def sync_playlist_batch(batch_id: str) -> dict[str, Any]:
    detail = get_batch_detail(batch_id)
    batch = detail["batch"]
    if batch.get("kind") != PLAYLIST_KIND or not batch.get("source_url"):
        raise PlaylistBatchError(
            "批次不是可同步的 YouTube 播放列表", "BATCH_NOT_PLAYLIST", recoverable=False
        )
    return create_or_sync_playlist(
        preview_playlist(batch["source_url"]), dict(batch.get("configuration") or {})
    )


def pause_batch(batch_id: str) -> dict[str, Any]:
    db = get_db()
    try:
        db.execute(
            "UPDATE batches SET paused=1,status='paused',updated_at=? WHERE id=?", (_now(), batch_id)
        )
        task_ids = [
            row[0]
            for row in db.execute(
                "SELECT task_id FROM batch_item_stages WHERE item_id IN (SELECT id FROM batch_items WHERE batch_id=?) AND status IN ('queued','running') AND task_id IS NOT NULL",
                (batch_id,),
            ).fetchall()
        ]
        db.execute(
            "UPDATE batch_item_stages SET status='paused',updated_at=? WHERE task_id IN (SELECT task_id FROM batch_item_stages WHERE item_id IN (SELECT id FROM batch_items WHERE batch_id=?) AND status IN ('queued','running'))",
            (_now(), batch_id),
        )
        db.commit()
    finally:
        db.close()
    for task_id in task_ids:
        task_manager.pause_task(task_id)
    return get_batch_detail(batch_id)


def resume_batch(batch_id: str) -> dict[str, Any]:
    db = get_db()
    try:
        db.execute(
            "UPDATE batches SET paused=0,status='running',updated_at=? WHERE id=?",
            (_now(), batch_id),
        )
        paused = db.execute(
            "SELECT item_id,stage,task_id FROM batch_item_stages WHERE item_id IN (SELECT id FROM batch_items WHERE batch_id=?) AND status='paused'",
            (batch_id,),
        ).fetchall()
        db.commit()
    finally:
        db.close()
    for row in paused:
        if row["task_id"] and task_manager.resume_task(row["task_id"]):
            db = get_db()
            try:
                db.execute(
                    "UPDATE batch_item_stages SET status='running',updated_at=? WHERE item_id=? AND stage=?",
                    (_now(), row["item_id"], row["stage"]),
                )
                db.commit()
            finally:
                db.close()
        else:
            db = get_db()
            try:
                db.execute(
                    """UPDATE batch_item_stages
                          SET status='waiting',task_id=NULL,error_code=NULL,error=NULL,updated_at=?
                        WHERE item_id=? AND stage=? AND status='paused'""",
                    (_now(), row["item_id"], row["stage"]),
                )
                db.commit()
            finally:
                db.close()
    facade_attr("_dispatch_batch", _dispatch_batch)(batch_id)
    return get_batch_detail(batch_id)


def cancel_pending_batch(batch_id: str) -> dict[str, Any]:
    db = get_db()
    try:
        task_ids = [
            row[0]
            for row in db.execute(
                "SELECT task_id FROM batch_item_stages WHERE item_id IN (SELECT id FROM batch_items WHERE batch_id=?) AND status IN ('queued','running','paused') AND task_id IS NOT NULL",
                (batch_id,),
            ).fetchall()
        ]
        db.execute(
            "UPDATE batches SET paused=1,status='cancelled',updated_at=? WHERE id=?",
            (_now(), batch_id),
        )
        db.execute(
            "UPDATE batch_item_stages SET status='cancelled',updated_at=? WHERE item_id IN (SELECT id FROM batch_items WHERE batch_id=?) AND status IN ('waiting','queued','running','paused','blocked')",
            (_now(), batch_id),
        )
        db.execute(
            "UPDATE batch_items SET status='cancelled',updated_at=? WHERE batch_id=? AND status IN ('pending','running','paused')",
            (_now(), batch_id),
        )
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
            item_sql += " AND id=?"
            params.append(item_id)
        item_ids = [row[0] for row in db.execute(item_sql, params).fetchall()]
        for current_item_id in item_ids:
            stages = db.execute(
                "SELECT stage,status FROM batch_item_stages WHERE item_id=? ORDER BY CASE stage WHEN 'download' THEN 1 WHEN 'extract_audio' THEN 2 WHEN 'transcribe' THEN 3 WHEN 'clean' THEN 4 ELSE 5 END",
                (current_item_id,),
            ).fetchall()
            failed_index = next(
                (
                    index
                    for index, row in enumerate(stages)
                    if row["status"] in {"failed", "partial", "cancelled"}
                ),
                None,
            )
            if failed_index is None:
                continue
            for row in stages[failed_index:]:
                if row["status"] != "skipped":
                    db.execute(
                        "UPDATE batch_item_stages SET status='waiting',task_id=NULL,error_code=NULL,error=NULL,updated_at=? WHERE item_id=? AND stage=?",
                        (_now(), current_item_id, row["stage"]),
                    )
            db.execute(
                "UPDATE batch_items SET status='pending',error=NULL,updated_at=? WHERE id=?",
                (_now(), current_item_id),
            )
        db.execute(
            "UPDATE batches SET paused=0,status='running',updated_at=? WHERE id=?",
            (_now(), batch_id),
        )
        db.commit()
    finally:
        db.close()
    facade_attr("_dispatch_batch", _dispatch_batch)(batch_id)
    return get_batch_detail(batch_id)


def enable_batch_stage(
    batch_id: str, stage: str, configuration: dict[str, Any]
) -> dict[str, Any]:
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
        if stage in {"clean", "translate"}:
            flags[stage] = True
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
        db.execute(
            "UPDATE batches SET configuration_json=?,paused=0,status='running',updated_at=? WHERE id=?",
            (snapshot, _now(), batch_id),
        )
        db.commit()
    finally:
        db.close()
    facade_attr("_dispatch_batch", _dispatch_batch)(batch_id)
    return get_batch_detail(batch_id)


def recover_playlist_batches(interrupted_tasks: list[dict[str, Any]]) -> None:
    """Reconcile playlist stages whose in-process workers disappeared."""
    original_status = {
        str(task.get("id")): str(task.get("status") or "running")
        for task in interrupted_tasks
        if task.get("id")
    }
    db = get_db()
    try:
        active = db.execute(
            """SELECT s.item_id,s.stage,s.status,s.task_id,i.batch_id
                 FROM batch_item_stages s
                 JOIN batch_items i ON i.id=s.item_id
                WHERE s.status IN ('queued','running','paused')"""
        ).fetchall()
        if not active:
            return
        now = _now()
        affected_items: set[str] = set()
        paused_batches: set[str] = set()
        for row in active:
            was_paused = (
                row["status"] == "paused"
                or original_status.get(str(row["task_id"] or "")) == "paused"
            )
            next_status = "paused" if was_paused else "failed"
            error = (
                "应用退出前已暂停；点击继续将从该阶段重新开始"
                if was_paused
                else "应用在任务执行期间退出"
            )
            db.execute(
                """UPDATE batch_item_stages
                      SET status=?,error_code='APP_INTERRUPTED',error=?,updated_at=?
                    WHERE item_id=? AND stage=?""",
                (next_status, error, now, row["item_id"], row["stage"]),
            )
            affected_items.add(row["item_id"])
            if was_paused:
                paused_batches.add(row["batch_id"])
        for batch_id in paused_batches:
            db.execute(
                "UPDATE batches SET paused=1,status='paused',updated_at=? WHERE id=?",
                (now, batch_id),
            )
        db.commit()
    finally:
        db.close()
    for item_id in affected_items:
        _refresh_item(item_id)
