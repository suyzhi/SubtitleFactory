"""Runtime watch-folder ingestion and interrupted workflow restoration."""

from __future__ import annotations

import json
import logging
import threading
import time

from ..models.database import get_db


logger = logging.getLogger(__name__)


def process_watch_folders_once() -> int:
    # Imports stay local to avoid coupling service initialization to FastAPI's
    # route import order.
    from ..api.batches import BatchCreate, create_batch
    from ..api.projects import WorkflowRequest, start_workflow
    from ..api.watch_folders import WatchImportMark, mark_watch_file_imported, scan_watch_folders

    ready = scan_watch_folders()["ready"]
    imported = 0
    for item in ready:
        try:
            workflow = dict(item.get("workflow") or {})
            batch = create_batch(BatchCreate(
                name="监听文件夹自动导入", paths=[item["path"]], configuration=workflow,
            ))
            project_id = batch["items"][0]["project_id"]
            template_id = workflow.get("style_template_id")
            if template_id:
                db = get_db()
                template = db.execute("SELECT settings_json FROM style_templates WHERE id=?", (template_id,)).fetchone()
                if template:
                    db.execute("INSERT OR REPLACE INTO project_styles(project_id,settings_json,updated_at) VALUES (?,?,?)", (project_id, template["settings_json"], time.strftime("%Y-%m-%d %H:%M:%S")))
                    db.commit()
                db.close()
            mark_watch_file_imported(item["watch_folder_id"], WatchImportMark(path=item["path"], project_id=project_id))
            start_workflow(project_id, WorkflowRequest(
                model=str(workflow.get("model") or "auto"),
                language=str(workflow.get("language") or "auto"),
                runtime=str(workflow.get("runtime") or "") or None,
            ))
            imported += 1
        except Exception:
            logger.exception("监听文件自动入队失败: %s", item.get("path"))
    return imported


def watch_loop(stop_event: threading.Event, interval_seconds: float = 15.0) -> None:
    while not stop_event.is_set():
        try:
            process_watch_folders_once()
        except Exception:
            logger.exception("监听文件夹周期扫描失败")
        stop_event.wait(interval_seconds)


def resume_interrupted_workflows(interrupted: list[dict]) -> int:
    from ..api.projects import _do_workflow
    from ..services.cloud_asr import FUN_ASR_MODEL_ID, FUN_ASR_RUNTIME
    from ..services.distribution import distribution_capabilities
    from ..utils.task_manager import task_manager

    youtube_enabled = distribution_capabilities().youtube
    resumed = 0
    for original in interrupted:
        if original.get("type") != "workflow":
            continue
        try:
            # A paused task reflects an explicit user decision.  The worker no
            # longer exists after restart, so surface it as interrupted and let
            # the user decide when to start it again.
            if original.get("status") == "paused":
                continue
            raw_details = original.get("details") or {}
            details = (
                raw_details
                if isinstance(raw_details, dict)
                else json.loads(raw_details or "{}")
            )
            if not isinstance(details, dict):
                continue
            payload = details.get("resume_payload") or {}
            if not isinstance(payload, dict):
                continue
            required = ("project_id", "model", "language", "runtime")
            if not all(payload.get(key) for key in required):
                continue
            # A crash can happen after a cloud provider accepted a billable
            # audio chunk but before the response reached us.  Never resubmit
            # cloud audio silently; the recovery card requires a new user click.
            if (
                payload.get("model") == FUN_ASR_MODEL_ID
                or payload.get("runtime") == FUN_ASR_RUNTIME
            ):
                continue
            db = get_db()
            exists = db.execute(
                "SELECT source_type FROM projects WHERE id=? AND deleted_at IS NULL",
                (payload["project_id"],),
            ).fetchone()
            db.close()
            if not exists:
                continue
            if exists["source_type"] == "youtube" and not youtube_enabled:
                continue
            task_id = task_manager.create_task(payload["project_id"], "workflow")
            task_manager.update_task(task_id, parent_task_id=original["id"], details={
                "resume_payload": payload, "resumed_after_restart": True,
            })
            # Keep the interrupted row as honest history, but remove its stale
            # retry button now that the linked child task owns the continuation.
            db = get_db()
            try:
                persisted = db.execute(
                    "SELECT details FROM tasks WHERE id=?", (original["id"],)
                ).fetchone()
                previous_details = {}
                if persisted:
                    try:
                        previous_details = json.loads(persisted["details"] or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        previous_details = {}
                if not isinstance(previous_details, dict):
                    previous_details = {}
                previous_details["resumed_by_task_id"] = task_id
                db.execute(
                    """UPDATE tasks
                          SET recoverable=0,available_actions='[]',
                              message='应用重启后已在新任务中继续',details=?
                        WHERE id=?""",
                    (json.dumps(previous_details, ensure_ascii=False), original["id"]),
                )
                db.commit()
            finally:
                db.close()
            task_manager.run_background(
                task_id, _do_workflow, payload["project_id"], payload["model"],
                payload["language"], payload.get("source_url"), payload["runtime"],
            )
            resumed += 1
        except Exception:
            logger.exception("恢复中断工作流失败: %s", original.get("id"))
    return resumed
