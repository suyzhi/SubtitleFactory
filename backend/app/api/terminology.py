"""Project/global glossaries and translation-memory endpoints."""

from __future__ import annotations

import csv
import io
import time
import uuid

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..models.database import get_db
from ..services.terminology import fuzzy_memory, normalize_source


router = APIRouter(prefix="/api")


class GlossaryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    project_id: str | None = None
    source_language: str = "auto"
    target_language: str = "zh"


class TermInput(BaseModel):
    source_text: str = Field(min_length=1)
    target_text: str = ""
    case_sensitive: bool = False
    whole_word: bool = True
    do_not_translate: bool = False
    note: str = ""


class TermImport(BaseModel):
    content: str
    delimiter: str = Field(default=",", pattern="^(,|\\t)$")
    commit: bool = False


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


@router.get("/glossaries")
def list_glossaries(project_id: str | None = None):
    db = get_db()
    try:
        if project_id:
            rows = db.execute(
                """SELECT g.*,(SELECT COUNT(*) FROM glossary_terms t WHERE t.glossary_id=g.id) term_count
                   FROM glossaries g WHERE g.project_id IS NULL OR g.project_id=? ORDER BY g.project_id DESC,g.name""",
                (project_id,),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT g.*,(SELECT COUNT(*) FROM glossary_terms t WHERE t.glossary_id=g.id) term_count
                   FROM glossaries g ORDER BY g.project_id DESC,g.name"""
            ).fetchall()
        return {"glossaries": [dict(row) for row in rows]}
    finally:
        db.close()


@router.post("/glossaries", status_code=201)
def create_glossary(request: GlossaryCreate):
    identifier, now = str(uuid.uuid4()), _now()
    db = get_db()
    try:
        if request.project_id and not db.execute("SELECT 1 FROM projects WHERE id=?", (request.project_id,)).fetchone():
            raise HTTPException(404, "项目不存在")
        db.execute(
            "INSERT INTO glossaries VALUES (?,?,?,?,?,?,?)",
            (identifier, request.project_id, request.name.strip(), request.source_language, request.target_language, now, now),
        )
        db.commit()
        return {"id": identifier, **request.model_dump()}
    finally:
        db.close()


@router.delete("/glossaries/{glossary_id}")
def delete_glossary(glossary_id: str):
    db = get_db()
    try:
        cursor = db.execute("DELETE FROM glossaries WHERE id=?", (glossary_id,))
        db.commit()
        if not cursor.rowcount:
            raise HTTPException(404, "术语表不存在")
        return {"deleted": True}
    finally:
        db.close()


@router.get("/glossaries/{glossary_id}/terms")
def list_terms(glossary_id: str):
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM glossary_terms WHERE glossary_id=? ORDER BY source_text", (glossary_id,)).fetchall()
        return {"terms": [{**dict(row), "case_sensitive": bool(row["case_sensitive"]), "whole_word": bool(row["whole_word"]), "do_not_translate": bool(row["do_not_translate"])} for row in rows]}
    finally:
        db.close()


@router.post("/glossaries/{glossary_id}/terms", status_code=201)
def add_term(glossary_id: str, request: TermInput):
    identifier, now = str(uuid.uuid4()), _now()
    db = get_db()
    try:
        if not db.execute("SELECT 1 FROM glossaries WHERE id=?", (glossary_id,)).fetchone():
            raise HTTPException(404, "术语表不存在")
        duplicate = db.execute(
            "SELECT id FROM glossary_terms WHERE glossary_id=? AND lower(source_text)=lower(?)",
            (glossary_id, request.source_text.strip()),
        ).fetchone()
        if duplicate:
            raise HTTPException(409, detail={"code": "TERM_CONFLICT", "message": "源词已存在", "details": {"term_id": duplicate["id"]}})
        db.execute(
            """INSERT INTO glossary_terms
               (id,glossary_id,source_text,target_text,case_sensitive,whole_word,do_not_translate,note,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (identifier, glossary_id, request.source_text.strip(), request.target_text.strip(), int(request.case_sensitive),
             int(request.whole_word), int(request.do_not_translate), request.note, now, now),
        )
        db.commit()
        return {"id": identifier, **request.model_dump()}
    finally:
        db.close()


@router.put("/glossaries/{glossary_id}/terms/{term_id}")
def update_term(glossary_id: str, term_id: str, request: TermInput):
    db = get_db()
    try:
        cursor = db.execute(
            """UPDATE glossary_terms SET source_text=?,target_text=?,case_sensitive=?,whole_word=?,
               do_not_translate=?,note=?,updated_at=? WHERE id=? AND glossary_id=?""",
            (request.source_text.strip(), request.target_text.strip(), int(request.case_sensitive), int(request.whole_word),
             int(request.do_not_translate), request.note, _now(), term_id, glossary_id),
        )
        db.commit()
        if not cursor.rowcount:
            raise HTTPException(404, "术语不存在")
        return {"id": term_id, **request.model_dump()}
    finally:
        db.close()


@router.delete("/glossaries/{glossary_id}/terms/{term_id}")
def delete_term(glossary_id: str, term_id: str):
    db = get_db()
    try:
        cursor = db.execute("DELETE FROM glossary_terms WHERE id=? AND glossary_id=?", (term_id, glossary_id))
        db.commit()
        if not cursor.rowcount:
            raise HTTPException(404, "术语不存在")
        return {"deleted": True}
    finally:
        db.close()


@router.get("/translation-memory/suggestions")
def translation_memory_suggestions(text: str, source_language: str = "auto", target_language: str = "zh", limit: int = 5):
    return {"suggestions": fuzzy_memory(text, source_language, target_language, limit)}


def _parse_terms(content: str, delimiter: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")), delimiter=delimiter)
    aliases = {"source": "source_text", "target": "target_text", "源词": "source_text", "目标词": "target_text"}
    rows = []
    for raw in reader:
        item = {aliases.get(key, key): (value or "").strip() for key, value in raw.items() if key}
        if item.get("source_text"):
            rows.append(item)
    return rows


@router.post("/glossaries/{glossary_id}/import")
def import_terms(glossary_id: str, request: TermImport):
    parsed = _parse_terms(request.content, request.delimiter)
    db = get_db()
    try:
        existing = {normalize_source(row["source_text"]): dict(row) for row in db.execute("SELECT * FROM glossary_terms WHERE glossary_id=?", (glossary_id,))}
        conflicts = [{"incoming": item, "existing": existing[normalize_source(item["source_text"])]} for item in parsed if normalize_source(item["source_text"]) in existing]
        fresh = [item for item in parsed if normalize_source(item["source_text"]) not in existing]
        if request.commit:
            now = _now()
            for item in fresh:
                db.execute(
                    """INSERT INTO glossary_terms
                       (id,glossary_id,source_text,target_text,case_sensitive,whole_word,do_not_translate,note,created_at,updated_at)
                       VALUES (?,?,?,?,0,1,0,?,?,?)""",
                    (str(uuid.uuid4()), glossary_id, item["source_text"], item.get("target_text", ""), item.get("note", ""), now, now),
                )
            db.commit()
        return {"rows": parsed, "new_count": len(fresh), "conflicts": conflicts, "committed": request.commit}
    finally:
        db.close()


@router.get("/glossaries/{glossary_id}/export", response_class=PlainTextResponse)
def export_terms(glossary_id: str, format: str = Query("csv", pattern="^(csv|tsv)$")):
    db = get_db()
    try:
        rows = db.execute("SELECT * FROM glossary_terms WHERE glossary_id=? ORDER BY source_text", (glossary_id,)).fetchall()
    finally:
        db.close()
    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t" if format == "tsv" else ",")
    writer.writerow(["source_text", "target_text", "case_sensitive", "whole_word", "do_not_translate", "note"])
    for row in rows:
        writer.writerow([row["source_text"], row["target_text"], row["case_sensitive"], row["whole_word"], row["do_not_translate"], row["note"]])
    return PlainTextResponse(output.getvalue(), media_type="text/tab-separated-values" if format == "tsv" else "text/csv")
