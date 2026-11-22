"""Restart interrupted subtitle workflows after the App relaunches."""

from __future__ import annotations

import json
import logging

from ..models.database import get_db

logger = logging.getLogger(__name__)


def resume_interrupted_workflows(interrupted: list[dict]) -> int:
    from ..api.projects import _do_workflow
    from ..services.distribution import distribution_capabilities
    from ..utils.task_manager import task_manager

    youtube_enabled = distribution_capabilities().youtube
    resumed = 0
    for original in interrupted:
        if original.get("type") != "workflow":
            continue
        try:
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
            # Never silently resubmit billable LLM requests after a crash.
            if (payload.get("options") or {}).get("enable_clean") or (
                payload.get("options") or {}
            ).get("enable_translate"):
                continue
            db = get_db()
            exists = db.execute(
                "SELECT source_type FROM projects WHERE id=?",
                (payload["project_id"],),
            ).fetchone()
            db.close()
            if not exists:
                continue
            if exists["source_type"] == "youtube" and not youtube_enabled:
                continue
            task_id = task_manager.create_task(payload["project_id"], "workflow")
            task_manager.update_task(
                task_id,
                parent_task_id=original["id"],
                details={
                    "resume_payload": payload,
                    "resumed_after_restart": True,
                },
            )
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
                task_id,
                _do_workflow,
                payload["project_id"],
                payload["model"],
                payload["language"],
                payload.get("source_url"),
                payload["runtime"],
                payload.get("options"),
                details,
            )
            resumed += 1
        except Exception:
            logger.exception("恢复中断工作流失败: %s", original.get("id"))
    return resumed
