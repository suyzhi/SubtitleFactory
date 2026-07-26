"""Glossary and exact translation-memory helpers."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher

from ..models.database import get_db


def normalize_source(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "").strip()).casefold()


def source_hash(text: str) -> str:
    return hashlib.sha256(normalize_source(text).encode("utf-8")).hexdigest()


def relevant_terms(project_id: str, texts: list[str]) -> list[dict]:
    db = get_db()
    try:
        rows = db.execute(
            """SELECT t.* FROM glossary_terms t JOIN glossaries g ON g.id=t.glossary_id
               WHERE g.project_id IS NULL OR g.project_id=? ORDER BY g.project_id DESC""",
            (project_id,),
        ).fetchall()
    finally:
        db.close()
    combined = "\n".join(texts)
    result = []
    seen = set()
    for row in rows:
        key = normalize_source(row["source_text"])
        if key in seen:
            continue
        flags = 0 if row["case_sensitive"] else re.IGNORECASE
        pattern = rf"\b{re.escape(row['source_text'])}\b" if row["whole_word"] else re.escape(row["source_text"])
        if re.search(pattern, combined, flags):
            result.append(dict(row)); seen.add(key)
    return result


def exact_memory(text: str, source_language: str, target_language: str) -> str | None:
    digest = source_hash(text)
    db = get_db()
    try:
        row = db.execute(
            """SELECT target_text FROM translation_memory
               WHERE source_hash=? AND source_language IN (?, 'auto') AND target_language=?
               ORDER BY confirmed DESC,use_count DESC,updated_at DESC LIMIT 1""",
            (digest, source_language, target_language),
        ).fetchone()
        if row:
            db.execute(
                "UPDATE translation_memory SET use_count=use_count+1 WHERE source_hash=? AND target_text=?",
                (digest, row["target_text"]),
            )
            db.commit()
            return row["target_text"]
        return None
    finally:
        db.close()


def remember_translation(
    source_text: str, target_text: str, source_language: str, target_language: str,
    origin: str = "machine", confirmed: bool = False,
) -> None:
    if not source_text.strip() or not target_text.strip():
        return
    db = get_db()
    try:
        db.execute(
            """INSERT INTO translation_memory
               (id,source_hash,source_language,target_language,source_text,target_text,origin,confirmed,use_count,created_at,updated_at)
               VALUES (lower(hex(randomblob(16))),?,?,?,?,?,?,?,0,datetime('now','localtime'),datetime('now','localtime'))
               ON CONFLICT(source_hash,source_language,target_language,target_text) DO UPDATE SET
               confirmed=MAX(confirmed,excluded.confirmed),origin=excluded.origin,updated_at=excluded.updated_at""",
            (source_hash(source_text), source_language, target_language, source_text, target_text, origin, int(confirmed)),
        )
        db.commit()
    finally:
        db.close()


def fuzzy_memory(text: str, source_language: str, target_language: str, limit: int = 5) -> list[dict]:
    """Return local suggestions only; callers must never auto-apply them."""
    normalized = normalize_source(text)
    if not normalized:
        return []
    db = get_db()
    try:
        rows = db.execute(
            """SELECT source_text,target_text,origin,confirmed,use_count FROM translation_memory
               WHERE source_language IN (?, 'auto') AND target_language=?
               ORDER BY confirmed DESC,use_count DESC,updated_at DESC LIMIT 500""",
            (source_language, target_language),
        ).fetchall()
    finally:
        db.close()
    matches = []
    for row in rows:
        score = SequenceMatcher(None, normalized, normalize_source(row["source_text"])).ratio()
        if score >= .62:
            matches.append({**dict(row), "score": round(score, 4)})
    return sorted(matches, key=lambda item: (item["confirmed"], item["score"], item["use_count"]), reverse=True)[:max(1, min(limit, 20))]
