"""
字幕工厂（简洁版）- 后台任务管理器

管理所有异步任务的创建、状态更新、取消与资源调度。
"""

import json
import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class TaskCancelled(Exception):
    """Raised inside a worker when cooperative cancellation is requested."""


class TaskManager:
    """全局任务管理器，管理所有后台任务的生命周期"""

    def __init__(self, max_workers: int = 6):
        self._tasks: dict = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.RLock()
        self._futures: dict[str, Future] = {}
        self._held_resources = threading.local()
        self._resource_limits = {
            "workflow": threading.Semaphore(2),
            "ml": threading.Semaphore(1),
            "ffmpeg": threading.Semaphore(1),
            "io": threading.Semaphore(2),
            "network_ai": threading.Semaphore(2),
        }

    def create_task(
        self,
        project_id: Optional[str],
        task_type: str,
        *,
        priority: int = 0,
        resource_class: str | None = None,
        max_attempts: int | None = None,
    ) -> str:
        """创建新任务，返回 task_id"""
        task_id = str(uuid.uuid4())
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        inferred_resource = resource_class or {
            "transcribe": "ml",
            "workflow": "workflow",
            "download": "io",
            "extract_audio": "ffmpeg",
            "clean": "network_ai",
            "translate": "network_ai",
        }.get(task_type, "io")
        task: dict = {
            "id": task_id,
            "project_id": project_id,
            "type": task_type,
            "status": "pending",
            "step": "",
            "progress": 0.0,
            "message": "",
            "error": None,
            "created_at": now,
            "updated_at": now,
            "details": {},
            "logs": [],
            "error_code": None,
            "recoverable": False,
            "available_actions": [],
            "parent_task_id": None,
            "attempt": 1,
            "priority": priority,
            "resource_class": inferred_resource,
            "max_attempts": max_attempts or (3 if inferred_resource in {"io", "network_ai"} else 1),
            "next_retry_at": None,
        }
        with self._lock:
            self._tasks[task_id] = task
            self._persist(task)
        logger.info(f"[TaskManager] 创建任务: {task_id} ({task_type})")
        return task_id

    def update_task(self, task_id: str, **kwargs):
        """更新任务状态（线程安全）"""
        with self._lock:
            if task_id in self._tasks:
                if self._tasks[task_id]["status"] == "cancelled":
                    return

                details = kwargs.pop("details", None)
                logs = kwargs.pop("logs", None)

                if details is not None and isinstance(details, dict):
                    self._tasks[task_id]["details"].update(details)

                if logs is not None and isinstance(logs, list):
                    self._tasks[task_id]["logs"].extend(logs)

                self._tasks[task_id].update(kwargs)
                self._tasks[task_id]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._persist(self._tasks[task_id])

    def add_log(
        self,
        task_id: str,
        level: str = "info",
        step: str = "",
        message: str = "",
        detail: str = "",
        suggestion: str = "",
    ):
        """添加日志条目到 task.logs"""
        log_entry = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "step": step,
            "message": message,
            "detail": detail,
            "suggestion": suggestion,
        }
        with self._lock:
            if task_id in self._tasks:
                if self._tasks[task_id]["status"] == "cancelled":
                    return
                self._tasks[task_id]["logs"].append(log_entry)
                self._tasks[task_id]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._persist(self._tasks[task_id])
        logger.info(f"[TaskManager] [{level.upper()}] {step}: {message}")

    def get_task(self, task_id: str) -> Optional[dict]:
        """获取任务状态"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                return dict(task)
        restored = self._load(task_id)
        if restored is None:
            return None
        with self._lock:
            task = self._tasks.setdefault(task_id, restored)
            return dict(task)

    def active_task_ids(self, project_id: str) -> list[str]:
        """Return pending/running task IDs for a project."""
        active_statuses = {"pending", "running", "paused"}
        with self._lock:
            task_ids = {
                task_id
                for task_id, task in self._tasks.items()
                if task.get("project_id") == project_id and task.get("status") in active_statuses
            }
            task_ids.update(
                task_id
                for task_id, future in self._futures.items()
                if not future.done() and self._tasks.get(task_id, {}).get("project_id") == project_id
            )
        try:
            from ..models.database import get_db

            db = get_db()
            rows = db.execute(
                """SELECT id FROM tasks
                   WHERE project_id=? AND status IN ('pending','running','paused')""",
                (project_id,),
            ).fetchall()
            db.close()
            task_ids.update(row["id"] for row in rows)
        except Exception:
            logger.debug("Unable to inspect persisted project tasks", exc_info=True)
        return sorted(task_ids)

    @staticmethod
    def _persist(task: dict) -> None:
        try:
            from ..models.database import get_db

            db = get_db(timeout=0.25)
            db.execute(
                """INSERT OR REPLACE INTO tasks
                   (id,project_id,type,status,step,progress,message,error,error_code,
                    recoverable,available_actions,parent_task_id,attempt,details,logs,
                    created_at,updated_at,priority,resource_class,max_attempts,next_retry_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task["id"],
                    task.get("project_id"),
                    task["type"],
                    task["status"],
                    task.get("step", ""),
                    task.get("progress", 0),
                    task.get("message", ""),
                    task.get("error"),
                    task.get("error_code"),
                    int(bool(task.get("recoverable"))),
                    json.dumps(task.get("available_actions", []), ensure_ascii=False),
                    task.get("parent_task_id"),
                    task.get("attempt", 1),
                    json.dumps(task.get("details", {}), ensure_ascii=False),
                    json.dumps(task.get("logs", []), ensure_ascii=False),
                    task["created_at"],
                    task["updated_at"],
                    task.get("priority", 0),
                    task.get("resource_class", "io"),
                    task.get("max_attempts", 1),
                    task.get("next_retry_at"),
                ),
            )
            db.commit()
            db.close()
        except Exception:
            logger.debug("Task persistence unavailable", exc_info=True)

    @staticmethod
    def _load(task_id: str) -> Optional[dict]:
        try:
            from ..models.database import get_db

            db = get_db()
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            db.close()
            if not row:
                return None
            result = dict(row)
            result["details"] = json.loads(result.get("details") or "{}")
            result["logs"] = json.loads(result.get("logs") or "[]")
            result["available_actions"] = json.loads(result.get("available_actions") or "[]")
            result["recoverable"] = bool(result.get("recoverable"))
            return result
        except Exception:
            logger.debug("Task restore unavailable", exc_info=True)
            return None

    def cancel_task(self, task_id: str) -> bool:
        """Cooperatively terminate a pending or running task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task["status"] == "cancelled":
                return True
            if task["status"] not in ("pending", "running", "paused"):
                return False

            now = time.strftime("%Y-%m-%d %H:%M:%S")
            task["status"] = "cancelled"
            task["message"] = "任务已终止"
            task["details"]["stages"] = {
                key: "cancelled" if value == "running" else value
                for key, value in (task["details"].get("stages") or {}).items()
            }
            task["error"] = None
            task["updated_at"] = now
            task["logs"].append({
                "time": now,
                "level": "info",
                "step": task.get("step", ""),
                "message": "任务已终止",
                "detail": "已请求在安全检查点停止后台处理",
                "suggestion": "",
            })

            task["details"]["cancel_requested_at"] = time.time()
            future = self._futures.get(task_id)
            if future:
                future.cancel()
                if not future.done():
                    task["message"] = "已请求停止，正在等待当前处理片段结束"
                else:
                    task["details"]["worker_stopped"] = True
            self._persist(task)
        return True

    def is_cancelled(self, task_id: str) -> bool:
        """Return whether cancellation has been requested for a task."""
        with self._lock:
            return self._tasks.get(task_id, {}).get("status") == "cancelled"

    def checkpoint(self, task_id: str):
        """Raise when termination has been requested."""
        with self._lock:
            if self._tasks.get(task_id, {}).get("status") == "cancelled":
                raise TaskCancelled("任务已终止")

    def wait_if_paused(self, task_id: str):
        """Backward-compatible name for the shared cooperative checkpoint."""
        self.checkpoint(task_id)

    def _acquire_resource(self, task_id: str, resource: str):
        held = getattr(self._held_resources, "counts", None)
        if held is None:
            held = self._held_resources.counts = {}
        if not held.get(resource):
            limiter = self._resource_limits.get(resource, self._resource_limits["io"])
            while True:
                self.checkpoint(task_id)
                if limiter.acquire(timeout=0.1):
                    break
        held[resource] = held.get(resource, 0) + 1

    def _release_resource(self, resource: str):
        held = self._held_resources.counts
        held[resource] -= 1
        if held[resource] == 0:
            self._resource_limits.get(resource, self._resource_limits["io"]).release()
            del held[resource]

    @contextmanager
    def resource_slot(self, task_id: str, resource: str):
        """Acquire only the resource used by this stage; nested use is reentrant."""
        self._acquire_resource(task_id, resource)
        try:
            self.checkpoint(task_id)
            yield
        finally:
            self._release_resource(resource)

    def run_background(self, task_id: str, func: Callable, *args, **kwargs):
        """在线程池中执行后台任务"""

        def _wrapper():
            resource = self._tasks.get(task_id, {}).get("resource_class", "io")
            acquired = False
            try:
                self._acquire_resource(task_id, resource)
                acquired = True
                while True:
                    try:
                        self.checkpoint(task_id)
                        self.update_task(
                            task_id,
                            status="running",
                            progress=0.0,
                            message="准备开始...",
                            next_retry_at=None,
                        )
                        self.checkpoint(task_id)
                        logger.info(f"[TaskManager] 任务开始: {task_id}")
                        func(task_id, *args, **kwargs)
                        self.checkpoint(task_id)
                        current = self.get_task(task_id) or {}
                        if current.get("status") != "partial":
                            self.update_task(
                                task_id,
                                status="success",
                                progress=100.0,
                                message=current.get("message") or "完成",
                                error=None,
                                error_code=None,
                                recoverable=False,
                                available_actions=[],
                                next_retry_at=None,
                            )
                        else:
                            self.update_task(task_id, progress=100.0)
                        logger.info(f"[TaskManager] 任务完成: {task_id}")
                        break
                    except TaskCancelled:
                        logger.info(f"[TaskManager] 任务已终止: {task_id}")
                        break
                    except Exception as e:
                        error_code = str(getattr(e, "error_code", "UNEXPECTED_ERROR"))
                        recoverable = bool(getattr(e, "recoverable", False)) or (
                            (self.get_task(task_id) or {}).get("type") == "workflow"
                        )
                        automatic_retry = bool(getattr(e, "automatic_retry", False))
                        current = self.get_task(task_id) or {}
                        attempt = int(current.get("attempt", 1))
                        maximum = int(current.get("max_attempts", 1))
                        if automatic_retry and attempt < maximum:
                            delay = min(30, 2 ** (attempt - 1))
                            retry_at = time.strftime(
                                "%Y-%m-%d %H:%M:%S", time.localtime(time.time() + delay)
                            )
                            self.update_task(
                                task_id,
                                status="pending",
                                attempt=attempt + 1,
                                next_retry_at=retry_at,
                                message=f"将在 {delay} 秒后自动重试",
                                error=str(e),
                                error_code=error_code,
                            )
                            self.add_log(
                                task_id,
                                "warning",
                                "自动重试",
                                f"第 {attempt} 次尝试失败",
                                detail=str(e),
                                suggestion=f"{delay} 秒后重试",
                            )
                            deadline = time.monotonic() + delay
                            while time.monotonic() < deadline:
                                self.checkpoint(task_id)
                                time.sleep(min(0.25, deadline - time.monotonic()))
                            continue
                        logger.error(f"[TaskManager] 任务失败: {task_id} - {str(e)}", exc_info=True)
                        actions = list(
                            getattr(e, "available_actions", ["retry"] if recoverable else [])
                        )
                        suggestion = getattr(e, "suggestion", "请检查网络、模型和 AI 服务配置后重试")
                        failure_details: dict = {"failure_suggestion": suggestion}
                        if current.get("type") == "workflow":
                            failure_details["stages"] = {
                                key: "failed" if value == "running" else value
                                for key, value in (
                                    current.get("details", {}).get("stages") or {}
                                ).items()
                            }
                        exception_details = getattr(e, "details", None)
                        if isinstance(exception_details, dict) and exception_details:
                            download_details = dict(
                                (current.get("details") or {}).get("download") or {}
                            )
                            download_details.update(exception_details)
                            download_details["task_attempt"] = attempt
                            download_details["automatic_retry"] = automatic_retry
                            failure_details["download"] = download_details
                        self.update_task(
                            task_id,
                            status="failed",
                            error=str(e),
                            error_code=error_code,
                            recoverable=recoverable,
                            available_actions=actions,
                            message=str(e),
                            details=failure_details,
                        )
                        self.add_log(task_id, "error", "任务失败", str(e), suggestion=suggestion)
                        try:
                            from ..models.database import get_db

                            db = get_db()
                            db.execute(
                                """UPDATE transcription_runs SET status='failed', error_code=?,error_message=?,
                                   finished_at=datetime('now','localtime') WHERE task_id=? AND status='running'""",
                                (error_code, str(e), task_id),
                            )
                            db.commit()
                            db.close()
                        except Exception:
                            logger.debug("Unable to finalize failed transcription run", exc_info=True)
                        break
            finally:
                if acquired:
                    self._release_resource(resource)
                if self.is_cancelled(task_id):
                    try:
                        from ..models.database import get_db

                        db = get_db()
                        db.execute(
                            "UPDATE transcription_runs SET status='cancelled',finished_at=datetime('now','localtime') WHERE task_id=? AND status='running'",
                            (task_id,),
                        )
                        db.commit()
                        db.close()
                    except Exception:
                        logger.debug(
                            "Unable to finalize cancelled transcription run", exc_info=True
                        )
                    with self._lock:
                        task = self._tasks[task_id]
                        task["message"] = "任务已终止"
                        task["details"].update(
                            worker_stopped=True,
                            is_generating_segments=False,
                            is_postprocessing=False,
                            worker_stop_seconds=round(
                                time.time() - task["details"].get("cancel_requested_at", time.time()),
                                3,
                            ),
                        )
                        self._persist(task)

        future = self._executor.submit(_wrapper)
        with self._lock:
            self._futures[task_id] = future
            if self._tasks.get(task_id, {}).get("status") == "cancelled":
                future.cancel()

        def _forget_future(done_future: Future):
            with self._lock:
                if self._futures.get(task_id) is done_future:
                    self._futures.pop(task_id, None)

        future.add_done_callback(_forget_future)
        return future

    def shutdown(self, wait: bool = True):
        """Release worker threads (primarily useful for isolated test managers)."""
        self._executor.shutdown(wait=wait, cancel_futures=True)


# 全局单例
task_manager = TaskManager()
