"""
字幕工厂（简洁版）- 任务状态查询与取消 API
"""

import json
import logging

from fastapi import APIRouter, HTTPException

from ..models.database import get_db
from ..utils.task_manager import task_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _task_dict(row) -> dict:
    task = dict(row)
    for key, fallback in (("details", {}), ("logs", []), ("available_actions", [])):
        value = task.get(key)
        if isinstance(value, type(fallback)):
            task[key] = value
        else:
            try:
                task[key] = json.loads(value or json.dumps(fallback))
            except (TypeError, json.JSONDecodeError):
                task[key] = fallback
    task["recoverable"] = bool(task.get("recoverable"))
    task["suggestion"] = (
        task.get("suggestion")
        or (task.get("details") or {}).get("failure_suggestion")
        or ""
    )
    return task


@router.get("/projects/{project_id}/tasks/latest")
def get_latest_project_task(project_id: str):
    db = get_db()
    row = db.execute(
        "SELECT * FROM tasks WHERE project_id=? ORDER BY updated_at DESC,rowid DESC LIMIT 1",
        (project_id,),
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
    return _task_dict(task)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    """终止待执行或运行中的后台任务。"""
    if not task_manager.get_task(task_id):
        raise HTTPException(404, "任务不存在")
    if not task_manager.cancel_task(task_id):
        raise HTTPException(409, "当前任务状态无法终止")
    return _task_dict(task_manager.get_task(task_id))
