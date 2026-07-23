"""Deterministic, offline subtitle quality checks."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from ..models.database import get_db


DEFAULT_RULES = {
    "min_duration": 0.5,
    "max_duration": 8.0,
    "max_cps": 20.0,
    "max_line_length": 42,
    "max_lines": 2,
    "large_gap": 8.0,
    "rapid_speaker_gap": 0.15,
}


def _text(row) -> str:
    return (row["clean_text"] or row["raw_text"] or "").strip()


def _fingerprint(rule: str, segment_id: str | None, message: str) -> str:
    return hashlib.sha256(f"{rule}|{segment_id or ''}|{message}".encode()).hexdigest()


def _issue(rule: str, row, severity: str, message: str, suggestion: str = "", details=None):
    return {
        "rule_id": rule,
        "segment_id": row["id"] if row else None,
        "segment_index": row["idx"] if row else None,
        "severity": severity,
        "message": message,
        "suggestion": suggestion,
        "details": details or {},
    }


def evaluate(project_id: str, configuration: dict | None = None) -> list[dict]:
    rules = {**DEFAULT_RULES, **(configuration or {})}
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM segments WHERE project_id=? ORDER BY idx", (project_id,)
        ).fetchall()
        terms = db.execute(
            """SELECT t.* FROM glossary_terms t JOIN glossaries g ON g.id=t.glossary_id
               WHERE g.project_id IS NULL OR g.project_id=?""", (project_id,)
        ).fetchall()
    finally:
        db.close()

    issues: list[dict] = []
    previous = None
    previous_text = ""
    for row in rows:
        start, end = float(row["start"]), float(row["end"])
        duration = end - start
        text = _text(row)
        translation = (row["translated_text"] or "").strip()
        if start < 0 or duration <= 0:
            issues.append(_issue("invalid_time", row, "error", "时间码无效", "调整开始和结束时间"))
        if previous is not None:
            overlap = float(previous["end"]) - start
            gap = start - float(previous["end"])
            if overlap > 0.001:
                issues.append(_issue("overlap", row, "error", f"与上一条重叠 {overlap:.2f} 秒", "调整相邻边界"))
            elif gap > rules["large_gap"]:
                issues.append(_issue("large_gap", row, "info", f"与上一条间隔 {gap:.1f} 秒", "确认是否遗漏字幕"))
            if (row["speaker_id"] and previous["speaker_id"] and row["speaker_id"] != previous["speaker_id"]
                    and gap < rules["rapid_speaker_gap"] and min(duration, float(previous["end"]) - float(previous["start"])) < .65):
                issues.append(_issue("rapid_speaker_switch", row, "info", "说话人在极短字幕间快速切换", "试听并确认说话人边界"))
        if 0 < duration < rules["min_duration"]:
            issues.append(_issue("short_duration", row, "warning", f"显示时间仅 {duration:.2f} 秒", "延长显示时间"))
        if duration > rules["max_duration"]:
            issues.append(_issue("long_duration", row, "warning", f"显示时间达到 {duration:.1f} 秒", "考虑拆分字幕"))
        if not text:
            issues.append(_issue("empty_text", row, "error", "字幕正文为空", "填写正文或删除该条"))
        elif duration > 0 and len(text.replace("\n", "")) / duration > rules["max_cps"]:
            cps = len(text.replace("\n", "")) / duration
            issues.append(_issue("reading_speed", row, "warning", f"阅读速度 {cps:.1f} 字/秒", "延长时间或精简文字"))
        lines = text.splitlines() or [""]
        if any(len(line) > rules["max_line_length"] for line in lines):
            issues.append(_issue("line_length", row, "warning", "单行文字过长", "在语义边界换行"))
        if len(lines) > rules["max_lines"]:
            issues.append(_issue("line_count", row, "warning", f"字幕包含 {len(lines)} 行", "减少行数"))
        if text and text == previous_text:
            issues.append(_issue("duplicate", row, "warning", "与上一条字幕完全重复", "确认是否应合并"))
        if text and not translation:
            issues.append(_issue("missing_translation", row, "info", "译文为空", "翻译或确认无需译文"))
        source_numbers = re.findall(r"\d+(?:[.,]\d+)?", text)
        target_numbers = re.findall(r"\d+(?:[.,]\d+)?", translation)
        if translation and source_numbers != target_numbers:
            issues.append(_issue("number_mismatch", row, "warning", "原文与译文数字不一致", "核对数字", {"source": source_numbers, "target": target_numbers}))
        if translation:
            source_has_end = bool(re.search(r"[.!?。！？…][\"'’”）)]?$", text))
            target_has_end = bool(re.search(r"[.!?。！？…][\"'’”）)]?$", translation))
            if source_has_end != target_has_end:
                issues.append(_issue("punctuation", row, "info", "原文与译文句末标点不一致", "核对句末标点"))
            known_terms = {str(term["source_text"]).casefold() for term in terms}
            missing_names = [name for name in re.findall(r"(?<![.\w])[A-Z][A-Za-z0-9-]{2,}", text)
                             if name.casefold() not in known_terms and not re.search(rf"\b{re.escape(name)}\b", translation)]
            if missing_names:
                issues.append(_issue("proper_noun", row, "info", f"专有名词可能未保持一致：{', '.join(missing_names[:3])}", "加入术语表或核对译名", {"names": missing_names}))
        for term in terms:
            flags = 0 if term["case_sensitive"] else re.IGNORECASE
            source = term["source_text"]
            pattern = rf"\b{re.escape(source)}\b" if term["whole_word"] else re.escape(source)
            if re.search(pattern, text, flags):
                expected = source if term["do_not_translate"] else term["target_text"]
                if expected and not re.search(re.escape(expected), translation, flags):
                    issues.append(_issue("glossary", row, "warning", f"术语“{source}”未使用“{expected}”", "按术语表修正", {"source": source, "expected": expected}))
        previous, previous_text = row, text
    return issues


def scan(project_id: str, configuration: dict | None = None) -> list[dict]:
    issues = evaluate(project_id, configuration)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    try:
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise FileNotFoundError("项目不存在")
        previous_status = {row["fingerprint"]: row["status"] for row in db.execute(
            "SELECT fingerprint,status FROM quality_issues WHERE project_id=?", (project_id,)
        )}
        db.execute("DELETE FROM quality_issues WHERE project_id=? AND rule_id<>'speaker_uncertain'", (project_id,))
        for issue in issues:
            issue_id = str(uuid.uuid4())
            fingerprint = _fingerprint(issue["rule_id"], issue["segment_id"], issue["message"])
            db.execute(
                """INSERT INTO quality_issues
                   (id,project_id,segment_id,rule_id,severity,fingerprint,message,suggestion,status,details_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?, ?, ?,?,?)""",
                (issue_id, project_id, issue["segment_id"], issue["rule_id"], issue["severity"], fingerprint,
                 issue["message"], issue["suggestion"], "ignored" if previous_status.get(fingerprint) == "ignored" else "open",
                 json.dumps(issue["details"], ensure_ascii=False), now, now),
            )
            issue["id"] = issue_id
            issue["status"] = "ignored" if previous_status.get(fingerprint) == "ignored" else "open"
        db.commit()
    finally:
        db.close()
    return issues


def list_issues(project_id: str, status: str = "open") -> list[dict]:
    db = get_db()
    try:
        rows = db.execute(
            """SELECT q.*,s.idx segment_index FROM quality_issues q
               LEFT JOIN segments s ON s.id=q.segment_id
               WHERE q.project_id=? AND (?='all' OR q.status=?)
               ORDER BY CASE q.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                        COALESCE(s.idx, 0)""", (project_id, status, status)
        ).fetchall()
        return [{**dict(row), "details": json.loads(row["details_json"] or "{}") } for row in rows]
    finally:
        db.close()


def set_issue_status(project_id: str, issue_id: str, status: str) -> bool:
    db = get_db()
    try:
        cursor = db.execute(
            "UPDATE quality_issues SET status=?,updated_at=datetime('now','localtime') WHERE id=? AND project_id=?",
            (status, issue_id, project_id),
        )
        db.commit()
        return cursor.rowcount > 0
    finally:
        db.close()
