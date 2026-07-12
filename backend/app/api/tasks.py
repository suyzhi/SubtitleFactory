"""
字幕工厂 - 任务状态 API
"""

import logging
import json

from fastapi import APIRouter, HTTPException

from ..utils.task_manager import task_manager
from ..models.database import get_db
from ..services.subtitle_cleaner import clean_subtitles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/projects/{project_id}/tasks/latest")
def get_latest_project_task(project_id: str):
    db = get_db()
    row = db.execute(
        "SELECT * FROM tasks WHERE project_id=? ORDER BY updated_at DESC LIMIT 1", (project_id,)
    ).fetchone()
    db.close()
    if not row:
        return {"task": None}
    task = dict(row)
    for key, fallback in (("details", {}), ("logs", []), ("available_actions", [])):
        try:
            task[key] = json.loads(task.get(key) or json.dumps(fallback))
        except json.JSONDecodeError:
            task[key] = fallback
    task["recoverable"] = bool(task.get("recoverable"))
    return {"task": task}


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
    if not original: raise HTTPException(404, "任务不存在")
    db=get_db(); failed=db.execute("SELECT COUNT(*) count FROM ai_batch_results WHERE task_id=? AND status='failed'",(task_id,)).fetchone()["count"]; db.close()
    if not failed: raise HTTPException(409, "没有可重试的失败批次")
    if original.get("type") != "clean": raise HTTPException(400, "当前仅支持重试整理批次")
    new_id=task_manager.create_task(original.get("project_id"), "clean")
    target=int((original.get("details") or {}).get("target_length",42))
    task_manager.update_task(new_id,parent_task_id=task_id,details={"retry_of":task_id})
    task_manager.run_background(new_id,clean_subtitles,original["project_id"],target)
    return {"task_id":new_id,"retry_of":task_id,"failed_batches":failed}
