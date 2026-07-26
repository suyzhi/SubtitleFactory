"""
字幕工厂 - 任务状态 API
"""

import logging
import json

from fastapi import APIRouter, HTTPException

from ..utils.task_manager import task_manager
from ..models.database import get_db
from ..services.subtitle_cleaner import retry_clean_batch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _task_dict(row) -> dict:
    task = dict(row)
    for key, fallback in (("details", {}), ("logs", []), ("available_actions", [])):
        try:
            task[key] = json.loads(task.get(key) or json.dumps(fallback))
        except json.JSONDecodeError:
            task[key] = fallback
    task["recoverable"] = bool(task.get("recoverable"))
    return task


@router.get("/tasks")
def list_tasks(status: str = "", limit: int = 100):
    """Global task drawer source, including tasks restored after restart."""
    db = get_db()
    try:
        if status:
            rows = db.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY priority DESC,updated_at DESC LIMIT ?",
                (status, max(1, min(limit, 500))),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM tasks ORDER BY priority DESC,updated_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return {"tasks": [_task_dict(row) for row in rows]}
    finally:
        db.close()


@router.get("/projects/{project_id}/tasks/latest")
def get_latest_project_task(project_id: str):
    db = get_db()
    row = db.execute(
        "SELECT * FROM tasks WHERE project_id=? ORDER BY updated_at DESC LIMIT 1", (project_id,)
    ).fetchone()
    db.close()
    if not row:
        return {"task": None}
    return {"task": _task_dict(row)}


@router.get("/tasks/{task_id}")
def get_task_status(task_id: str):
    """查询后台任务状态"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.post("/tasks/{task_id}/pause")
def pause_task(task_id: str):
    """在安全检查点暂停后台任务。"""
    if not task_manager.get_task(task_id):
        raise HTTPException(404, "任务不存在")
    if not task_manager.pause_task(task_id):
        raise HTTPException(409, "当前任务状态无法暂停")
    return task_manager.get_task(task_id)


@router.post("/tasks/{task_id}/resume")
def resume_task(task_id: str):
    """继续已暂停的后台任务。"""
    if not task_manager.get_task(task_id):
        raise HTTPException(404, "任务不存在")
    if not task_manager.resume_task(task_id):
        raise HTTPException(409, "当前任务未处于暂停状态")
    return task_manager.get_task(task_id)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    """终止待执行、运行中或暂停中的后台任务。"""
    if not task_manager.get_task(task_id):
        raise HTTPException(404, "任务不存在")
    if not task_manager.cancel_task(task_id):
        raise HTTPException(409, "当前任务状态无法终止")
    return task_manager.get_task(task_id)


@router.post("/tasks/{task_id}/retry-failed-batches")
def retry_failed_batches(task_id: str):
    original = task_manager.get_task(task_id)
    if not original:
        raise HTTPException(404, "任务不存在")
    failed = _failed_batch_rows(task_id)
    if not failed:
        raise HTTPException(409, "没有可重试的失败批次")
    if len(failed) != 1:
        raise HTTPException(409, "存在多个失败批次，请选择要重试的具体批次")
    return _start_batch_retry(original, int(failed[0]["batch_index"]))


@router.get("/tasks/{task_id}/failed-batches")
def list_failed_batches(task_id: str):
    original = task_manager.get_task(task_id)
    if not original:
        raise HTTPException(404, "任务不存在")
    if original.get("type") != "clean":
        raise HTTPException(400, "当前任务不是字幕整理任务")
    return {"task_id": task_id, "batches": _failed_batch_rows(task_id)}


@router.post("/tasks/{task_id}/retry-failed-batches/{batch_index}")
def retry_failed_batch(task_id: str, batch_index: int):
    original = task_manager.get_task(task_id)
    if not original:
        raise HTTPException(404, "任务不存在")
    if not any(int(row["batch_index"]) == batch_index for row in _failed_batch_rows(task_id)):
        raise HTTPException(409, "这个批次不存在，或已经重试成功")
    return _start_batch_retry(original, batch_index)


def _failed_batch_rows(task_id: str) -> list[dict]:
    db = get_db()
    try:
        rows = db.execute(
            """SELECT batch_index,input_fingerprint,attempts,error,updated_at
               FROM ai_batch_results WHERE task_id=? AND operation='clean' AND status='failed'
               ORDER BY batch_index""",
            (task_id,),
        ).fetchall()
    finally:
        db.close()
    result = []
    for row in rows:
        try:
            segments = json.loads(row["input_fingerprint"] or "{}").get("segments") or []
        except (TypeError, ValueError, json.JSONDecodeError):
            segments = []
        result.append({
            "batch_index": int(row["batch_index"]),
            "segment_count": len(segments),
            "start": float(segments[0][2]) if segments else None,
            "end": float(segments[-1][3]) if segments else None,
            "attempts": int(row["attempts"] or 0),
            "error": row["error"] or "",
            "updated_at": row["updated_at"],
        })
    return result


def _start_batch_retry(original: dict, batch_index: int) -> dict:
    if original.get("type") != "clean":
        raise HTTPException(400, "当前仅支持重试字幕整理批次")
    project_id = original.get("project_id")
    active = task_manager.active_task_ids(project_id)
    if active:
        raise HTTPException(409, "当前项目还有任务正在运行，请完成后再重试")
    new_id = task_manager.create_task(project_id, "clean", max_attempts=1)
    task_manager.update_task(
        new_id, parent_task_id=original["id"],
        details={"retry_of": original["id"], "batch_index": batch_index, "single_batch_retry": True},
    )
    task_manager.run_background(new_id, retry_clean_batch, original["id"], batch_index)
    return {"task_id": new_id, "retry_of": original["id"], "batch_index": batch_index}
