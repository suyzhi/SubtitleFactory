"""Local full-text subtitle search and index maintenance."""

from __future__ import annotations

from typing import Any

from ..models.database import get_db


def rebuild_search_index() -> dict[str, int]:
    db = get_db()
    try:
        db.execute("DELETE FROM segment_search")
        db.execute(
            """INSERT INTO segment_search(
                   segment_id,project_id,project_title,speaker_name,
                   raw_text,clean_text,translated_text
               )
               SELECT s.id,s.project_id,p.title,COALESCE(s.speaker,''),
                      COALESCE(s.raw_text,''),COALESCE(s.clean_text,''),
                      COALESCE(s.translated_text,'')
               FROM segments s JOIN projects p ON p.id=s.project_id"""
        )
        db.commit()
        indexed = int(db.execute("SELECT COUNT(*) FROM segment_search").fetchone()[0])
        source = int(db.execute("SELECT COUNT(*) FROM segments").fetchone()[0])
        return {"indexed": indexed, "source": source}
    finally:
        db.close()


def search_index_status() -> dict[str, Any]:
    db = get_db()
    try:
        indexed = int(db.execute("SELECT COUNT(*) FROM segment_search").fetchone()[0])
        source = int(db.execute("SELECT COUNT(*) FROM segments").fetchone()[0])
        return {"ok": indexed == source, "indexed": indexed, "source": source}
    except Exception as exc:
        return {"ok": False, "indexed": 0, "source": 0, "error": str(exc)}
    finally:
        db.close()


def _escaped_like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fts_phrase(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _snippet(row: dict[str, Any], query: str) -> tuple[str, list[str]]:
    fields = (
        ("clean_text", row.get("clean_text") or ""),
        ("translated_text", row.get("translated_text") or ""),
        ("raw_text", row.get("raw_text") or ""),
        ("speaker_name", row.get("speaker_name") or ""),
        ("project_title", row.get("project_title") or ""),
    )
    needle = query.casefold()
    matches: list[str] = []
    selected = ""
    for name, text in fields:
        if needle in text.casefold():
            matches.append(name)
            if not selected:
                selected = text
    selected = selected or row.get("clean_text") or row.get("raw_text") or ""
    folded = selected.casefold()
    position = folded.find(needle)
    if position < 0:
        return selected[:180], matches
    start = max(0, position - 70)
    end = min(len(selected), position + len(query) + 100)
    prefix = "…" if start else ""
    suffix = "…" if end < len(selected) else ""
    return f"{prefix}{selected[start:end]}{suffix}", matches


def search_segments(
    query: str,
    *,
    page: int = 1,
    page_size: int = 50,
    project_id: str = "",
    group_name: str = "",
    speaker_id: str = "",
    source_language: str = "",
    target_language: str = "",
    created_from: str = "",
    created_to: str = "",
) -> dict[str, Any]:
    query = query.strip()
    if len(created_to) == 10:
        created_to = f"{created_to} 23:59:59"
    if len(query) < 2:
        return {"query": query, "hits": [], "total": 0, "page": 1, "page_size": page_size, "facets": {}}
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))

    joins = [
        "JOIN segments s ON s.id=segment_search.segment_id",
        "JOIN projects p ON p.id=s.project_id",
    ]
    where = ["p.deleted_at IS NULL", "COALESCE(s.is_draft,0)=0"]
    values: list[Any] = []
    use_fts = len(query) >= 3
    if use_fts:
        where.append("segment_search MATCH ?")
        values.append(_fts_phrase(query))
    else:
        pattern = _escaped_like(query)
        where.append(
            """(segment_search.project_title LIKE ? ESCAPE '\\'
                 OR segment_search.speaker_name LIKE ? ESCAPE '\\'
                 OR segment_search.raw_text LIKE ? ESCAPE '\\'
                 OR segment_search.clean_text LIKE ? ESCAPE '\\'
                 OR segment_search.translated_text LIKE ? ESCAPE '\\')"""
        )
        values.extend([pattern] * 5)
    for value, clause in (
        (project_id, "p.id=?"),
        (group_name, "COALESCE(p.group_name,'')=?"),
        (speaker_id, "s.speaker_id=?"),
        (source_language, "p.language=?"),
        (target_language, "p.target_language=?"),
        (created_from, "p.created_at>=?"),
        (created_to, "p.created_at<=?"),
    ):
        if value:
            where.append(clause)
            values.append(value)
    predicate = " AND ".join(where)
    rank = "bm25(segment_search,0,0,1.2,1.0,0.7,2.0,1.6)" if use_fts else "0"

    db = get_db()
    try:
        total = int(
            db.execute(
                f"SELECT COUNT(*) FROM segment_search {' '.join(joins)} WHERE {predicate}",
                values,
            ).fetchone()[0]
        )
        rows = [
            dict(row)
            for row in db.execute(
                f"""SELECT s.id segment_id,s.project_id,p.title project_title,
                           p.group_name,p.source_type,p.language source_language,
                           p.target_language,p.created_at,p.updated_at,
                           s.idx segment_index,s.start,s.end,s.speaker_id,
                           COALESCE(s.speaker,'') speaker_name,
                           COALESCE(s.raw_text,'') raw_text,
                           COALESCE(s.clean_text,'') clean_text,
                           COALESCE(s.translated_text,'') translated_text,
                           (SELECT b.title FROM batch_items bi
                              JOIN batches b ON b.id=bi.batch_id
                             WHERE bi.project_id=p.id AND b.kind='youtube_playlist'
                             LIMIT 1) playlist_title,
                           {rank} rank
                      FROM segment_search {' '.join(joins)}
                     WHERE {predicate}
                     ORDER BY rank,p.updated_at DESC,s.idx
                     LIMIT ? OFFSET ?""",
                [*values, page_size, (page - 1) * page_size],
            ).fetchall()
        ]
        for row in rows:
            row["snippet"], row["match_fields"] = _snippet(row, query)
            row.pop("raw_text", None)
            row.pop("clean_text", None)
            row.pop("translated_text", None)
        facets = {
            "projects": [
                dict(row)
                for row in db.execute(
                    """SELECT id,title,group_name,language source_language,target_language
                         FROM projects WHERE deleted_at IS NULL
                        ORDER BY updated_at DESC"""
                ).fetchall()
            ],
            "speakers": [
                dict(row)
                for row in db.execute(
                    """SELECT sp.id,sp.name,sp.project_id,p.title project_title
                         FROM speakers sp JOIN projects p ON p.id=sp.project_id
                        WHERE p.deleted_at IS NULL ORDER BY sp.name"""
                ).fetchall()
            ],
            "groups": [
                row[0]
                for row in db.execute(
                    """SELECT DISTINCT group_name FROM projects
                        WHERE deleted_at IS NULL AND COALESCE(group_name,'')<>''
                        ORDER BY group_name"""
                ).fetchall()
            ],
        }
        return {
            "query": query,
            "hits": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "facets": facets,
        }
    finally:
        db.close()
