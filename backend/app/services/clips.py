"""Subtitle-grounded short-clip recommendations and local FFmpeg rendering."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import tempfile
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..models.database import get_db
from ..utils.config import EXPORTS_DIR
from ..utils.task_manager import TaskCancelled, task_manager
from .ai_providers import assigned_provider
from .content_packs import _call_json
from .ffmpeg_encoding import select_h264_encoder_args
from .runtime_diagnostics import resolve_ffmpeg_path, resolve_ffprobe_path


ASPECT_DIMENSIONS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}


class ClipError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "CLIP_OPERATION_FAILED",
        recoverable: bool = True,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.recoverable = recoverable
        self.available_actions = ["retry"] if recoverable else []
        self.details = details or {}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _source(project_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    db = get_db()
    try:
        project = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        rows = [
            dict(row)
            for row in db.execute(
                """SELECT * FROM segments
                    WHERE project_id=? AND COALESCE(is_draft,0)=0 ORDER BY idx""",
                (project_id,),
            ).fetchall()
        ]
    finally:
        db.close()
    if not project:
        raise FileNotFoundError("项目不存在")
    if not rows:
        raise ClipError("项目还没有可用字幕", error_code="CLIP_SOURCE_EMPTY")
    return dict(project), rows


def _fingerprint(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(
        [[row["idx"], row["start"], row["end"], row.get("clean_text"), row.get("raw_text")] for row in rows],
        ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()


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
                text = (rows[end_position].get("clean_text") or rows[end_position].get("raw_text") or "").strip()
                gap = (
                    float(rows[end_position + 1]["start"]) - float(rows[end_position]["end"])
                    if end_position + 1 < len(rows) else 1
                )
                if gap >= .45 or text.endswith(("。", "！", "？", ".", "!", "?")):
                    break
        duration = float(rows[end_position]["end"]) - float(start["start"])
        if duration < minimum:
            continue
        selected = rows[start_position:end_position + 1]
        windows.append({
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
        })
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
    payload = _call_json(
        ai,
        system=(
            "你是长视频短片策划。只能从候选窗口中选择，不得改动边界或编造内容。"
            "优先选择主题完整、有明确开场、独立可理解的片段。返回严格 JSON。"
        ),
        user={
            "project_title": title,
            "desired_count": desired_count,
            "schema": {
                "selections": [{
                    "candidate_key": "1-20", "title": "string", "hook": "string",
                    "reason": "string", "score": 0,
                }]
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
        selected.append({
            **window,
            "title": str(item.get("title") or "精彩片段").strip()[:160],
            "hook": str(item.get("hook") or "").strip(),
            "reason": str(item.get("reason") or "").strip(),
            "score": max(0, min(100, float(item.get("score") or 0))),
        })
        if len(selected) >= desired_count:
            break
    for window in windows:
        if len(selected) >= desired_count:
            break
        if window["candidate_key"] in used:
            continue
        selected.append({
            **window,
            "title": f"片段 {len(selected) + 1}",
            "hook": "",
            "reason": "按主题连续性和自然句界生成的候选",
            "score": 0,
        })
    return selected


def create_clip_set(
    project_id: str,
    name: str,
    desired_count: int,
    min_duration: float,
    max_duration: float,
) -> tuple[str, str]:
    project, rows = _source(project_id)
    if desired_count not in {3, 5, 10}:
        raise ClipError("候选数量只支持 3、5 或 10", error_code="CLIP_COUNT_INVALID", recoverable=False)
    if min_duration < 15 or max_duration > 180 or max_duration <= min_duration:
        raise ClipError("短片推荐时长范围无效", error_code="CLIP_DURATION_INVALID", recoverable=False)
    ai = assigned_provider("content")
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
                identifier, project_id, name.strip() or f"{project['title']} 短片",
                int(project.get("edit_revision") or 0), _fingerprint(rows),
                ai["provider"], ai["model"], desired_count, min_duration, max_duration,
                now, now,
            ),
        )
        db.commit()
    finally:
        db.close()
    task_id = task_manager.create_task(project_id, "clip_recommend", resource_class="network_ai")
    task_manager.update_task(task_id, details={"clip_set_id": identifier})
    task_manager.run_background(task_id, recommend_clips, identifier)
    return identifier, task_id


def recommend_clips(task_id: str, clip_set_id: str) -> None:
    db = get_db()
    try:
        clip_set = db.execute("SELECT * FROM clip_sets WHERE id=?", (clip_set_id,)).fetchone()
    finally:
        db.close()
    if not clip_set:
        raise ClipError("短片集合不存在", error_code="CLIP_SET_NOT_FOUND", recoverable=False)
    clip_set = dict(clip_set)
    project, rows = _source(clip_set["project_id"])
    task_manager.update_task(
        task_id, step="clip_windows", progress=12, message="正在分析字幕主题边界",
        details={"clip_set_id": clip_set_id},
    )
    windows = _candidate_windows(rows, float(clip_set["min_duration"]), float(clip_set["max_duration"]))
    if not windows:
        raise ClipError("视频中没有满足时长范围的连续字幕", error_code="CLIP_WINDOW_EMPTY")
    task_manager.update_task(task_id, step="clip_ranking", progress=35, message="AI 正在排序短片候选")
    ai = assigned_provider("content", clip_set.get("provider_id"), clip_set.get("model"))
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
                    candidate_id, clip_set_id, item["title"], item["hook"], item["reason"],
                    item["score"], item["start"], item["end"], item["start_index"],
                    item["end_index"], int(order == 0), int(project.get("edit_revision") or 0),
                    order, now, now,
                ),
            )
            for ratio in ("9:16", "1:1", "16:9"):
                db.execute(
                    """INSERT INTO clip_layouts(
                           candidate_id,aspect_ratio,enabled,composition,focal_x,focal_y,
                           subtitle_mode,style_json,revision,updated_at
                       ) VALUES (?,?,?,?,0.5,0.5,'original','{}',0,?)""",
                    (candidate_id, ratio, int(ratio == "9:16"), "crop" if ratio == "16:9" else "blur", now),
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
    task_manager.update_task(
        task_id, step="clip_recommend_done", progress=100, message=f"已生成 {len(selected)} 个短片候选",
        details={"clip_set_id": clip_set_id, "candidate_count": len(selected)},
    )


def _candidate_dict(db, row) -> dict[str, Any]:
    item = dict(row)
    item["selected"] = bool(item["selected"])
    item["stale"] = int(
        item["source_confirmed_revision"]
        if item["source_confirmed_revision"] is not None
        else -1
    ) != int(
        item["current_project_revision"]
    )
    item["layouts"] = [
        {**dict(layout), "enabled": bool(layout["enabled"]), "style": json.loads(layout["style_json"] or "{}")}
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
            {**dict(row), "stale": int(row["source_revision"]) != int(row["current_project_revision"])}
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
        if not rows or end > float(rows[-1]["end"]) + .001:
            raise ClipError("短片范围超出视频字幕", error_code="CLIP_RANGE_INVALID")
        start_index = min(rows, key=lambda item: abs(float(item["start"]) - start))["idx"]
        end_index = min(rows, key=lambda item: abs(float(item["end"]) - end))["idx"]
        confirmed = int(row["edit_revision"]) if confirm_current_source else row["source_confirmed_revision"]
        db.execute(
            """UPDATE clip_candidates
                  SET title=?,start=?,end=?,start_segment_index=?,end_segment_index=?,
                      selected=?,source_confirmed_revision=?,revision=revision+1,updated_at=?
                WHERE id=?""",
            (
                title.strip()[:160], start, end, start_index, end_index, int(selected),
                confirmed, _now(), candidate_id,
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
    if composition not in {"blur", "crop"} or subtitle_mode not in {"off", "original", "translated", "bilingual"}:
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
                int(enabled), composition, max(0, min(1, focal_x)), max(0, min(1, focal_y)),
                subtitle_mode, json.dumps(style, ensure_ascii=False), _now(),
                candidate_id, aspect_ratio,
            ),
        )
        clip_set_id = db.execute(
            "SELECT clip_set_id FROM clip_candidates WHERE id=?", (candidate_id,)
        ).fetchone()[0]
        db.commit()
    finally:
        db.close()
    return get_clip_set(clip_set_id)


def _subtitle_rows(
    candidate: dict[str, Any],
    layout: dict[str, Any],
    project_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    db = get_db()
    try:
        rows = [
            dict(row)
            for row in db.execute(
                """SELECT * FROM segments
                    WHERE project_id=? AND end>? AND start<?
                    ORDER BY idx""",
                (project_id, candidate["start"], candidate["end"]),
            ).fetchall()
        ]
        style_row = db.execute(
            "SELECT settings_json FROM project_styles WHERE project_id=?", (project_id,)
        ).fetchone()
    finally:
        db.close()
    shifted = []
    duration = float(candidate["end"]) - float(candidate["start"])
    for row in rows:
        row["start"] = max(0, float(row["start"]) - float(candidate["start"]))
        row["end"] = min(duration, float(row["end"]) - float(candidate["start"]))
        if row["end"] > row["start"]:
            shifted.append(row)
    style = json.loads(style_row["settings_json"]) if style_row else {}
    style.update(layout.get("style") or {})
    return shifted, style


@lru_cache(maxsize=32)
def _font_path(font_family: str, needs_cjk: bool) -> str:
    requested = font_family.split(",", 1)[0].strip().strip("\"'").lower()
    roots = [
        Path.home() / "Library/Fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
        Path("/System/Library/Fonts/Supplemental"),
    ]
    if needs_cjk:
        cjk_fallbacks = [
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
            Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        ]
        available = next((path for path in cjk_fallbacks if path.is_file()), None)
        if available:
            return str(available)
    if requested:
        token = "".join(character for character in requested if character.isalnum())
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.glob("*"):
                name = "".join(character for character in path.stem.lower() if character.isalnum())
                if token and token in name and path.suffix.lower() in {".ttf", ".ttc", ".otf"}:
                    return str(path)
    fallbacks = [
        Path("/System/Library/Fonts/SFNS.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    return str(next((path for path in fallbacks if path.is_file()), fallbacks[-1]))


def _hex_rgba(value: Any, fallback: str, alpha: int = 255) -> tuple[int, int, int, int]:
    raw = str(value or fallback).strip().lstrip("#")
    try:
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16), alpha
    except (ValueError, IndexError):
        default = fallback.lstrip("#")
        return int(default[0:2], 16), int(default[2:4], 16), int(default[4:6], 16), alpha


def _wrap_caption(draw: Any, text: str, font: Any, maximum: float) -> list[str]:
    text = " ".join(str(text or "").split())
    if not text:
        return []
    units = text.split(" ") if " " in text else list(text)
    separator = " " if " " in text else ""
    lines: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}{separator if current else ''}{unit}"
        if current and draw.textlength(candidate, font=font) > maximum:
            lines.append(current)
            current = unit
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _subtitle_overlay_paths(
    task_id: str,
    candidate: dict[str, Any],
    layout: dict[str, Any],
    project_id: str,
) -> list[dict[str, Any]]:
    """Pre-render captions when the bundled FFmpeg has no libass filter."""
    mode = layout["subtitle_mode"]
    if mode == "off":
        return []
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise ClipError(
            "短片字幕渲染组件缺失",
            error_code="CLIP_SUBTITLE_RUNTIME_MISSING",
            recoverable=False,
        ) from exc
    rows, style = _subtitle_rows(candidate, layout, project_id)
    render_size = layout.get("render_size")
    width, height = (
        (int(render_size[0]), int(render_size[1]))
        if isinstance(render_size, (list, tuple)) and len(render_size) == 2
        else ASPECT_DIMENSIONS[layout["aspect_ratio"]]
    )
    scale = min(width, height) / 1080
    original_size = max(18, round(float(style.get("originalFontSize") or style.get("fontSize") or 46) * scale))
    translated_size = max(16, round(float(style.get("translatedFontSize") or original_size * .82) * scale))
    font_family = str(style.get("fontFamily") or "Arial")
    vertical_position = max(10, min(94, float(style.get("verticalPosition", 88))))
    margin = max(round(height * .055), 28)
    maximum_width = width - margin * 2
    background_mode = str(style.get("backgroundMode") or "none")
    shadow = bool(style.get("shadow", True))
    assets: list[dict[str, Any]] = []
    for row in rows:
        original = str(row.get("clean_text") or row.get("raw_text") or "")
        translated = str(row.get("translated_text") or "")
        lines: list[tuple[str, str]] = []
        if mode == "translated":
            lines.append(("translated", translated or original))
        elif mode == "bilingual" and translated:
            lines.extend((("original", original), ("translated", translated)))
        else:
            lines.append(("original", original))
        needs_cjk = any("\u3400" <= character <= "\u9fff" for _kind, text in lines for character in text)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        fitted_original, fitted_translated = original_size, translated_size
        rendered: list[tuple[str, str, Any]] = []
        dimensions: list[tuple[int, int, int, int]] = []
        maximum_height = height * (.32 if height > width else .28)
        maximum_lines = 5 if height > width else 4
        while True:
            original_font = ImageFont.truetype(
                _font_path(font_family, needs_cjk), fitted_original
            )
            translated_font = ImageFont.truetype(
                _font_path(font_family, needs_cjk), fitted_translated
            )
            rendered = []
            for kind, text in lines:
                font = translated_font if kind == "translated" else original_font
                rendered.extend(
                    (kind, wrapped, font)
                    for wrapped in _wrap_caption(draw, text, font, maximum_width)
                )
            spacing = max(5, round(fitted_original * .18))
            dimensions = [
                draw.textbbox(
                    (0, 0), text, font=font,
                    stroke_width=max(1, round(font.size * .055)),
                )
                for _kind, text, font in rendered
            ]
            measured_height = (
                sum(box[3] - box[1] for box in dimensions)
                + spacing * max(0, len(rendered) - 1)
            )
            if (
                measured_height <= maximum_height
                and len(rendered) <= maximum_lines
            ) or fitted_original <= 20:
                break
            fitted_original = max(20, round(fitted_original * .88))
            fitted_translated = max(18, round(fitted_translated * .88))
        if not rendered:
            continue
        total_height = sum(box[3] - box[1] for box in dimensions) + spacing * (len(rendered) - 1)
        top = min(height - margin - total_height, vertical_position / 100 * height - total_height)
        top = max(margin, top)
        if background_mode in {"black", "white"}:
            widest = max(box[2] - box[0] for box in dimensions)
            padding = max(10, round(original_size * .28))
            fill = (255, 255, 255, 218) if background_mode == "white" else (0, 0, 0, 205)
            draw.rounded_rectangle(
                (
                    (width - widest) / 2 - padding,
                    top - padding,
                    (width + widest) / 2 + padding,
                    top + total_height + padding,
                ),
                radius=padding,
                fill=fill,
            )
        cursor = top
        for (kind, text, font), box in zip(rendered, dimensions):
            line_width = box[2] - box[0]
            line_height = box[3] - box[1]
            color = _hex_rgba(
                style.get("translatedTextColor") if kind == "translated" else style.get("originalTextColor") or style.get("textColor"),
                "#dddddd" if kind == "translated" else "#ffffff",
            )
            stroke_width = max(1, round(font.size * .055))
            draw.text(
                ((width - line_width) / 2, cursor - box[1]),
                text,
                font=font,
                fill=color,
                stroke_width=stroke_width,
                stroke_fill=(0, 0, 0, 225) if shadow or background_mode == "none" else (0, 0, 0, 0),
            )
            cursor += line_height + spacing
        path = Path(tempfile.gettempdir()) / (
            f"subtitle-factory-clip-{task_id}-{candidate['id']}-"
            f"{layout['aspect_ratio'].replace(':','x')}-{row['idx']}.tiff"
        )
        # The bundled minimal FFmpeg intentionally omits the PNG decoder, but
        # includes TIFF with alpha support. Keep overlays lossless and directly
        # decodable by the runtime shipped with the app.
        canvas.save(path, format="TIFF", compression="tiff_lzw")
        assets.append({"path": str(path), "start": row["start"], "end": row["end"]})
    return assets


def _filter_graph(
    width: int,
    height: int,
    layout: dict[str, Any],
    subtitle_path: str | None,
    subtitle_overlays: list[dict[str, Any]] | None = None,
) -> str:
    if layout["composition"] == "passthrough":
        graph = "[0:v]null[base]"
    elif layout["composition"] == "blur":
        graph = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma=24:steps=2[bg2];"
            f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fg2];"
            f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2[base]"
        )
    else:
        x = max(0, min(1, float(layout["focal_x"])))
        y = max(0, min(1, float(layout["focal_y"])))
        graph = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:x='max(0,(in_w-out_w)*{x:.4f})':"
            f"y='max(0,(in_h-out_h)*{y:.4f})'[base]"
        )
    if subtitle_path:
        escaped = subtitle_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\''")
        graph += f";[base]subtitles=filename='{escaped}'[v]"
    elif subtitle_overlays:
        previous = "base"
        for index, overlay in enumerate(subtitle_overlays):
            escaped = str(overlay["path"]).replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\''")
            output = "v" if index == len(subtitle_overlays) - 1 else f"caption{index}"
            graph += (
                f";movie=filename='{escaped}',format=rgba[subtitle{index}];"
                f"[{previous}][subtitle{index}]overlay=0:0:eof_action=repeat:shortest=0:"
                f"enable='between(t,{float(overlay['start']):.3f},{float(overlay['end']):.3f})'[{output}]"
            )
            previous = output
    else:
        graph += ";[base]null[v]"
    return graph


def _run_process(task_id: str, command: list[str], duration: float) -> None:
    del duration
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as error_log:
        process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=error_log, text=True
        )
        stopped = False
        try:
            while process.poll() is None:
                state = task_manager.get_task(task_id) or {}
                if state.get("status") == "paused" and not stopped:
                    os.kill(process.pid, signal.SIGSTOP)
                    stopped = True
                try:
                    task_manager.checkpoint(task_id)
                except TaskCancelled:
                    if stopped:
                        os.kill(process.pid, signal.SIGCONT)
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise
                if stopped:
                    os.kill(process.pid, signal.SIGCONT)
                    stopped = False
                time.sleep(.2)
            if process.returncode:
                error_log.seek(0)
                stderr = error_log.read()[-1000:]
                raise ClipError(
                    f"短片渲染失败：{stderr}",
                    error_code="CLIP_RENDER_FAILED",
                    details={"ffmpeg_tail": stderr},
                )
        finally:
            if process.poll() is None:
                process.kill()


def _probe(path: Path, width: int, height: int, expected_duration: float) -> dict[str, Any]:
    ffprobe = resolve_ffprobe_path()
    if not ffprobe:
        raise ClipError("短片验证缺少 FFprobe", error_code="DOWNLOAD_RUNTIME_MISSING")
    result = subprocess.run(
        [
            str(ffprobe.path), "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode:
        raise ClipError("FFprobe 无法读取短片输出", error_code="CLIP_VALIDATION_FAILED")
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float((payload.get("format") or {}).get("duration") or 0)
    if not video or not audio or int(video.get("width") or 0) != width or int(video.get("height") or 0) != height:
        raise ClipError("短片流或画面尺寸验证失败", error_code="CLIP_VALIDATION_FAILED")
    if abs(duration - expected_duration) > max(.3, 1 / 24):
        raise ClipError("短片时长验证失败", error_code="CLIP_VALIDATION_FAILED")
    return {"width": width, "height": height, "duration": duration, "size": path.stat().st_size}


def _configuration(candidate: dict[str, Any], layout: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    config = {
        "renderer_version": 3,
        "candidate_id": candidate["id"], "start": candidate["start"], "end": candidate["end"],
        "aspect_ratio": layout["aspect_ratio"], "composition": layout["composition"],
        "focal_x": layout["focal_x"], "focal_y": layout["focal_y"],
        "subtitle_mode": layout["subtitle_mode"], "style": layout.get("style") or {},
    }
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), config


def create_render_batch(project_id: str, items: list[dict[str, str]], confirm_stale: bool) -> tuple[str, list[str]]:
    project, _rows = _source(project_id)
    render_ids: list[str] = []
    jobs: list[dict[str, Any]] = []
    db = get_db()
    try:
        for request in items:
            candidate_id, aspect = request["candidate_id"], request["aspect_ratio"]
            row = db.execute(
                """SELECT c.*,cs.project_id,cs.source_revision,p.edit_revision current_project_revision
                     FROM clip_candidates c JOIN clip_sets cs ON cs.id=c.clip_set_id
                     JOIN projects p ON p.id=cs.project_id
                    WHERE c.id=? AND cs.project_id=?""",
                (candidate_id, project_id),
            ).fetchone()
            layout_row = db.execute(
                "SELECT * FROM clip_layouts WHERE candidate_id=? AND aspect_ratio=?",
                (candidate_id, aspect),
            ).fetchone()
            if not row or not layout_row or aspect not in ASPECT_DIMENSIONS:
                raise ClipError("短片候选或布局不存在", error_code="CLIP_NOT_FOUND", recoverable=False)
            candidate = dict(row)
            if int(
                candidate["source_confirmed_revision"]
                if candidate["source_confirmed_revision"] is not None
                else -1
            ) != int(
                candidate["current_project_revision"]
            ):
                if not confirm_stale:
                    raise ClipError(
                        "字幕已更新，请重新预览并确认短片范围",
                        error_code="CLIP_SOURCE_STALE",
                        details={"candidate_id": candidate_id},
                    )
                db.execute(
                    "UPDATE clip_candidates SET source_confirmed_revision=?,updated_at=? WHERE id=?",
                    (candidate["current_project_revision"], _now(), candidate_id),
                )
            layout = {**dict(layout_row), "style": json.loads(layout_row["style_json"] or "{}")}
            fingerprint, _config = _configuration(candidate, layout)
            existing = db.execute(
                """SELECT * FROM clip_renders
                    WHERE configuration_fingerprint=? AND status='success'
                    ORDER BY updated_at DESC LIMIT 1""",
                (fingerprint,),
            ).fetchone()
            if existing and existing["path"] and Path(existing["path"]).is_file():
                render_ids.append(existing["id"])
                continue
            render_id, now = str(uuid.uuid4()), _now()
            db.execute(
                """INSERT INTO clip_renders(
                       id,project_id,candidate_id,aspect_ratio,configuration_fingerprint,
                       status,created_at,updated_at
                   ) VALUES (?,?,?,?,?,'pending',?,?)""",
                (render_id, project_id, candidate_id, aspect, fingerprint, now, now),
            )
            render_ids.append(render_id)
            jobs.append({"render_id": render_id, "candidate": candidate, "layout": layout, "fingerprint": fingerprint})
        db.commit()
    finally:
        db.close()
    if not jobs:
        return "", render_ids
    task_id = task_manager.create_task(project_id, "clip_render_batch", resource_class="ffmpeg")
    task_manager.update_task(task_id, details={
        "clip_set_id": str(jobs[0]["candidate"].get("clip_set_id") or "") if jobs else "",
        "render_ids": list(render_ids),
    })
    db = get_db()
    try:
        db.executemany(
            "UPDATE clip_renders SET task_id=?,updated_at=? WHERE id=?",
            [(task_id, _now(), item["render_id"]) for item in jobs],
        )
        db.commit()
    finally:
        db.close()
    task_manager.run_background(task_id, render_batch, project, jobs)
    return task_id, render_ids


def render_batch(task_id: str, project: dict[str, Any], jobs: list[dict[str, Any]]) -> None:
    video_path = project.get("video_path")
    if not video_path or not Path(video_path).is_file():
        if project.get("source_type") != "youtube" or not project.get("source_url"):
            raise ClipError("视频文件不存在且无法重新下载", error_code="MEDIA_NOT_AVAILABLE")
        task_manager.update_task(
            task_id, step="materialize_video", progress=1,
            message="正在下载并保留本地视频以生成短片",
        )
        from ..api.projects import _do_download
        _do_download(
            task_id, project["id"], project["source_url"], preserve_metadata=True,
            progress_start=2, progress_end=25, completed_progress=25,
            materialization_reason="clip_render",
        )
        project, _rows = _source(project["id"])
        video_path = project.get("video_path")
    if not video_path or not Path(video_path).is_file():
        raise ClipError("本地视频准备失败", error_code="MEDIA_NOT_AVAILABLE")
    ffmpeg = resolve_ffmpeg_path()
    if not ffmpeg:
        raise ClipError("短片渲染缺少 FFmpeg", error_code="DOWNLOAD_RUNTIME_MISSING")
    output_root = Path(EXPORTS_DIR) / "clips" / project["id"]
    output_root.mkdir(parents=True, exist_ok=True)
    video_codec_args, video_codec_name = select_h264_encoder_args(ffmpeg.path)
    successes: list[str] = []
    failures: list[str] = []
    for position, job in enumerate(jobs, 1):
        render_id = job["render_id"]
        candidate, layout = job["candidate"], job["layout"]
        width, height = ASPECT_DIMENSIONS[layout["aspect_ratio"]]
        target = output_root / f"{candidate['id']}-{layout['aspect_ratio'].replace(':','x')}-{job['fingerprint'][:10]}.mp4"
        temporary = target.with_suffix(f".{uuid.uuid4().hex}.part.mp4")
        subtitle_path = None
        subtitle_overlays: list[dict[str, Any]] = []
        db = get_db()
        try:
            db.execute(
                "UPDATE clip_renders SET status='running',updated_at=? WHERE id=?",
                (_now(), render_id),
            )
            db.commit()
        finally:
            db.close()
        try:
            if layout["subtitle_mode"] != "off":
                subtitle_overlays = _subtitle_overlay_paths(
                    task_id, candidate, layout, project["id"]
                )
            filter_graph = _filter_graph(
                width, height, layout, subtitle_path, subtitle_overlays
            )
            command = [
                str(ffmpeg.path), "-ss", f"{float(candidate['start']):.3f}",
                "-i", str(video_path), "-t", f"{float(candidate['end']) - float(candidate['start']):.3f}",
                "-filter_complex", filter_graph, "-map", "[v]", "-map", "0:a?",
                *video_codec_args,
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", "-y", str(temporary),
            ]
            task_manager.update_task(
                task_id,
                step="clip_rendering",
                progress=25 + (position - 1) / len(jobs) * 70,
                message=f"正在渲染短片 {position}/{len(jobs)} · {layout['aspect_ratio']}",
                details={"render_id": render_id, "candidate_id": candidate["id"], "aspect_ratio": layout["aspect_ratio"], "video_codec": video_codec_name},
            )
            _run_process(task_id, command, float(candidate["end"]) - float(candidate["start"]))
            metadata = _probe(
                temporary, width, height, float(candidate["end"]) - float(candidate["start"])
            )
            digest = hashlib.sha256()
            with temporary.open("rb") as rendered:
                for chunk in iter(lambda: rendered.read(1024 * 1024), b""):
                    digest.update(chunk)
            checksum = digest.hexdigest()
            os.replace(temporary, target)
            db = get_db()
            try:
                db.execute(
                    """UPDATE clip_renders
                          SET path=?,status='success',error=NULL,width=?,height=?,duration=?,
                              size=?,checksum=?,updated_at=? WHERE id=?""",
                    (
                        str(target), metadata["width"], metadata["height"], metadata["duration"],
                        metadata["size"], checksum, _now(), render_id,
                    ),
                )
                db.commit()
            finally:
                db.close()
            successes.append(render_id)
        except TaskCancelled:
            temporary.unlink(missing_ok=True)
            db = get_db()
            try:
                db.execute(
                    "UPDATE clip_renders SET status='cancelled',updated_at=? WHERE id=?",
                    (_now(), render_id),
                )
                remaining_ids = [
                    pending["render_id"] for pending in jobs[position:]
                ]
                if remaining_ids:
                    placeholders = ",".join("?" for _ in remaining_ids)
                    db.execute(
                        f"""UPDATE clip_renders
                               SET status='cancelled',updated_at=?
                             WHERE status='pending' AND id IN ({placeholders})""",
                        (_now(), *remaining_ids),
                    )
                db.commit()
            finally:
                db.close()
            raise
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            failures.append(render_id)
            db = get_db()
            try:
                db.execute(
                    "UPDATE clip_renders SET status='failed',error=?,updated_at=? WHERE id=?",
                    (str(exc)[:1000], _now(), render_id),
                )
                db.commit()
            finally:
                db.close()
            task_manager.add_log(
                task_id, "warning", "clip_rendering", "一个短片输出失败",
                detail=str(exc), suggestion="已成功的其他比例仍可下载，可单独重试失败项",
            )
        finally:
            if subtitle_path:
                Path(subtitle_path).unlink(missing_ok=True)
            for overlay in subtitle_overlays:
                Path(overlay["path"]).unlink(missing_ok=True)
    task_manager.update_task(
        task_id, step="clip_render_done", progress=100,
        status="partial" if failures else "running",
        message=f"短片渲染完成：{len(successes)} 成功，{len(failures)} 失败",
        details={"successful_renders": successes, "failed_renders": failures},
    )


def get_render(render_id: str) -> dict[str, Any]:
    db = get_db()
    try:
        row = db.execute("SELECT * FROM clip_renders WHERE id=?", (render_id,)).fetchone()
        if not row:
            raise FileNotFoundError("短片输出不存在")
        return dict(row)
    finally:
        db.close()


def delete_render(render_id: str) -> bool:
    db = get_db()
    try:
        row = db.execute("SELECT path FROM clip_renders WHERE id=?", (render_id,)).fetchone()
        if not row:
            return False
        if row["path"]:
            Path(row["path"]).unlink(missing_ok=True)
        db.execute("DELETE FROM clip_renders WHERE id=?", (render_id,))
        db.commit()
        return True
    finally:
        db.close()
