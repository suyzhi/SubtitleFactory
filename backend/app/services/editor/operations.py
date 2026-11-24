"""Low-level segment editing operations, timeline validation, and snapshot helpers."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from ...models.schemas import SegmentOperationRequest

SEGMENT_COLUMNS = (
    "id",
    "project_id",
    "idx",
    "start",
    "end",
    "raw_text",
    "clean_text",
    "translated_text",
    "speaker",
    "speaker_id",
    "locked",
    "is_draft",
    "source_stage",
    "transcription_run_id",
)


@dataclass
class EditorServiceError(Exception):
    status_code: int
    code: str
    message: str
    suggestion: str = ""
    details: dict | None = None

    def as_detail(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
            "details": self.details or {},
            "recoverable": self.status_code < 500,
        }


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _now_milliseconds() -> str:
    return datetime.now().isoformat(sep=" ", timespec="milliseconds")


def _project_revision(conn, project_id: str) -> int:
    row = conn.execute(
        "SELECT edit_revision FROM projects WHERE id=? AND deleted_at IS NULL",
        (project_id,),
    ).fetchone()
    if not row:
        raise EditorServiceError(404, "PROJECT_NOT_FOUND", "项目不存在")
    return int(row["edit_revision"] or 0)


def _rows(conn, project_id: str):
    return conn.execute(
        "SELECT * FROM segments WHERE project_id=? ORDER BY idx", (project_id,)
    ).fetchall()


def _snapshot(rows: Iterable) -> list[dict]:
    result = []
    for row in rows:
        keys = set(row.keys())
        result.append({name: row[name] if name in keys else None for name in SEGMENT_COLUMNS})
    return result


def _restore_snapshot(conn, project_id: str, snapshot: list[dict]) -> None:
    conn.execute("DELETE FROM segments WHERE project_id=?", (project_id,))
    placeholders = ",".join("?" for _ in SEGMENT_COLUMNS)
    columns = ",".join(SEGMENT_COLUMNS)
    rows = []
    for item in snapshot:
        values = [item.get(name) for name in SEGMENT_COLUMNS]
        values[1] = project_id
        rows.append(values)
    if rows:
        conn.executemany(f"INSERT INTO segments ({columns}) VALUES ({placeholders})", rows)


def _renumber(conn, project_id: str) -> None:
    conn.execute(
        """WITH ranked AS (
               SELECT id, ROW_NUMBER() OVER (ORDER BY idx, start, id) AS rn
                 FROM segments WHERE project_id=?
           )
           UPDATE segments
              SET idx = (SELECT rn FROM ranked WHERE ranked.id = segments.id)
            WHERE project_id=?""",
        (project_id, project_id),
    )


def _validate_timeline(conn, project_id: str) -> None:
    previous = None
    for row in _rows(conn, project_id):
        start, end = float(row["start"]), float(row["end"])
        if start < 0 or end <= start:
            raise EditorServiceError(
                422,
                "INVALID_TIME_RANGE",
                f"第 {row['idx']} 条字幕时间范围无效",
                "请确保开始时间非负且结束时间晚于开始时间",
            )
        if previous is not None and float(previous["end"]) > start + 1e-6:
            raise EditorServiceError(
                422,
                "SEGMENT_OVERLAP",
                f"第 {previous['idx']} 与 {row['idx']} 条字幕发生重叠",
                "请先调整相邻字幕边界",
            )
        previous = row


def _speaker(conn, project_id: str, speaker_id: str | None) -> tuple[str | None, str]:
    if speaker_id is None:
        return None, ""
    row = conn.execute(
        "SELECT id,name FROM speakers WHERE id=? AND project_id=?", (speaker_id, project_id)
    ).fetchone()
    if not row:
        raise EditorServiceError(422, "SPEAKER_NOT_FOUND", "所选说话人不存在")
    return row["id"], row["name"]


def _language_join(values: Iterable[str]) -> str:
    output = ""
    for raw in values:
        value = (raw or "").strip()
        if not value:
            continue
        if not output:
            output = value
        elif re.search(r"[\u3400-\u9fff]$", output) or re.match(
            r"^[\u3400-\u9fff，。！？；：、]", value
        ):
            output += value
        else:
            output += " " + value
    return output


def _remember_human_translations(
    conn, project_id: str, before: list[dict], after: list[dict]
) -> None:
    project = conn.execute(
        "SELECT language,target_language FROM projects WHERE id=?", (project_id,)
    ).fetchone()
    if not project:
        return
    previous = {item["id"]: item for item in before}
    for item in after:
        old = previous.get(item["id"])
        target = (item.get("translated_text") or "").strip()
        if not target or (old and (old.get("translated_text") or "").strip() == target):
            continue
        source = (item.get("clean_text") or item.get("raw_text") or "").strip()
        if not source:
            continue
        normalized = re.sub(r"\s+", " ", source).strip().casefold()
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        conn.execute(
            """INSERT INTO translation_memory
               (id,source_hash,source_language,target_language,source_text,target_text,origin,confirmed,use_count,created_at,updated_at)
               VALUES (lower(hex(randomblob(16))),?,?,?,?,?,'human',1,0,?,?)
               ON CONFLICT(source_hash,source_language,target_language,target_text) DO UPDATE SET
               confirmed=1,origin='human',updated_at=excluded.updated_at""",
            (
                digest,
                project["language"] or "auto",
                project["target_language"] or "zh",
                source,
                target,
                _now(),
                _now(),
            ),
        )


def _split_offset(text: str, ratio: float, preferred: int | None = None) -> int:
    if not text:
        return 0
    if preferred is not None:
        return max(0, min(len(text), preferred))
    target = max(1, min(len(text) - 1, round(len(text) * ratio)))
    if re.search(r"\s", text):
        candidates = [match.start() for match in re.finditer(r"\s+", text)]
        if candidates:
            return min(candidates, key=lambda value: abs(value - target))
    return target


def _split_text(text: str, ratio: float, preferred: int | None = None) -> tuple[str, str]:
    offset = _split_offset(text or "", ratio, preferred)
    return (text[:offset].rstrip(), text[offset:].lstrip())


def _apply_update_many(conn, project_id: str, request: SegmentOperationRequest) -> tuple[int, bool]:
    affected = 0
    time_changed = False
    for item in request.items:
        row = conn.execute(
            "SELECT * FROM segments WHERE project_id=? AND idx=?",
            (project_id, item.index),
        ).fetchone()
        if not row:
            raise EditorServiceError(404, "SEGMENT_NOT_FOUND", f"第 {item.index} 条字幕不存在")
        values = item.model_dump(exclude={"index"}, exclude_unset=True)
        if row["locked"] and not request.include_locked and set(values) != {"locked"}:
            continue
        if "speaker_id" in values:
            values["speaker_id"], values["speaker"] = _speaker(
                conn, project_id, values["speaker_id"]
            )
        if not values:
            continue
        time_changed = time_changed or "start" in values or "end" in values
        assignments = ",".join(f"{key}=?" for key in values)
        conn.execute(
            f"UPDATE segments SET {assignments} WHERE id=?",
            [*values.values(), row["id"]],
        )
        affected += 1
    return affected, time_changed


def _apply_replace(conn, project_id: str, request: SegmentOperationRequest) -> tuple[int, bool]:
    if not request.search:
        raise EditorServiceError(422, "EMPTY_SEARCH", "查找内容不能为空")
    affected = 0
    pattern = re.compile(re.escape(request.search), 0 if request.match_case else re.IGNORECASE)
    replacement = request.replacement
    for row in _rows(conn, project_id):
        if row["locked"] and not request.include_locked:
            continue
        updates = {}
        for field in request.fields:
            value = row[field] or ""
            replaced = pattern.sub(lambda _match: replacement, value)
            if replaced != value:
                updates[field] = replaced
        if updates:
            assignments = ",".join(f"{key}=?" for key in updates)
            conn.execute(
                f"UPDATE segments SET {assignments} WHERE id=?",
                [*updates.values(), row["id"]],
            )
            affected += 1
    return affected, False


def _apply_shift(conn, project_id: str, request: SegmentOperationRequest) -> tuple[int, bool]:
    selected = set(request.indices)
    clauses = ["project_id=?"]
    values: list = [request.delta, request.delta, project_id]
    if selected:
        placeholders = ",".join("?" for _ in selected)
        clauses.append(f"idx IN ({placeholders})")
        values.extend(sorted(selected))
    if not request.include_locked:
        clauses.append("COALESCE(locked,0)=0")
    cursor = conn.execute(
        f"UPDATE segments SET start=start+?,end=end+? WHERE {' AND '.join(clauses)}",
        values,
    )
    return cursor.rowcount, True


def _apply_split(conn, project_id: str, request: SegmentOperationRequest) -> tuple[int, bool]:
    if request.split_index is None or request.split_at is None:
        raise EditorServiceError(422, "SPLIT_ARGUMENTS_REQUIRED", "拆分需要字幕序号和拆分时间")
    row = conn.execute(
        "SELECT * FROM segments WHERE project_id=? AND idx=?",
        (project_id, request.split_index),
    ).fetchone()
    if not row:
        raise EditorServiceError(404, "SEGMENT_NOT_FOUND", "要拆分的字幕不存在")
    if row["locked"] and not request.include_locked:
        raise EditorServiceError(409, "SEGMENT_LOCKED", "锁定字幕不能拆分")
    start, end = float(row["start"]), float(row["end"])
    if not start < request.split_at < end:
        raise EditorServiceError(422, "INVALID_SPLIT_POINT", "拆分点必须位于字幕时间范围内部")
    ratio = (request.split_at - start) / (end - start)
    raw_left, raw_right = _split_text(row["raw_text"] or "", ratio)
    clean_left, clean_right = _split_text(row["clean_text"] or "", ratio, request.text_offset)
    translated_left, translated_right = _split_text(row["translated_text"] or "", ratio)
    conn.execute(
        "UPDATE segments SET idx=idx+1 WHERE project_id=? AND idx>?", (project_id, row["idx"])
    )
    conn.execute(
        "UPDATE segments SET end=?,raw_text=?,clean_text=?,translated_text=? WHERE id=?",
        (request.split_at, raw_left, clean_left, translated_left, row["id"]),
    )
    values = dict(row)
    values.update(
        {
            "id": str(uuid.uuid4()),
            "idx": row["idx"] + 1,
            "start": request.split_at,
            "raw_text": raw_right,
            "clean_text": clean_right,
            "translated_text": translated_right,
        }
    )
    placeholders = ",".join("?" for _ in SEGMENT_COLUMNS)
    conn.execute(
        f"INSERT INTO segments ({','.join(SEGMENT_COLUMNS)}) VALUES ({placeholders})",
        [values.get(name) for name in SEGMENT_COLUMNS],
    )
    return 2, True


def _apply_merge(conn, project_id: str, request: SegmentOperationRequest) -> tuple[int, bool]:
    indices = sorted(set(request.indices))
    if len(indices) < 2 or indices != list(range(indices[0], indices[-1] + 1)):
        raise EditorServiceError(422, "NON_ADJACENT_MERGE", "只能合并两个或更多相邻字幕")
    placeholders = ",".join("?" for _ in indices)
    rows = conn.execute(
        f"SELECT * FROM segments WHERE project_id=? AND idx IN ({placeholders}) ORDER BY idx",
        [project_id, *indices],
    ).fetchall()
    if len(rows) != len(indices):
        raise EditorServiceError(404, "SEGMENT_NOT_FOUND", "部分待合并字幕不存在")
    if any(row["locked"] for row in rows) and not request.include_locked:
        raise EditorServiceError(409, "SEGMENT_LOCKED", "锁定字幕不能合并")
    speaker_ids = {row["speaker_id"] for row in rows}
    speakers = {row["speaker"] for row in rows}
    first = rows[0]
    merged_speaker_id = next(iter(speaker_ids)) if len(speaker_ids) == 1 else None
    merged_speaker = next(iter(speakers)) if len(speakers) == 1 else ""
    conn.execute(
        """UPDATE segments SET end=?,raw_text=?,clean_text=?,translated_text=?,
                  speaker_id=?,speaker=?,locked=? WHERE id=?""",
        (
            rows[-1]["end"],
            _language_join(row["raw_text"] for row in rows),
            _language_join(row["clean_text"] for row in rows),
            _language_join(row["translated_text"] for row in rows),
            merged_speaker_id,
            merged_speaker,
            int(all(row["locked"] for row in rows)),
            first["id"],
        ),
    )
    conn.execute(
        f"DELETE FROM segments WHERE project_id=? AND idx IN ({placeholders}) AND id<>?",
        [project_id, *indices, first["id"]],
    )
    _renumber(conn, project_id)
    return len(rows), True


def _apply_delete(conn, project_id: str, request: SegmentOperationRequest) -> tuple[int, bool]:
    indices = sorted(set(request.indices))
    if not indices:
        raise EditorServiceError(422, "SEGMENTS_REQUIRED", "请选择要删除的字幕")
    placeholders = ",".join("?" for _ in indices)
    rows = conn.execute(
        f"SELECT * FROM segments WHERE project_id=? AND idx IN ({placeholders}) ORDER BY idx",
        [project_id, *indices],
    ).fetchall()
    if len(rows) != len(indices):
        raise EditorServiceError(404, "SEGMENT_NOT_FOUND", "部分待删除字幕不存在")
    if any(row["locked"] for row in rows) and not request.include_locked:
        raise EditorServiceError(409, "SEGMENT_LOCKED", "锁定字幕不能删除")
    conn.execute(
        f"DELETE FROM segments WHERE project_id=? AND idx IN ({placeholders})",
        [project_id, *indices],
    )
    _renumber(conn, project_id)
    return len(rows), False


def _apply_assign_speaker(
    conn, project_id: str, request: SegmentOperationRequest
) -> tuple[int, bool]:
    speaker_id, name = _speaker(conn, project_id, request.speaker_id)
    selected = set(request.indices)
    if not selected:
        raise EditorServiceError(422, "SEGMENTS_REQUIRED", "请选择要指定说话人的字幕")
    placeholders = ",".join("?" for _ in selected)
    clauses = [f"idx IN ({placeholders})", "project_id=?"]
    values: list = [speaker_id, name, *sorted(selected), project_id]
    if not request.include_locked:
        clauses.append("COALESCE(locked,0)=0")
    cursor = conn.execute(
        f"UPDATE segments SET speaker_id=?,speaker=? WHERE {' AND '.join(clauses)}",
        values,
    )
    return cursor.rowcount, False


APPLIERS = {
    "update_many": _apply_update_many,
    "replace": _apply_replace,
    "shift": _apply_shift,
    "split": _apply_split,
    "merge": _apply_merge,
    "assign_speaker": _apply_assign_speaker,
    "delete": _apply_delete,
}
