"""
字幕工厂 - 任务状态 API
"""

import logging
import json

from fastapi import APIRouter, HTTPException

from ..utils.task_manager import task_manager
from ..models.database import get_db

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
