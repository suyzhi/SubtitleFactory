"""Transactional editor operations, import snapshots, undo/redo history, and drafts."""

from __future__ import annotations

import json
import uuid

from pydantic import ValidationError

from ...models.database import get_db, segment_to_dict
from ...models.schemas import SegmentOperationItem, SegmentOperationRequest
from .operations import (
    APPLIERS,
    EditorServiceError,
    _now,
    _now_milliseconds,
    _project_revision,
    _remember_human_translations,
    _restore_snapshot,
    _rows,
    _snapshot,
    _validate_timeline,
)


def execute_operation(
    project_id: str,
    request: SegmentOperationRequest | None,
    *,
    commit_saved_draft: bool = False,
    rebase_draft: bool = False,
) -> dict:
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        revision = _project_revision(conn, project_id)
        if commit_saved_draft:
            draft = conn.execute(
                "SELECT base_revision,draft_json FROM segment_drafts WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if not draft:
                raise EditorServiceError(409, "DRAFT_EMPTY", "没有待保存的字幕草稿")
            try:
                items = json.loads(draft["draft_json"] or "[]")
                request = SegmentOperationRequest(
                    expected_revision=(revision if rebase_draft else int(draft["base_revision"])),
                    operation="update_many",
                    items=items,
                )
                if not request.items or any(
                    not (item.model_fields_set - {"index"}) for item in request.items
                ):
                    raise ValueError("draft must contain at least one change")
            except (TypeError, ValueError, ValidationError) as error:
                raise EditorServiceError(
                    422,
                    "DRAFT_INVALID",
                    "字幕草稿已损坏",
                    "请放弃该草稿并重新编辑",
                ) from error
        if request is None:
            raise EditorServiceError(422, "EDITOR_OPERATION_REQUIRED", "缺少字幕编辑操作")
        if revision != request.expected_revision:
            raise EditorServiceError(
                409,
                "EDIT_REVISION_CONFLICT",
                "字幕已在其他操作中发生变化",
                "请刷新字幕后重试",
                {"expected": request.expected_revision, "actual": revision},
            )
        before = _snapshot(_rows(conn, project_id))
        if commit_saved_draft:
            content_indices = {
                item.index
                for item in request.items
                if item.model_fields_set - {"index", "locked"}
            }
            explicit_unlock_indices = {
                item.index
                for item in request.items
                if "locked" in item.model_fields_set and item.locked is False
            }
            locked_indices = {
                int(row["idx"])
                for row in before
                if row["locked"] and row["idx"] in content_indices
            }
            locked_conflicts = sorted(locked_indices - explicit_unlock_indices)
            if locked_conflicts:
                raise EditorServiceError(
                    409,
                    "DRAFT_LOCKED_CONFLICT",
                    "草稿包含当前已锁定的字幕，未应用任何更改",
                    "请在草稿中明确解锁这些字幕，或放弃草稿",
                    {"indices": locked_conflicts},
                )
            if locked_indices:
                request.include_locked = True
        affected, time_changed = APPLIERS[request.operation](conn, project_id, request)
        if time_changed:
            _validate_timeline(conn, project_id)
        after_rows = _rows(conn, project_id)
        after = _snapshot(after_rows)
        _remember_human_translations(conn, project_id, before, after)
        operation_id = str(uuid.uuid4())
        next_revision = revision + 1
        conn.execute("DELETE FROM edit_operations WHERE project_id=? AND undone=1", (project_id,))
        conn.execute(
            """INSERT INTO edit_operations
               (id,project_id,operation,before_json,after_json,base_revision,result_revision,undone,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                operation_id,
                project_id,
                request.operation,
                json.dumps(before, ensure_ascii=False, separators=(",", ":")),
                json.dumps(after, ensure_ascii=False, separators=(",", ":")),
                revision,
                next_revision,
                0,
                _now(),
            ),
        )
        conn.execute(
            "UPDATE projects SET edit_revision=?,updated_at=? WHERE id=?",
            (next_revision, _now(), project_id),
        )
        conn.execute(
            """DELETE FROM edit_operations WHERE id IN (
                   SELECT id FROM edit_operations WHERE project_id=? AND undone=0
                   ORDER BY result_revision DESC LIMIT -1 OFFSET 500
               )""",
            (project_id,),
        )
        if commit_saved_draft:
            conn.execute("DELETE FROM segment_drafts WHERE project_id=?", (project_id,))
        conn.commit()
        return {
            "revision": next_revision,
            "operation_id": operation_id,
            "operation": request.operation,
            "affected_count": affected,
            "segments": [segment_to_dict(row) for row in after_rows],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def import_segment_snapshot(
    project_id: str,
    expected_revision: int,
    cues: list[dict],
    *,
    candidate_run_id: str | None = None,
) -> dict:
    """Replace the current track with imported cues as one persistent operation."""
    from ..transcription_results import candidate_result

    partial_candidate = False
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        revision = _project_revision(conn, project_id)
        if revision != expected_revision:
            raise EditorServiceError(
                409,
                "EDIT_REVISION_CONFLICT",
                "字幕已发生变化",
                "请刷新后重新导入",
                {"expected": expected_revision, "actual": revision},
            )
        if candidate_run_id:
            active = conn.execute(
                "SELECT type,details FROM tasks WHERE project_id=? AND status IN ('pending','running','paused') AND type IN ('clean','translate','ocr','speaker_diarization','workflow')",
                (project_id,),
            ).fetchall()
            for task in active:
                stages = json.loads(task["details"] or "{}").get("stages") or {}
                if task["type"] != "workflow" or any(
                    stages.get(step) == "running" for step in ("clean", "translate")
                ):
                    raise EditorServiceError(
                        409,
                        "SUBTITLE_TASK_ACTIVE",
                        "字幕正在被其他任务处理，请完成或取消后再采用候选结果",
                    )
            draft = conn.execute(
                "SELECT 1 FROM segment_drafts WHERE project_id=?", (project_id,)
            ).fetchone()
            if draft:
                raise EditorServiceError(
                    409, "DRAFT_PENDING", "请先保存或放弃字幕草稿，再替换候选结果"
                )
            candidate = candidate_result(conn, project_id, candidate_run_id)
            if candidate is None:
                raise EditorServiceError(409, "CANDIDATE_UNAVAILABLE", "候选结果已变化，请刷新")
            cues, partial_candidate = candidate
            if not cues:
                raise EditorServiceError(409, "CANDIDATE_EMPTY", "候选结果为空")
        before = _snapshot(_rows(conn, project_id))
        conn.execute("DELETE FROM segments WHERE project_id=?", (project_id,))
        for index, cue in enumerate(cues, 1):
            identifier = str(uuid.uuid4())
            text = cue.get("text", "")
            conn.execute(
                """INSERT INTO segments
                   (id,project_id,idx,start,end,raw_text,clean_text,translated_text,speaker,speaker_id,locked,is_draft,source_stage)
                   VALUES (?,?,?,?,?,?,?,'','',NULL,0,0,'imported')""",
                (identifier, project_id, index, cue["start"], cue["end"], text, text),
            )
        _validate_timeline(conn, project_id)
        if candidate_run_id:
            conn.execute(
                "UPDATE transcription_runs SET status='accepted' WHERE id=?", (candidate_run_id,)
            )
            conn.execute(
                "UPDATE segments SET transcription_run_id=?,source_stage=? WHERE project_id=?",
                (
                    candidate_run_id,
                    "recovered_partial" if partial_candidate else "postprocessed",
                    project_id,
                ),
            )
        after_rows = _rows(conn, project_id)
        after = _snapshot(after_rows)
        operation_id = str(uuid.uuid4())
        next_revision = revision + 1
        conn.execute("DELETE FROM edit_operations WHERE project_id=? AND undone=1", (project_id,))
        conn.execute(
            """INSERT INTO edit_operations
               (id,project_id,operation,before_json,after_json,base_revision,result_revision,undone,created_at)
               VALUES (?,?,?,?,?,?,?,0,?)""",
            (
                operation_id,
                project_id,
                "import_subtitles",
                json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False),
                revision,
                next_revision,
                _now(),
            ),
        )
        conn.execute(
            "UPDATE projects SET edit_revision=?,updated_at=? WHERE id=?",
            (next_revision, _now(), project_id),
        )
        conn.commit()
        return {
            "revision": next_revision,
            "operation_id": operation_id,
            "operation": "import_subtitles",
            "affected_count": len(cues),
            "segments": [segment_to_dict(row) for row in after_rows],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def history_step(project_id: str, expected_revision: int, direction: str) -> dict:
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        revision = _project_revision(conn, project_id)
        if revision != expected_revision:
            raise EditorServiceError(
                409,
                "EDIT_REVISION_CONFLICT",
                "字幕版本已变化",
                "请刷新字幕后重试",
                {"expected": expected_revision, "actual": revision},
            )
        if direction == "undo":
            row = conn.execute(
                """SELECT * FROM edit_operations WHERE project_id=? AND undone=0
                   ORDER BY result_revision DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
            snapshot_field, undone = "before_json", 1
        else:
            row = conn.execute(
                """SELECT * FROM edit_operations WHERE project_id=? AND undone=1
                   ORDER BY result_revision ASC LIMIT 1""",
                (project_id,),
            ).fetchone()
            snapshot_field, undone = "after_json", 0
        if not row:
            raise EditorServiceError(
                409,
                "HISTORY_EMPTY",
                "没有可撤销的操作" if direction == "undo" else "没有可重做的操作",
            )
        _restore_snapshot(conn, project_id, json.loads(row[snapshot_field]))
        next_revision = revision + 1
        conn.execute("UPDATE edit_operations SET undone=? WHERE id=?", (undone, row["id"]))
        conn.execute(
            "UPDATE projects SET edit_revision=?,updated_at=? WHERE id=?",
            (next_revision, _now(), project_id),
        )
        rows = _rows(conn, project_id)
        conn.commit()
        return {
            "revision": next_revision,
            "operation_id": row["id"],
            "operation": direction,
            "affected_count": len(rows),
            "segments": [segment_to_dict(item) for item in rows],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_draft(project_id: str, base_revision: int, items: list[dict]) -> dict:
    if not items or any(not (set(item) - {"index"}) for item in items):
        raise EditorServiceError(422, "DRAFT_INVALID", "字幕草稿没有可保存的更改")
    conn = get_db()
    try:
        _project_revision(conn, project_id)
        conn.execute(
            """INSERT INTO segment_drafts(project_id,base_revision,draft_json,updated_at)
               VALUES (?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET
               base_revision=excluded.base_revision,draft_json=excluded.draft_json,updated_at=excluded.updated_at""",
            (
                project_id,
                base_revision,
                json.dumps(items, ensure_ascii=False),
                _now_milliseconds(),
            ),
        )
        conn.commit()
        return {"project_id": project_id, "base_revision": base_revision, "items": items}
    finally:
        conn.close()


def get_draft(project_id: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM segment_drafts WHERE project_id=?", (project_id,)
        ).fetchone()
        if not row:
            return None
        invalid = False
        try:
            raw_items = json.loads(row["draft_json"] or "[]")
            if not isinstance(raw_items, list):
                raise ValueError("draft items must be a list")
            items = [
                SegmentOperationItem.model_validate(item).model_dump(exclude_unset=True)
                for item in raw_items
            ]
        except (TypeError, ValueError, ValidationError):
            items = []
            invalid = True
        return {
            "project_id": project_id,
            "base_revision": row["base_revision"],
            "items": items,
            "updated_at": row["updated_at"],
            "invalid": invalid,
        }
    finally:
        conn.close()


def commit_draft(project_id: str, *, rebase: bool = False) -> dict:
    return execute_operation(
        project_id,
        None,
        commit_saved_draft=True,
        rebase_draft=rebase,
    )


def discard_draft(project_id: str) -> None:
    conn = get_db()
    try:
        conn.execute("DELETE FROM segment_drafts WHERE project_id=?", (project_id,))
        conn.commit()
    finally:
        conn.close()
