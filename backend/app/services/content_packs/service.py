"""Content pack persistence, generation, section editing, and regeneration."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from ...models.database import get_db
from ...utils.task_manager import TaskCancelled, task_manager
from ..ai_providers import assigned_provider
from .prompts import (
    SECTION_ORDER,
    SECTION_TITLES,
    ContentPackError,
    _call_json,
    _compact_source,
    _now,
    _project_source,
    _section_prompt,
    _validate_section,
    facade_attr,
)


def _load_pack(pack_id: str) -> dict[str, Any]:
    db = get_db()
    try:
        row = db.execute(
            """SELECT cp.*,p.edit_revision current_project_revision,p.title project_title
                 FROM content_packs cp JOIN projects p ON p.id=cp.project_id
                WHERE cp.id=?""",
            (pack_id,),
        ).fetchone()
        if not row:
            raise FileNotFoundError("内容发布包不存在")
        result = dict(row)
        result["stale"] = int(result["source_revision"]) != int(result["current_project_revision"])
        result["sections"] = [
            {**dict(item), "content": json.loads(item["content_json"] or "{}")}
            for item in db.execute(
                "SELECT * FROM content_pack_sections WHERE pack_id=? ORDER BY sort_order",
                (pack_id,),
            ).fetchall()
        ]
        for section in result["sections"]:
            section.pop("content_json", None)
        return result
    finally:
        db.close()


def list_content_packs(project_id: str) -> list[dict[str, Any]]:
    db = get_db()
    try:
        return [
            {
                **dict(row),
                "stale": int(row["source_revision"]) != int(row["current_project_revision"]),
            }
            for row in db.execute(
                """SELECT cp.*,p.edit_revision current_project_revision,
                          (SELECT COUNT(*) FROM content_pack_sections s
                            WHERE s.pack_id=cp.id AND s.status='failed') failed_sections
                     FROM content_packs cp JOIN projects p ON p.id=cp.project_id
                    WHERE cp.project_id=? ORDER BY cp.updated_at DESC""",
                (project_id,),
            ).fetchall()
        ]
    finally:
        db.close()


def create_content_pack(
    project_id: str,
    name: str,
    input_mode: str,
    output_language: str,
    allow_translation_fallback: bool,
) -> tuple[dict[str, Any], str]:
    tm = facade_attr("task_manager", task_manager)
    provider_fn = facade_attr("assigned_provider", assigned_provider)
    project, source, _coverage = _project_source(
        project_id, input_mode, allow_translation_fallback=allow_translation_fallback
    )
    ai = provider_fn("content")
    pack_id, now = str(uuid.uuid4()), _now()
    fingerprint = hashlib.sha256(
        json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    db = get_db()
    try:
        db.execute(
            """INSERT INTO content_packs(
                   id,project_id,name,input_mode,output_language,allow_translation_fallback,source_revision,
                   source_fingerprint,provider_id,model,status,revision,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,'pending',0,?,?)""",
            (
                pack_id,
                project_id,
                name.strip() or f"{project['title']} 发布包",
                input_mode,
                output_language,
                int(allow_translation_fallback),
                int(project.get("edit_revision") or 0),
                fingerprint,
                ai["provider"],
                ai["model"],
                now,
                now,
            ),
        )
        for order, kind in enumerate(SECTION_ORDER):
            db.execute(
                """INSERT INTO content_pack_sections(
                       id,pack_id,kind,title,content_json,status,sort_order,updated_at
                   ) VALUES (?,?,?,?,?,'pending',?,?)""",
                (str(uuid.uuid4()), pack_id, kind, SECTION_TITLES[kind], "{}", order, now),
            )
        db.commit()
    finally:
        db.close()
    task_id = tm.create_task(project_id, "content_generate", resource_class="network_ai")
    tm.update_task(
        task_id,
        details={
            "content_pack_id": pack_id,
            "content_section": None,
        },
    )
    tm.run_background(task_id, generate_content_pack, pack_id, allow_translation_fallback, None)
    return _load_pack(pack_id), task_id


def generate_content_pack(
    task_id: str,
    pack_id: str,
    allow_translation_fallback: bool | None = None,
    only_kind: str | None = None,
) -> None:
    tm = facade_attr("task_manager", task_manager)
    provider_fn = facade_attr("assigned_provider", assigned_provider)
    call_fn = facade_attr("_call_json", _call_json)

    pack = _load_pack(pack_id)
    if allow_translation_fallback is None:
        allow_translation_fallback = bool(pack.get("allow_translation_fallback"))
    project, source, coverage = _project_source(
        pack["project_id"],
        pack["input_mode"],
        allow_translation_fallback=allow_translation_fallback,
    )
    ai = provider_fn("content", pack.get("provider_id"), pack.get("model"))
    compact = _compact_source(ai, source, task_id)
    kinds = [only_kind] if only_kind else list(SECTION_ORDER)
    failed: list[str] = []
    db = get_db()
    try:
        db.execute(
            "UPDATE content_packs SET status='generating',updated_at=? WHERE id=?",
            (_now(), pack_id),
        )
        if only_kind:
            db.execute(
                "UPDATE content_pack_sections SET status='generating',error=NULL,updated_at=? WHERE pack_id=? AND kind=?",
                (_now(), pack_id, only_kind),
            )
        else:
            db.execute(
                "UPDATE content_pack_sections SET status='generating',error=NULL,updated_at=? WHERE pack_id=?",
                (_now(), pack_id),
            )
        db.commit()
    finally:
        db.close()
    for position, kind in enumerate(kinds, 1):
        if kind not in SECTION_ORDER:
            raise ContentPackError(
                "未知内容区域", error_code="CONTENT_SECTION_INVALID", recoverable=False
            )
        tm.update_task(
            task_id,
            step=f"content_{kind}",
            progress=35 + position / len(kinds) * 60,
            message=f"正在生成{SECTION_TITLES[kind]}",
            details={"pack_id": pack_id, "section": kind, "translation_coverage": coverage},
        )
        try:
            system, schema = _section_prompt(kind)
            payload = call_fn(
                ai,
                system=system,
                user={
                    "project_title": project["title"],
                    "output_language": pack["output_language"],
                    "input_mode": pack["input_mode"],
                    "schema": schema,
                    "source": compact,
                },
                task_id=task_id,
            )
            content = _validate_section(kind, payload, source)
            db = get_db()
            try:
                db.execute(
                    """UPDATE content_pack_sections
                          SET content_json=?,status='ready',error=NULL,
                              revision=revision+1,generated_at=?,updated_at=?
                        WHERE pack_id=? AND kind=?""",
                    (json.dumps(content, ensure_ascii=False), _now(), _now(), pack_id, kind),
                )
                db.commit()
            finally:
                db.close()
        except TaskCancelled:
            db = get_db()
            try:
                db.execute(
                    """UPDATE content_pack_sections
                          SET status='failed',error='生成已取消',updated_at=?
                        WHERE pack_id=? AND kind=?""",
                    (_now(), pack_id, kind),
                )
                ready_count = int(
                    db.execute(
                        "SELECT COUNT(*) FROM content_pack_sections WHERE pack_id=? AND status='ready'",
                        (pack_id,),
                    ).fetchone()[0]
                )
                db.execute(
                    "UPDATE content_packs SET status=?,updated_at=? WHERE id=?",
                    ("partial" if ready_count else "failed", _now(), pack_id),
                )
                db.commit()
            finally:
                db.close()
            raise
        except Exception as exc:
            failed.append(kind)
            db = get_db()
            try:
                db.execute(
                    """UPDATE content_pack_sections
                          SET status='failed',error=?,updated_at=?
                        WHERE pack_id=? AND kind=?""",
                    (str(exc)[:500], _now(), pack_id, kind),
                )
                db.commit()
            finally:
                db.close()
            tm.add_log(
                task_id,
                "warning",
                f"content_{kind}",
                f"{SECTION_TITLES[kind]}生成失败",
                detail=str(exc),
                suggestion="可只重试这个区域",
            )
    db = get_db()
    try:
        status = "partial" if failed else "ready"
        db.execute(
            """UPDATE content_packs
                  SET status=?,source_revision=?,source_fingerprint=?,
                      revision=revision+1,updated_at=?
                WHERE id=?""",
            (
                status,
                int(project.get("edit_revision") or 0),
                hashlib.sha256(json.dumps(source, ensure_ascii=False).encode()).hexdigest(),
                _now(),
                pack_id,
            ),
        )
        db.commit()
    finally:
        db.close()
    tm.update_task(
        task_id,
        step="content_done",
        progress=100,
        status="partial" if failed else "running",
        message="内容发布包部分完成" if failed else "内容发布包已生成",
        details={"pack_id": pack_id, "failed_sections": failed},
    )


def update_pack(pack_id: str, name: str, expected_revision: int) -> dict[str, Any]:
    db = get_db()
    try:
        row = db.execute("SELECT revision FROM content_packs WHERE id=?", (pack_id,)).fetchone()
        if not row:
            raise FileNotFoundError("内容发布包不存在")
        if int(row["revision"]) != expected_revision:
            raise ContentPackError("内容发布包已在其他位置更新", error_code="CONTENT_REVISION_CONFLICT")
        db.execute(
            "UPDATE content_packs SET name=?,revision=revision+1,updated_at=? WHERE id=?",
            (name.strip(), _now(), pack_id),
        )
        db.commit()
    finally:
        db.close()
    return _load_pack(pack_id)


def update_section(
    pack_id: str,
    kind: str,
    content: dict[str, Any],
    expected_revision: int,
) -> dict[str, Any]:
    db = get_db()
    try:
        row = db.execute(
            "SELECT revision FROM content_pack_sections WHERE pack_id=? AND kind=?",
            (pack_id, kind),
        ).fetchone()
        if not row:
            raise FileNotFoundError("内容区域不存在")
        if int(row["revision"]) != expected_revision:
            raise ContentPackError("内容区域已在其他位置更新", error_code="CONTENT_REVISION_CONFLICT")
        db.execute(
            """UPDATE content_pack_sections
                  SET content_json=?,status='ready',error=NULL,
                      revision=revision+1,updated_at=?
                WHERE pack_id=? AND kind=?""",
            (json.dumps(content, ensure_ascii=False), _now(), pack_id, kind),
        )
        db.execute(
            "UPDATE content_packs SET revision=revision+1,updated_at=? WHERE id=?",
            (_now(), pack_id),
        )
        db.commit()
    finally:
        db.close()
    return _load_pack(pack_id)


def regenerate_section(pack_id: str, kind: str) -> str:
    tm = facade_attr("task_manager", task_manager)
    pack = _load_pack(pack_id)
    if kind not in SECTION_ORDER:
        raise ContentPackError("未知内容区域", error_code="CONTENT_SECTION_INVALID", recoverable=False)
    task_id = tm.create_task(pack["project_id"], "content_generate", resource_class="network_ai")
    tm.update_task(
        task_id,
        details={
            "content_pack_id": pack_id,
            "content_section": kind,
        },
    )
    tm.run_background(task_id, generate_content_pack, pack_id, None, kind)
    return task_id


def delete_pack(pack_id: str) -> bool:
    db = get_db()
    try:
        cursor = db.execute("DELETE FROM content_packs WHERE id=?", (pack_id,))
        db.commit()
        return bool(cursor.rowcount)
    finally:
        db.close()
