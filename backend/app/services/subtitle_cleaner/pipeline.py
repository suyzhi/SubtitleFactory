"""Subtitle cleaning orchestration, failed-batch retry, undo, and transactional commit."""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from ...models.database import get_db
from ...utils.task_manager import task_manager
from ..ai_providers import assigned_provider
from ..ai_settings import get_ai_settings
from ..editor import SEGMENT_COLUMNS, history_step
from .batching import (
    _batch_character_count,
    _build_semantic_batches,
    _fingerprint,
    _join_text,
    _now,
    facade_attr,
)
from .llm import _call_llm_group


class BatchRetryError(RuntimeError):
    def __init__(self, message: str, error_code: str = "CLEAN_BATCH_RETRY_CONFLICT"):
        super().__init__(message)
        self.error_code = error_code
        self.recoverable = False
        self.available_actions = []
        self.suggestion = "字幕在失败后已被修改，请重新执行整份 AI 整理"


def clean_subtitles(
    task_id: str,
    project_id: str,
    target_length: int = 42,
    provider_id: str | None = None,
    model: str | None = None,
):
    tm = facade_attr("task_manager", task_manager)
    provider_fn = facade_attr("assigned_provider", assigned_provider)
    ai_settings_fn = facade_attr("get_ai_settings", get_ai_settings)
    call_llm_fn = facade_attr("_call_llm_group", _call_llm_group)

    target_length = max(16, min(100, int(target_length)))
    try:
        ai = provider_fn("clean", provider_id, model)
    except ValueError:
        ai = ai_settings_fn(include_secret=True)

    db = get_db()
    rows = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM segments WHERE project_id = ? ORDER BY idx", (project_id,)
        ).fetchall()
    ]
    db.close()
    if not rows:
        tm.update_task(task_id, step="cleaning", progress=100, message="没有字幕需要处理")
        return []

    initial_fingerprint = _fingerprint(rows)
    batches = _build_semantic_batches(rows)
    total_batches = len(batches)
    grouped_results = []
    failed_batches = 0
    failed_batch_indexes: list[int] = []
    request_attempts = 0
    adaptive_splits = 0
    length_recoveries = 0
    cache_hits = 0
    prompt_tokens = 0
    completion_tokens = 0
    reasoning_tokens = 0
    failed_leaf_batches = 0
    tm.add_log(
        task_id,
        "info",
        "AI 句子重组",
        f"开始分析 {len(rows)} 条字幕，将在 {total_batches} 个语义批次中识别完整句子",
        detail=(
            f"AI: {ai['provider']} · {ai['model']} · "
            f"{'思考开启' if ai.get('thinking_enabled') else '思考关闭'} · "
            f"输入字符 {_batch_character_count(rows)} · 单批最多 32 条/3500 字符"
        ),
    )

    def process(batch_index: int, batch: list[dict]):
        fingerprint = json.dumps(
            {
                "segments": _fingerprint(batch),
                "provider": ai["provider"],
                "model": ai["model"],
                "target_length": target_length,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        db = get_db()
        cached = db.execute(
            "SELECT result_json FROM ai_batch_results WHERE project_id=? AND operation='clean' AND input_fingerprint=? AND status='success' ORDER BY updated_at DESC LIMIT 1",
            (project_id, fingerprint),
        ).fetchone()
        if cached:
            try:
                groups = json.loads(cached["result_json"])
                db.close()
                return batch_index, groups, None, {"cache_hits": 1}
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        db.execute(
            """INSERT OR REPLACE INTO ai_batch_results
            (task_id,project_id,operation,batch_index,input_fingerprint,status,result_json,attempts,error,updated_at)
            VALUES (?,?,?,?,?,'running','[]',0,'',?)""",
            (task_id, project_id, "clean", batch_index, fingerprint, _now()),
        )
        db.commit()
        db.close()
        diagnostics: dict[str, int] = {}
        try:
            groups = call_llm_fn(
                batch,
                ai,
                target_length,
                task_id=task_id,
                diagnostics=diagnostics,
            )
            attempts = int(diagnostics.get("request_attempts", 0))
            db = get_db()
            db.execute(
                "UPDATE ai_batch_results SET status='success',result_json=?,attempts=?,error='',updated_at=? WHERE task_id=? AND batch_index=?",
                (json.dumps(groups, ensure_ascii=False), attempts, _now(), task_id, batch_index),
            )
            db.commit()
            db.close()
            return batch_index, groups, None, diagnostics
        except Exception as exc:
            attempts = int(diagnostics.get("request_attempts", 0))
            db = get_db()
            db.execute(
                "UPDATE ai_batch_results SET status='failed',attempts=?,error=?,updated_at=? WHERE task_id=? AND batch_index=?",
                (attempts, str(exc)[:500], _now(), task_id, batch_index),
            )
            db.commit()
            db.close()
            groups = [
                {"ids": [str(row["idx"])], "clean_text": row["clean_text"] or row["raw_text"]}
                for row in batch
            ]
            return batch_index, groups, exc, diagnostics

    completed = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="subtitle-ai") as executor:
        futures = [executor.submit(process, index, batch) for index, batch in enumerate(batches, 1)]
        for done_count, future in enumerate(as_completed(futures), 1):
            tm.checkpoint(task_id)
            index, groups, error, diagnostics = future.result()
            completed[index] = groups
            request_attempts += int(diagnostics.get("request_attempts", 0))
            adaptive_splits += int(diagnostics.get("adaptive_splits", 0))
            length_recoveries += int(diagnostics.get("length_recoveries", 0))
            cache_hits += int(diagnostics.get("cache_hits", 0))
            prompt_tokens += int(diagnostics.get("prompt_tokens", 0))
            completion_tokens += int(diagnostics.get("completion_tokens", 0))
            reasoning_tokens += int(diagnostics.get("reasoning_tokens", 0))
            failed_leaf_batches += int(diagnostics.get("failed_leaf_batches", 0))
            if error:
                failed_batches += 1
                failed_batch_indexes.append(index)
                tm.add_log(
                    task_id,
                    "warning",
                    "AI 句子重组",
                    f"第 {index}/{total_batches} 批未能重组",
                    detail=str(error),
                    suggestion="可在“整理”设置中单独重试该批次",
                )
            tm.update_task(
                task_id,
                step="restructuring",
                progress=5 + done_count / max(total_batches, 1) * 88,
                message=f"已完成 {done_count}/{total_batches} 个批次",
                details={
                    "total_segments": len(rows),
                    "total_batches": total_batches,
                    "completed_batches": done_count,
                    "failed_batches": failed_batches,
                    "failed_batch_indexes": sorted(failed_batch_indexes),
                    "ai_provider": ai["provider"],
                    "ai_model": ai["model"],
                    "request_attempts": request_attempts,
                    "adaptive_splits": adaptive_splits,
                    "length_recoveries": length_recoveries,
                    "cache_hits": cache_hits,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "reasoning_tokens": reasoning_tokens,
                    "failed_leaf_batches": failed_leaf_batches,
                },
            )
    for index in range(1, total_batches + 1):
        grouped_results.extend(completed[index])

    tm.checkpoint(task_id)
    final_segments = _compose_final_segments(rows, grouped_results)
    tm.checkpoint(task_id)
    conflicts = _commit_restructured_segments(
        project_id, rows, initial_fingerprint, final_segments, task_id=task_id
    )
    merged_count = len(rows) - len(final_segments)
    status = "partial" if failed_batches else "running"
    message = f"AI 整理完成：{len(rows)} 条重组为 {len(final_segments)} 条完整字幕"
    if failed_batches:
        message += f"，{failed_batches} 个批次保持原样"
    tm.update_task(
        task_id,
        step="cleaning_done",
        progress=100,
        status=status,
        message=message,
        details={
            "source_segments": len(rows),
            "total_segments": len(final_segments),
            "merged_segments": max(0, merged_count),
            "failed_batches": failed_batches,
            "conflict_batches": conflicts,
            "total_batches": total_batches,
            "ai_provider": ai["provider"],
            "ai_model": ai["model"],
            "target_length": target_length,
            "failed_batch_indexes": sorted(failed_batch_indexes),
            "request_attempts": request_attempts,
            "adaptive_splits": adaptive_splits,
            "length_recoveries": length_recoveries,
            "cache_hits": cache_hits,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "reasoning_tokens": reasoning_tokens,
            "failed_leaf_batches": failed_leaf_batches,
        },
    )
    tm.add_log(
        task_id,
        "info",
        "AI 句子重组",
        message,
        suggestion="如效果不合适，可使用“撤销整理”恢复",
    )
    return final_segments


def retry_clean_batch(task_id: str, original_task_id: str, batch_index: int):
    """Retry one stored failed batch and reconcile only its original segment range."""
    tm = facade_attr("task_manager", task_manager)
    provider_fn = facade_attr("assigned_provider", assigned_provider)
    ai_settings_fn = facade_attr("get_ai_settings", get_ai_settings)
    call_llm_fn = facade_attr("_call_llm_group", _call_llm_group)

    db = get_db()
    try:
        failed = db.execute(
            """SELECT * FROM ai_batch_results
               WHERE task_id=? AND operation='clean' AND batch_index=? AND status='failed'""",
            (original_task_id, batch_index),
        ).fetchone()
        if not failed:
            raise BatchRetryError("这个失败批次不存在，或已经重试成功", "CLEAN_BATCH_NOT_FOUND")
        failed = dict(failed)
        try:
            stored = json.loads(failed["input_fingerprint"] or "{}")
            stored_segments = stored["segments"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BatchRetryError(
                "失败批次缺少可恢复的输入快照", "CLEAN_BATCH_SNAPSHOT_INVALID"
            ) from exc
        current = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM segments WHERE project_id=? ORDER BY idx", (failed["project_id"],)
            ).fetchall()
        ]
    finally:
        db.close()

    if not stored_segments:
        raise BatchRetryError("失败批次的输入快照为空", "CLEAN_BATCH_SNAPSHOT_INVALID")
    positions = {row["id"]: index for index, row in enumerate(current)}
    stored_ids = [str(item[0]) for item in stored_segments]
    if any(segment_id not in positions for segment_id in stored_ids):
        raise BatchRetryError("失败批次中的字幕已被删除或合并，不能安全覆盖")
    selected_positions = [positions[segment_id] for segment_id in stored_ids]
    first_position = min(selected_positions)
    if selected_positions != list(range(first_position, first_position + len(stored_ids))):
        raise BatchRetryError("失败批次中的字幕顺序已改变，不能安全覆盖")

    batch = [current[position] for position in selected_positions]
    for snapshot, row in zip(stored_segments, batch, strict=True):
        current_signature = (
            row["id"],
            float(row["start"]),
            float(row["end"]),
            row.get("raw_text", ""),
            row.get("clean_text", ""),
            row.get("translated_text", ""),
            int(bool(row.get("locked"))),
        )
        stored_signature = (
            str(snapshot[0]),
            float(snapshot[2]),
            float(snapshot[3]),
            snapshot[4] or "",
            snapshot[5] or "",
            snapshot[6] or "",
            int(bool(snapshot[7])),
        )
        if current_signature != stored_signature:
            raise BatchRetryError("失败批次中的字幕内容或时间已经修改，不能安全覆盖")

    provider_id = stored.get("provider")
    model = stored.get("model")
    target_length = max(16, min(100, int(stored.get("target_length") or 42)))
    try:
        ai = provider_fn("clean", provider_id, model)
    except ValueError:
        ai = ai_settings_fn(include_secret=True)

    retry_fingerprint = json.dumps(
        {
            "segments": _fingerprint(batch),
            "provider": ai["provider"],
            "model": ai["model"],
            "target_length": target_length,
            "retry_of": original_task_id,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    tm.update_task(
        task_id,
        step="retrying_failed_batch",
        progress=10,
        message=f"正在单独重试第 {batch_index} 批（{len(batch)} 条字幕）",
        details={
            "retry_of": original_task_id,
            "batch_index": batch_index,
            "batch_segments": len(batch),
            "target_length": target_length,
        },
    )
    tm.add_log(
        task_id,
        "info",
        "AI 批次重试",
        f"只重试第 {batch_index} 批",
        detail=f"范围 {batch[0]['start']:.2f}s–{batch[-1]['end']:.2f}s，共 {len(batch)} 条",
    )
    db = get_db()
    db.execute(
        """INSERT OR REPLACE INTO ai_batch_results
           (task_id,project_id,operation,batch_index,input_fingerprint,status,result_json,attempts,error,updated_at)
           VALUES (?,?,?,?,?,'running','[]',0,'',?)""",
        (task_id, failed["project_id"], "clean", batch_index, retry_fingerprint, _now()),
    )
    db.commit()
    db.close()

    diagnostics: dict[str, int] = {}
    try:
        groups = call_llm_fn(
            batch,
            ai,
            target_length,
            task_id=task_id,
            diagnostics=diagnostics,
        )
        tm.update_task(task_id, progress=75, message=f"第 {batch_index} 批返回有效 JSON，正在应用")
        final_segments = _compose_final_segments(current, groups)
        conflicts = _commit_restructured_segments(
            failed["project_id"], current, _fingerprint(current), final_segments, task_id=task_id
        )
        if conflicts:
            raise BatchRetryError("应用重试结果时检测到字幕被同时修改，结果未覆盖原字幕")
    except Exception as exc:
        db = get_db()
        attempts = int(diagnostics.get("request_attempts", 0))
        db.execute(
            "UPDATE ai_batch_results SET status='failed',attempts=?,error=?,updated_at=? WHERE task_id=? AND batch_index=?",
            (attempts, str(exc)[:500], _now(), task_id, batch_index),
        )
        db.commit()
        db.close()
        tm.update_task(
            task_id,
            details={
                "failed_batches": 1,
                "failed_batch_indexes": [batch_index],
                "batch_index": batch_index,
                "single_batch_retry": True,
            },
        )
        raise

    db = get_db()
    try:
        result_json = json.dumps(groups, ensure_ascii=False)
        attempts = int(diagnostics.get("request_attempts", 0))
        db.execute(
            "UPDATE ai_batch_results SET status='success',result_json=?,attempts=?,error='',updated_at=? WHERE task_id=? AND batch_index=?",
            (result_json, attempts, _now(), task_id, batch_index),
        )
        db.execute(
            "UPDATE ai_batch_results SET status='success',result_json=?,error='',updated_at=? WHERE task_id=? AND batch_index=?",
            (result_json, _now(), original_task_id, batch_index),
        )
        remaining_rows = db.execute(
            "SELECT batch_index FROM ai_batch_results WHERE task_id=? AND status='failed' ORDER BY batch_index",
            (original_task_id,),
        ).fetchall()
        db.commit()
    finally:
        db.close()

    remaining = [int(row["batch_index"]) for row in remaining_rows]
    original = tm.get_task(original_task_id)
    if original:
        tm.update_task(
            original_task_id,
            status="partial" if remaining else "success",
            message=(
                f"仍有 {len(remaining)} 个整理批次失败" if remaining else "所有整理批次均已成功"
            ),
            details={"failed_batches": len(remaining), "failed_batch_indexes": remaining},
        )
    tm.update_task(
        task_id,
        step="batch_retry_done",
        progress=100,
        message=f"第 {batch_index} 批已重新整理成功",
        details={
            "failed_batches": len(remaining),
            "remaining_failed_batch_indexes": remaining,
            "request_attempts": int(diagnostics.get("request_attempts", 0)),
            "adaptive_splits": int(diagnostics.get("adaptive_splits", 0)),
            "length_recoveries": int(diagnostics.get("length_recoveries", 0)),
        },
    )
    return final_segments


def undo_last_clean(project_id: str) -> int:
    db = get_db()
    latest = db.execute(
        "SELECT operation FROM edit_operations WHERE project_id=? AND undone=0 ORDER BY result_revision DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    project = db.execute("SELECT edit_revision FROM projects WHERE id=?", (project_id,)).fetchone()
    db.close()
    if not latest or latest["operation"] != "ai_clean" or not project:
        raise ValueError("最近一次编辑不是可撤销的 AI 整理")
    return history_step(project_id, int(project["edit_revision"] or 0), "undo")["affected_count"]


def _compose_final_segments(rows: list[dict], groups: list[dict]) -> list[dict]:
    by_idx = {str(row["idx"]): row for row in rows}
    group_by_first = {group["ids"][0]: group for group in groups}
    consumed = set()
    output = []
    for row in rows:
        key = str(row["idx"])
        if key in consumed:
            continue
        if row.get("locked") or key not in group_by_first:
            output.append(dict(row))
            consumed.add(key)
            continue
        group = group_by_first[key]
        source_rows = [by_idx[value] for value in group["ids"]]
        consumed.update(group["ids"])
        first, last = source_rows[0], source_rows[-1]
        raw_text = _join_text([item["raw_text"] for item in source_rows])
        speakers = {item.get("speaker") or "" for item in source_rows}
        output.append(
            {
                "id": first["id"],
                "project_id": first["project_id"],
                "idx": 0,
                "start": first["start"],
                "end": last["end"],
                "raw_text": raw_text,
                "clean_text": group["clean_text"],
                "translated_text": "",
                "speaker": speakers.pop() if len(speakers) == 1 else "",
                "locked": 0,
                "is_draft": 0,
                "source_stage": "cleaned",
                "_source_ids": [item["id"] for item in source_rows],
            }
        )
    for index, segment in enumerate(output, 1):
        segment["idx"] = index
    return output


def _commit_restructured_segments(
    project_id: str,
    original: list[dict],
    fingerprint,
    final_segments: list[dict],
    task_id: str | None = None,
):
    del fingerprint
    tm = facade_attr("task_manager", task_manager)
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        current = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM segments WHERE project_id = ? ORDER BY idx", (project_id,)
            ).fetchall()
        ]
        before_snapshot = [{column: row.get(column) for column in SEGMENT_COLUMNS} for row in current]
        revision = int(
            db.execute("SELECT edit_revision FROM projects WHERE id=?", (project_id,)).fetchone()[0]
            or 0
        )
        original_by_id = {row["id"]: row for row in original}
        current_by_id = {row["id"]: row for row in current}
        reconciled = []
        consumed = set()
        conflicts = 0
        for segment in final_segments:
            source_ids = segment.pop("_source_ids", [segment["id"]])
            unchanged = all(
                source_id in current_by_id
                and source_id in original_by_id
                and _fingerprint([current_by_id[source_id]])
                == _fingerprint([original_by_id[source_id]])
                for source_id in source_ids
            )
            if unchanged:
                reconciled.append(segment)
            else:
                conflicts += 1
                reconciled.extend(
                    dict(current_by_id[source_id])
                    for source_id in source_ids
                    if source_id in current_by_id
                )
            consumed.update(source_ids)
        reconciled.extend(dict(row) for row in current if row["id"] not in consumed)
        reconciled = sorted(reconciled, key=lambda item: item["start"])
        for index, segment in enumerate(reconciled, 1):
            segment["idx"] = index
        if task_id:
            tm.checkpoint(task_id)
        db.execute("DELETE FROM segments WHERE project_id = ?", (project_id,))
        for index, segment in enumerate(reconciled):
            if task_id and index % 20 == 0:
                tm.checkpoint(task_id)
            _insert_segment(db, segment)
        after_rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM segments WHERE project_id=? ORDER BY idx", (project_id,)
            )
        ]
        after_snapshot = [{column: row.get(column) for column in SEGMENT_COLUMNS} for row in after_rows]
        operation_id = str(uuid.uuid4())
        next_revision = revision + 1
        db.execute("DELETE FROM edit_operations WHERE project_id=? AND undone=1", (project_id,))
        db.execute(
            """INSERT INTO edit_operations(id,project_id,operation,before_json,after_json,base_revision,result_revision,undone,created_at)
               VALUES (?,?,?,?,?,?,?,0,?)""",
            (
                operation_id,
                project_id,
                "ai_clean",
                json.dumps(before_snapshot, ensure_ascii=False),
                json.dumps(after_snapshot, ensure_ascii=False),
                revision,
                next_revision,
                _now(),
            ),
        )
        db.execute(
            "UPDATE projects SET edit_revision=?,updated_at=? WHERE id=?",
            (next_revision, _now(), project_id),
        )
        db.execute(
            """DELETE FROM edit_operations WHERE id IN (
            SELECT id FROM edit_operations WHERE project_id=? AND undone=0
            ORDER BY result_revision DESC LIMIT -1 OFFSET 500)""",
            (project_id,),
        )
        if task_id:
            tm.checkpoint(task_id)
        db.commit()
        return conflicts
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _insert_segment(db, segment: dict):
    db.execute(
        """INSERT INTO segments
           (id, project_id, idx, start, end, raw_text, clean_text, translated_text, speaker, speaker_id, locked, is_draft, source_stage, transcription_run_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            segment["id"],
            segment["project_id"],
            segment["idx"],
            segment["start"],
            segment["end"],
            segment.get("raw_text", ""),
            segment.get("clean_text", ""),
            segment.get("translated_text", ""),
            segment.get("speaker", ""),
            segment.get("speaker_id"),
            int(bool(segment.get("locked"))),
            int(bool(segment.get("is_draft"))),
            segment.get("source_stage", "final"),
            segment.get("transcription_run_id"),
        ),
    )
