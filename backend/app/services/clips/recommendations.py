"""Subtitle-grounded short-clip window discovery, AI ranking, and clip-set CRUD."""

from __future__ import annotations

import json
import uuid
from typing import Any

from ...models.database import get_db
from ...utils.task_manager import TaskCancelled, task_manager
from ..ai_providers import assigned_provider
from ..content_packs import _call_json
from .common import ASPECT_DIMENSIONS, ClipError, _fingerprint, _now, _source, facade_attr


def _candidate_windows(
    rows: list[dict[str, Any]],
    minimum: float,
    maximum: float,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    step = max(1, len(rows) // 28)
    windows: list[dict[str, Any]] = []
    for start_position in range(0, len(rows), step):
        start = rows[start_position]
        end_position = start_position
        while end_position + 1 < len(rows):
            next_row = rows[end_position + 1]
            if float(next_row["end"]) - float(start["start"]) > maximum:
                break
            end_position += 1
            if float(rows[end_position]["end"]) - float(start["start"]) >= minimum:
                # Prefer a natural pause or a complete sentence ending.
                text = (
                    rows[end_position].get("clean_text") or rows[end_position].get("raw_text") or ""
                ).strip()
                gap = (
                    float(rows[end_position + 1]["start"]) - float(rows[end_position]["end"])
                    if end_position + 1 < len(rows)
                    else 1
                )
                if gap >= 0.45 or text.endswith(("。", "！", "？", ".", "!", "?")):
                    break
        duration = float(rows[end_position]["end"]) - float(start["start"])
        if duration < minimum:
            continue
        selected = rows[start_position : end_position + 1]
        windows.append(
            {
                "candidate_key": f"{int(start['idx'])}-{int(selected[-1]['idx'])}",
                "start_index": int(start["idx"]),
                "end_index": int(selected[-1]["idx"]),
                "start": float(start["start"]),
                "end": float(selected[-1]["end"]),
                "duration": duration,
                "text": "\n".join(
                    f"{row.get('speaker') or ''}：{row.get('clean_text') or row.get('raw_text') or ''}"
                    for row in selected
                )[:10_000],
            }
        )
        if len(windows) >= 40:
            break
    return windows


def _rank_candidates(
    ai: dict[str, Any],
    task_id: str,
    title: str,
    windows: list[dict[str, Any]],
    desired_count: int,
) -> list[dict[str, Any]]:
    payload = facade_attr("_call_json", _call_json)(
        ai,
        system=(
            "你是长视频短片策划。只能从候选窗口中选择，不得改动边界或编造内容。"
            "优先选择主题完整、有明确开场、独立可理解的片段。返回严格 JSON。"
        ),
        user={
            "project_title": title,
            "desired_count": desired_count,
            "schema": {
                "selections": [
                    {
                        "candidate_key": "1-20",
                        "title": "string",
                        "hook": "string",
                        "reason": "string",
                        "score": 0,
                    }
                ]
            },
            "candidates": windows,
        },
        task_id=task_id,
        max_tokens=4096,
    )
    by_key = {item["candidate_key"]: item for item in windows}
    selected: list[dict[str, Any]] = []
    used: set[str] = set()
    for item in payload.get("selections") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("candidate_key") or "")
        window = by_key.get(key)
        if not window or key in used:
            continue
        used.add(key)
        selected.append(
            {
                **window,
                "title": str(item.get("title") or "精彩片段").strip()[:160],
                "hook": str(item.get("hook") or "").strip(),
                "reason": str(item.get("reason") or "").strip(),
                "score": max(0, min(100, float(item.get("score") or 0))),
            }
        )
        if len(selected) >= desired_count:
            break
    for window in windows:
        if len(selected) >= desired_count:
            break
        if window["candidate_key"] in used:
            continue
        selected.append(
            {
                **window,
                "title": f"片段 {len(selected) + 1}",
                "hook": "",
                "reason": "按主题连续性和自然句界生成的候选",
                "score": 0,
            }
        )
    return selected


def create_clip_set(
    project_id: str,
    name: str,
    desired_count: int,
    min_duration: float,
    max_duration: float,
) -> tuple[str, str]:
    tm = facade_attr("task_manager", task_manager)
    provider_fn = facade_attr("assigned_provider", assigned_provider)
    project, rows = _source(project_id)
    if desired_count not in {3, 5, 10}:
        raise ClipError(
            "候选数量只支持 3、5 或 10", error_code="CLIP_COUNT_INVALID", recoverable=False
        )
    if min_duration < 15 or max_duration > 180 or max_duration <= min_duration:
        raise ClipError(
            "短片推荐时长范围无效", error_code="CLIP_DURATION_INVALID", recoverable=False
        )
    ai = provider_fn("content")
    identifier, now = str(uuid.uuid4()), _now()
    db = get_db()
    try:
        db.execute(
            """INSERT INTO clip_sets(
                   id,project_id,name,source_revision,source_fingerprint,
                   provider_id,model,desired_count,min_duration,max_duration,
                   status,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?,?)""",
            (
                identifier,
                project_id,
                name.strip() or f"{project['title']} 短片",
                int(project.get("edit_revision") or 0),
                _fingerprint(rows),
                ai["provider"],
                ai["model"],
                desired_count,
                min_duration,
                max_duration,
                now,
                now,
            ),
        )
        db.commit()
    finally:
        db.close()
    task_id = tm.create_task(project_id, "clip_recommend", resource_class="network_ai")
    tm.update_task(task_id, details={"clip_set_id": identifier})
    tm.run_background(task_id, recommend_clips, identifier)
    return identifier, task_id


def recommend_clips(task_id: str, clip_set_id: str) -> None:
    tm = facade_attr("task_manager", task_manager)
    provider_fn = facade_attr("assigned_provider", assigned_provider)
    db = get_db()
    try:
        clip_set = db.execute("SELECT * FROM clip_sets WHERE id=?", (clip_set_id,)).fetchone()
    finally:
        db.close()
    if not clip_set:
        raise ClipError("短片集合不存在", error_code="CLIP_SET_NOT_FOUND", recoverable=False)
    clip_set = dict(clip_set)
    project, rows = _source(clip_set["project_id"])
    tm.update_task(
        task_id,
        step="clip_windows",
        progress=12,
        message="正在分析字幕主题边界",
        details={"clip_set_id": clip_set_id},
    )
    windows = _candidate_windows(
        rows, float(clip_set["min_duration"]), float(clip_set["max_duration"])
    )
    if not windows:
        raise ClipError("视频中没有满足时长范围的连续字幕", error_code="CLIP_WINDOW_EMPTY")
    tm.update_task(task_id, step="clip_ranking", progress=35, message="AI 正在排序短片候选")
    ai = provider_fn("content", clip_set.get("provider_id"), clip_set.get("model"))
    try:
        selected = _rank_candidates(
            ai, task_id, project["title"], windows, int(clip_set["desired_count"])
        )
    except TaskCancelled:
        db = get_db()
        try:
            db.execute(
                "UPDATE clip_sets SET status='failed',updated_at=? WHERE id=?",
                (_now(), clip_set_id),
            )
            db.commit()
        finally:
            db.close()
        raise
    now = _now()
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM clip_candidates WHERE clip_set_id=?", (clip_set_id,))
        for order, item in enumerate(selected):
            candidate_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO clip_candidates(
                       id,clip_set_id,title,hook,reason,score,start,end,
                       start_segment_index,end_segment_index,selected,revision,
                       source_confirmed_revision,sort_order,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)""",
                (
                    candidate_id,
                    clip_set_id,
                    item["title"],
                    item["hook"],
                    item["reason"],
                    item["score"],
                    item["start"],
                    item["end"],
                    item["start_index"],
                    item["end_index"],
                    int(order == 0),
                    int(project.get("edit_revision") or 0),
                    order,
                    now,
                    now,
                ),
            )
            for ratio in ("9:16", "1:1", "16:9"):
                db.execute(
                    """INSERT INTO clip_layouts(
                           candidate_id,aspect_ratio,enabled,composition,focal_x,focal_y,
                           subtitle_mode,style_json,revision,updated_at
                       ) VALUES (?,?,?,?,0.5,0.5,'original','{}',0,?)""",
                    (
                        candidate_id,
                        ratio,
                        int(ratio == "9:16"),
                        "crop" if ratio == "16:9" else "blur",
                        now,
                    ),
                )
        db.execute(
            "UPDATE clip_sets SET status='ready',source_revision=?,source_fingerprint=?,updated_at=? WHERE id=?",
            (int(project.get("edit_revision") or 0), _fingerprint(rows), now, clip_set_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    tm.update_task(
        task_id,
        step="clip_recommend_done",
        progress=100,
        message=f"已生成 {len(selected)} 个短片候选",
        details={"clip_set_id": clip_set_id, "candidate_count": len(selected)},
    )


def _candidate_dict(db, row) -> dict[str, Any]:
    item = dict(row)
    item["selected"] = bool(item["selected"])
    item["stale"] = int(
        item["source_confirmed_revision"]
        if item["source_confirmed_revision"] is not None
        else -1
    ) != int(item["current_project_revision"])
    item["layouts"] = [
        {
            **dict(layout),
            "enabled": bool(layout["enabled"]),
            "style": json.loads(layout["style_json"] or "{}"),
        }
        for layout in db.execute(
            "SELECT * FROM clip_layouts WHERE candidate_id=? ORDER BY aspect_ratio DESC",
            (item["id"],),
        ).fetchall()
    ]
    for layout in item["layouts"]:
        layout.pop("style_json", None)
    item["renders"] = [
        dict(render)
        for render in db.execute(
            "SELECT * FROM clip_renders WHERE candidate_id=? ORDER BY updated_at DESC",
            (item["id"],),
        ).fetchall()
    ]
    return item


def get_clip_set(clip_set_id: str) -> dict[str, Any]:
    db = get_db()
    try:
        row = db.execute(
            """SELECT cs.*,p.edit_revision current_project_revision,p.title project_title
                 FROM clip_sets cs JOIN projects p ON p.id=cs.project_id WHERE cs.id=?""",
            (clip_set_id,),
        ).fetchone()
        if not row:
            raise FileNotFoundError("短片集合不存在")
        result = dict(row)
        result["stale"] = int(result["source_revision"]) != int(result["current_project_revision"])
        candidates = db.execute(
            """SELECT c.*,cs.source_revision,p.edit_revision current_project_revision
                 FROM clip_candidates c JOIN clip_sets cs ON cs.id=c.clip_set_id
                 JOIN projects p ON p.id=cs.project_id
                WHERE c.clip_set_id=? ORDER BY c.sort_order""",
            (clip_set_id,),
        ).fetchall()
        result["candidates"] = [_candidate_dict(db, item) for item in candidates]
        return result
    finally:
        db.close()


def list_clip_sets(project_id: str) -> list[dict[str, Any]]:
    db = get_db()
    try:
        return [
            {
                **dict(row),
                "stale": int(row["source_revision"]) != int(row["current_project_revision"]),
            }
            for row in db.execute(
                """SELECT cs.*,p.edit_revision current_project_revision,
                          (SELECT COUNT(*) FROM clip_candidates c WHERE c.clip_set_id=cs.id) candidate_count
                     FROM clip_sets cs JOIN projects p ON p.id=cs.project_id
                    WHERE cs.project_id=? ORDER BY cs.updated_at DESC""",
                (project_id,),
            ).fetchall()
        ]
    finally:
        db.close()


def update_candidate(
    candidate_id: str,
    *,
    title: str,
    start: float,
    end: float,
    selected: bool,
    expected_revision: int,
    confirm_current_source: bool,
) -> dict[str, Any]:
    duration = end - start
    if start < 0 or duration < 15 or duration > 180:
        raise ClipError("人工短片长度必须在 15–180 秒之间", error_code="CLIP_DURATION_INVALID")
    db = get_db()
    try:
        row = db.execute(
            """SELECT c.*,cs.project_id,p.edit_revision
                 FROM clip_candidates c JOIN clip_sets cs ON cs.id=c.clip_set_id
                 JOIN projects p ON p.id=cs.project_id WHERE c.id=?""",
            (candidate_id,),
        ).fetchone()
        if not row:
            raise FileNotFoundError("短片候选不存在")
        if int(row["revision"]) != expected_revision:
            raise ClipError("短片候选已在其他位置更新", error_code="CLIP_REVISION_CONFLICT")
        rows = db.execute(
            "SELECT idx,start,end FROM segments WHERE project_id=? ORDER BY idx",
            (row["project_id"],),
        ).fetchall()
        if not rows or end > float(rows[-1]["end"]) + 0.001:
            raise ClipError("短片范围超出视频字幕", error_code="CLIP_RANGE_INVALID")
        start_index = min(rows, key=lambda item: abs(float(item["start"]) - start))["idx"]
        end_index = min(rows, key=lambda item: abs(float(item["end"]) - end))["idx"]
        confirmed = (
            int(row["edit_revision"]) if confirm_current_source else row["source_confirmed_revision"]
        )
        db.execute(
            """UPDATE clip_candidates
                  SET title=?,start=?,end=?,start_segment_index=?,end_segment_index=?,
                      selected=?,source_confirmed_revision=?,revision=revision+1,updated_at=?
                WHERE id=?""",
            (
                title.strip()[:160],
                start,
                end,
                start_index,
                end_index,
                int(selected),
                confirmed,
                _now(),
                candidate_id,
            ),
        )
        db.commit()
        clip_set_id = row["clip_set_id"]
    finally:
        db.close()
    return get_clip_set(clip_set_id)


def update_layout(
    candidate_id: str,
    aspect_ratio: str,
    *,
    enabled: bool,
    composition: str,
    focal_x: float,
    focal_y: float,
    subtitle_mode: str,
    style: dict[str, Any],
    expected_revision: int,
) -> dict[str, Any]:
    if aspect_ratio not in ASPECT_DIMENSIONS:
        raise ClipError("不支持的画面比例", error_code="CLIP_ASPECT_INVALID", recoverable=False)
    if composition not in {"blur", "crop"} or subtitle_mode not in {
        "off",
        "original",
        "translated",
        "bilingual",
    }:
        raise ClipError("短片布局配置无效", error_code="CLIP_LAYOUT_INVALID", recoverable=False)
    db = get_db()
    try:
        row = db.execute(
            "SELECT revision FROM clip_layouts WHERE candidate_id=? AND aspect_ratio=?",
            (candidate_id, aspect_ratio),
        ).fetchone()
        if not row:
            raise FileNotFoundError("短片布局不存在")
        if int(row["revision"]) != expected_revision:
            raise ClipError("短片布局已在其他位置更新", error_code="CLIP_REVISION_CONFLICT")
        db.execute(
            """UPDATE clip_layouts
                  SET enabled=?,composition=?,focal_x=?,focal_y=?,subtitle_mode=?,
                      style_json=?,revision=revision+1,updated_at=?
                WHERE candidate_id=? AND aspect_ratio=?""",
            (
                int(enabled),
                composition,
                max(0, min(1, focal_x)),
                max(0, min(1, focal_y)),
                subtitle_mode,
                json.dumps(style, ensure_ascii=False),
                _now(),
                candidate_id,
                aspect_ratio,
            ),
        )
        clip_set_id = db.execute(
            "SELECT clip_set_id FROM clip_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0]
        db.commit()
    finally:
        db.close()
    return get_clip_set(clip_set_id)
