"""
字幕工厂 - 后台任务管理器

管理所有异步任务的创建、状态更新和查询。
使用线程池执行后台任务，保证 API 不阻塞。
"""

import uuid
import time
import json
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class TaskCancelled(Exception):
    """Raised inside a worker when cooperative cancellation is requested."""


class TaskCreationBlocked(RuntimeError):
    """Raised while an exclusive maintenance boundary blocks new work."""

    error_code = "TASK_CREATION_BLOCKED"
    recoverable = True


class TaskManager:
    """全局任务管理器，管理所有后台任务的生命周期"""

    def __init__(self, max_workers: int = 6):
        self._tasks: dict = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.RLock()
        self._pause_conditions: dict[str, threading.Condition] = {}
        self._futures: dict[str, Future] = {}
        self._maintenance_reason: str | None = None
        self._active_api_mutations = 0
        self._resource_limits = {
            "ml": threading.Semaphore(1), "ffmpeg": threading.Semaphore(1),
            "io": threading.Semaphore(2), "network_ai": threading.Semaphore(2),
        }

    def create_task(
        self, project_id: Optional[str], task_type: str, *, priority: int = 0,
        resource_class: str | None = None, max_attempts: int | None = None,
    ) -> str:
        """创建新任务，返回 task_id"""
        task_id = str(uuid.uuid4())
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        inferred_resource = resource_class or {
            "transcribe": "ml", "workflow": "ml", "render": "ffmpeg",
            "download": "io", "extract_audio": "io", "clean": "network_ai", "translate": "network_ai",
        }.get(task_type, "io")
        task = {
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
            "details": {},      # 丰富的执行细节
            "logs": [],         # 日志列表
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
            if self._maintenance_reason:
                raise TaskCreationBlocked(
                    "数据库恢复已开始，暂时不能创建新任务；App 将在安全重启后恢复工作"
                )
            self._tasks[task_id] = task
            self._pause_conditions[task_id] = threading.Condition(self._lock)
            self._persist(task)
        logger.info(f"[TaskManager] 创建任务: {task_id} ({task_type})")
        return task_id

    def update_task(self, task_id: str, **kwargs):
        """更新任务状态（线程安全）

        支持传入 details dict 和 logs list：
        - details: 会被合并到现有 details 中
        - logs: 会被合并到现有 logs 中
        """
        with self._lock:
            if task_id in self._tasks:
                # Cancellation is terminal. A worker may still be returning from
                # a blocking library call, so never let a late progress/success
                # update revive a cancelled task.
                if self._tasks[task_id]["status"] == "cancelled":
                    return

                # 分离特殊字段
                details = kwargs.pop("details", None)
                logs = kwargs.pop("logs", None)

                # 合并 details
                if details is not None and isinstance(details, dict):
                    self._tasks[task_id]["details"].update(details)

                # 合并 logs
                if logs is not None and isinstance(logs, list):
                    self._tasks[task_id]["logs"].extend(logs)

                # 更新其他字段
                self._tasks[task_id].update(kwargs)
                self._tasks[task_id]["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._persist(self._tasks[task_id])

    def add_log(self, task_id: str, level: str = "info",
                step: str = "", message: str = "",
                detail: str = "", suggestion: str = ""):
        """添加日志条目到 task.logs

        Args:
            task_id: 任务 ID
            level: 日志级别 (info, warning, error, debug)
            step: 当前执行步骤名称
            message: 日志消息
            detail: 详细描述
            suggestion: 建议或解决方案
        """
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
        """获取任务状态（包含 details 和 logs 字段）"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                return dict(task)
        # Disk restore can wait for another SQLite writer.  Never keep the
        # in-memory task lock held while doing that I/O, otherwise every task
        # update and the whole task-center API can appear frozen.
        restored = self._load(task_id)
        if restored is None:
            return None
        with self._lock:
            task = self._tasks.setdefault(task_id, restored)
            self._pause_conditions.setdefault(task_id, threading.Condition(self._lock))
            return dict(task)

    def active_task_ids(self, project_id: str) -> list[str]:
        """Return pending/running/paused task IDs for a project.

        Querying both memory and SQLite keeps project deletion safe after an API
        worker has been restored lazily or when a task is queued in the current
        process but has not been read through ``get_task`` yet.
        """
        active_statuses = {"pending", "running", "paused"}
        with self._lock:
            task_ids = {
                task_id for task_id, task in self._tasks.items()
                if task.get("project_id") == project_id
                and task.get("status") in active_statuses
            }
            # A cooperatively cancelled worker can still be unwinding from a
            # blocking native process. Keep permanent deletion blocked until
            # its Future is actually finished, even though its public task
            # status is already the terminal ``cancelled`` state.
            task_ids.update(
                task_id for task_id, future in self._futures.items()
                if not future.done()
                and self._tasks.get(task_id, {}).get("project_id") == project_id
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

    def active_tasks(self) -> list[dict]:
        """Return every task that makes a database restore unsafe.

        The in-memory Future is authoritative while a worker is unwinding;
        SQLite persistence is best-effort and can briefly lag under write
        contention, so restore callers must inspect both sources.
        """
        with self._lock:
            return self._active_tasks_locked()

    def _active_tasks_locked(self) -> list[dict]:
        active_statuses = {"pending", "running", "paused"}
        task_ids = {
            task_id for task_id, task in self._tasks.items()
            if task.get("status") in active_statuses
        }
        task_ids.update(
            task_id for task_id, future in self._futures.items()
            if not future.done()
        )
        return [
            {
                "id": task_id,
                "type": str(self._tasks.get(task_id, {}).get("type") or "unknown"),
                "status": str(self._tasks.get(task_id, {}).get("status") or "running"),
            }
            for task_id in sorted(task_ids)
        ]

    def begin_exclusive_maintenance(self, reason: str) -> tuple[bool, list[dict]]:
        """Atomically stop new task creation when no in-memory work is active."""
        with self._lock:
            active = self._active_tasks_locked()
            if self._maintenance_reason:
                return False, [{
                    "id": "maintenance",
                    "type": self._maintenance_reason,
                    "status": "running",
                }]
            if self._active_api_mutations:
                return False, [{
                    "id": "request",
                    "type": "database_write",
                    "status": "running",
                }]
            if active:
                return False, active
            self._maintenance_reason = reason
            return True, []

    def end_exclusive_maintenance(self, reason: str) -> None:
        with self._lock:
            if self._maintenance_reason == reason:
                self._maintenance_reason = None

    def begin_api_mutation(self) -> bool:
        """Enter a mutating API request unless maintenance has closed the gate."""
        with self._lock:
            if self._maintenance_reason:
                return False
            self._active_api_mutations += 1
            return True

    def end_api_mutation(self) -> None:
        with self._lock:
            self._active_api_mutations = max(0, self._active_api_mutations - 1)

    def cancel_project_tasks(self, project_id: str) -> list[str]:
        """Request cooperative cancellation for every active project task."""
        cancelled = []
        for task_id in self.active_task_ids(project_id):
            # Loading a persisted task first lets cancel_task update its terminal
            # state using exactly the same path as an in-memory worker.
            if self.get_task(task_id) and self.cancel_task(task_id):
                cancelled.append(task_id)
        return cancelled

    @staticmethod
    def _persist(task: dict) -> None:
        """Persist task state without making the task manager depend on DB import order."""
        try:
            from ..models.database import get_db
            # The authoritative live state is in memory.  Do not hold
            # TaskManager's lock for the full stage-write timeout; a later
            # progress update persists the complete snapshot after contention.
            db = get_db(timeout=.25)
            db.execute(
                """INSERT OR REPLACE INTO tasks
                   (id,project_id,type,status,step,progress,message,error,error_code,
                    recoverable,available_actions,parent_task_id,attempt,details,logs,
                    created_at,updated_at,priority,resource_class,max_attempts,next_retry_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task["id"], task.get("project_id"), task["type"], task["status"],
                    task.get("step", ""), task.get("progress", 0), task.get("message", ""),
                    task.get("error"), task.get("error_code"), int(bool(task.get("recoverable"))),
                    json.dumps(task.get("available_actions", []), ensure_ascii=False),
                    task.get("parent_task_id"), task.get("attempt", 1),
                    json.dumps(task.get("details", {}), ensure_ascii=False),
                    json.dumps(task.get("logs", []), ensure_ascii=False),
                    task["created_at"], task["updated_at"], task.get("priority", 0),
                    task.get("resource_class", "io"), task.get("max_attempts", 1), task.get("next_retry_at"),
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

    def pause_task(self, task_id: str) -> bool:
        """Request a cooperative pause for a pending/running task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task["status"] not in ("pending", "running"):
                return False
            task["status"] = "paused"
            task["message"] = "已暂停"
            task["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._persist(task)
            return True

    def resume_task(self, task_id: str) -> bool:
        """Resume a cooperatively paused task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task["status"] != "paused":
                return False
            task["status"] = "running"
            task["message"] = "已继续处理"
            task["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            condition = self._pause_conditions.get(task_id)
            if condition:
                condition.notify_all()
            self._persist(task)
            return True

    def cancel_task(self, task_id: str) -> bool:
        """Cooperatively terminate a pending, running, or paused task.

        Paused workers are notified immediately. Pending futures are cancelled
        when the executor has not started them yet; otherwise the worker exits at
        its next checkpoint.
        """
        task_type = ""
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
            task_type = str(task.get("type") or "")
            task["message"] = "任务已终止"
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

            future = self._futures.get(task_id)
            if future:
                future.cancel()
            condition = self._pause_conditions.get(task_id)
            if condition:
                condition.notify_all()
            self._persist(task)
        if task_type == "clip_render_batch":
            try:
                from ..models.database import get_db
                db = get_db()
                db.execute(
                    """UPDATE clip_renders
                          SET status='cancelled',updated_at=datetime('now','localtime')
                        WHERE task_id=? AND status IN ('pending','running')""",
                    (task_id,),
                )
                db.commit()
                db.close()
            except Exception:
                logger.debug("Unable to cancel dependent clip render rows", exc_info=True)
        return True

    def is_cancelled(self, task_id: str) -> bool:
        """Return whether cancellation has been requested for a task."""
        with self._lock:
            return self._tasks.get(task_id, {}).get("status") == "cancelled"

    def checkpoint(self, task_id: str):
        """Pause cooperatively or raise when termination has been requested."""
        with self._lock:
            condition = self._pause_conditions.get(task_id)
            while condition and self._tasks.get(task_id, {}).get("status") == "paused":
                condition.wait()
            if self._tasks.get(task_id, {}).get("status") == "cancelled":
                raise TaskCancelled("任务已终止")

    def wait_if_paused(self, task_id: str):
        """Backward-compatible name for the shared cooperative checkpoint."""
        self.checkpoint(task_id)

    def run_background(self, task_id: str, func: Callable, *args, **kwargs):
        """在线程池中执行后台任务"""
        def _wrapper():
            resource = self._tasks.get(task_id, {}).get("resource_class", "io")
            limiter = self._resource_limits.get(resource, self._resource_limits["io"])
            try:
                limiter.acquire()
                while True:
                    try:
                        self.checkpoint(task_id)
                        current = self.get_task(task_id) or {}
                        self.update_task(task_id, status="running", progress=0.0, message="准备开始...", next_retry_at=None)
                        self.checkpoint(task_id)
                        logger.info(f"[TaskManager] 任务开始: {task_id}")
                        func(task_id, *args, **kwargs)
                        self.checkpoint(task_id)
                        current = self.get_task(task_id) or {}
                        if current.get("status") != "partial":
                            # A recoverable attempt may have populated error fields
                            # before the same task succeeds on retry.  Terminal
                            # success must not keep showing that stale failure in
                            # the task center or diagnostics.
                            self.update_task(
                                task_id, status="success", progress=100.0, message="完成",
                                error=None, error_code=None, recoverable=False,
                                available_actions=[], next_retry_at=None,
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
                        recoverable = bool(getattr(e, "recoverable", False))
                        automatic_retry = bool(getattr(e, "automatic_retry", False))
                        current = self.get_task(task_id) or {}
                        attempt = int(current.get("attempt", 1)); maximum = int(current.get("max_attempts", 1))
                        if automatic_retry and attempt < maximum:
                            delay = min(30, 2 ** (attempt - 1))
                            retry_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + delay))
                            self.update_task(task_id, status="pending", attempt=attempt + 1, next_retry_at=retry_at,
                                             message=f"将在 {delay} 秒后自动重试", error=str(e), error_code=error_code)
                            self.add_log(task_id, "warning", "自动重试", f"第 {attempt} 次尝试失败", detail=str(e), suggestion=f"{delay} 秒后重试")
                            deadline = time.monotonic() + delay
                            while time.monotonic() < deadline:
                                self.checkpoint(task_id); time.sleep(min(.25, deadline - time.monotonic()))
                            continue
                        logger.error(f"[TaskManager] 任务失败: {task_id} - {str(e)}", exc_info=True)
                        actions = list(getattr(e, "available_actions", ["retry"] if recoverable else []))
                        suggestion = getattr(e, "suggestion", "请复制诊断信息并检查运行日志")
                        failure_details = {"failure_suggestion": suggestion}
                        exception_details = getattr(e, "details", None)
                        if isinstance(exception_details, dict) and exception_details:
                            download_details = dict(
                                (current.get("details") or {}).get("download") or {}
                            )
                            download_details.update(exception_details)
                            download_details["task_attempt"] = attempt
                            download_details["automatic_retry"] = automatic_retry
                            failure_details["download"] = download_details
                        self.update_task(task_id, status="failed", error=str(e), error_code=error_code,
                                         recoverable=recoverable, available_actions=actions, message=str(e),
                                         details=failure_details)
                        self.add_log(task_id, "error", "任务失败", str(e), suggestion=suggestion)
                        try:
                            from ..models.database import get_db
                            db = get_db(); db.execute(
                                """UPDATE transcription_runs SET status='failed', error_code=?,error_message=?,
                                   finished_at=datetime('now','localtime') WHERE task_id=? AND status='running'""",
                                (error_code, str(e), task_id)); db.commit(); db.close()
                        except Exception:
                            logger.debug("Unable to finalize failed transcription run", exc_info=True)
                        break
            finally:
                limiter.release()

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
