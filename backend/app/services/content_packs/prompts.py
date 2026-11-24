"""Section schemas, prompts, source chunking/compaction, and JSON LLM validation for content packs."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from typing import Any

import httpx

from ...models.database import get_db
from ...utils.task_manager import task_manager
from ..ai_providers import prepare_chat_payload, raise_for_provider_status

SECTION_ORDER = ("chapters", "summary", "quotes", "youtube", "podcast", "social")
SECTION_TITLES = {
    "chapters": "章节与时间戳",
    "summary": "摘要与要点",
    "quotes": "金句",
    "youtube": "YouTube 发布文案",
    "podcast": "播客 Show Notes",
    "social": "社交平台文案",
}


class ContentPackError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "CONTENT_GENERATION_FAILED",
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


def facade_attr(name: str, default: Any) -> Any:
    pkg = sys.modules.get(__package__ or "")
    return getattr(pkg, name, default) if pkg is not None else default


def _fingerprint(rows: list[dict[str, Any]], mode: str) -> str:
    payload = [
        [
            row["idx"],
            round(float(row["start"]), 3),
            round(float(row["end"]), 3),
            row.get("clean_text") or row.get("raw_text") or "",
            row.get("translated_text") or "",
            row.get("speaker") or "",
        ]
        for row in rows
    ]
    return hashlib.sha256(
        json.dumps(
            {"mode": mode, "segments": payload}, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _project_source(
    project_id: str,
    input_mode: str,
    *,
    allow_translation_fallback: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    if input_mode not in {"original", "translated", "bilingual"}:
        raise ContentPackError(
            "内容输入模式无效", error_code="CONTENT_INPUT_INVALID", recoverable=False
        )
    db = get_db()
    try:
        project_row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
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
    if not project_row:
        raise FileNotFoundError("项目不存在")
    if not rows:
        raise ContentPackError("项目还没有可用字幕", error_code="CONTENT_SOURCE_EMPTY")
    translated = sum(bool((row.get("translated_text") or "").strip()) for row in rows)
    if (
        input_mode in {"translated", "bilingual"}
        and translated < len(rows)
        and not allow_translation_fallback
    ):
        raise ContentPackError(
            f"译文尚未完整：{translated}/{len(rows)} 条",
            error_code="TRANSLATION_INCOMPLETE",
            details={"translated": translated, "total": len(rows), "coverage": translated / len(rows)},
        )
    source: list[dict[str, Any]] = []
    for row in rows:
        original = (row.get("clean_text") or row.get("raw_text") or "").strip()
        translation = (row.get("translated_text") or "").strip()
        if input_mode == "original":
            text = original
        elif input_mode == "translated":
            text = translation or original
        else:
            text = original if not translation else f"{original}\n译文：{translation}"
        source.append(
            {
                "index": int(row["idx"]),
                "start": round(float(row["start"]), 3),
                "end": round(float(row["end"]), 3),
                "speaker": row.get("speaker") or "",
                "text": text,
            }
        )
    project = dict(project_row)
    coverage = {"translated": translated, "total": len(rows), "coverage": translated / len(rows)}
    return project, source, coverage


def _extract_json(value: str) -> dict[str, Any]:
    content = (value or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.DOTALL)
    if fenced:
        content = fenced.group(1)
    else:
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            content = content[start : end + 1]
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("AI 必须返回 JSON 对象")
    return parsed


def _call_json(
    ai: dict[str, Any],
    *,
    system: str,
    user: dict[str, Any],
    task_id: str,
    max_tokens: int = 8192,
) -> dict[str, Any]:
    tm = facade_attr("task_manager", task_manager)
    tm.checkpoint(task_id)
    payload: dict[str, Any] = {
        "model": ai["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if ai.get("provider") in {"deepseek", "openai"}:
        payload["response_format"] = {"type": "json_object"}
    response = httpx.post(
        f"{ai['base_url'].rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {ai['api_key']}", "Content-Type": "application/json"},
        json=prepare_chat_payload(ai, payload),
        timeout=180,
    )
    raise_for_provider_status(response)
    tm.checkpoint(task_id)
    choice = response.json()["choices"][0]
    if choice.get("finish_reason") == "length":
        raise ContentPackError("AI 输出达到长度上限", error_code="AI_OUTPUT_TRUNCATED")
    return _extract_json(choice["message"]["content"])


def _source_chunks(
    source: list[dict[str, Any]], limit: int = 12_000
) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for row in source:
        row_size = len(row["text"]) + 80
        if current and size + row_size > limit:
            chunks.append(current)
            current, size = [], 0
        current.append(row)
        size += row_size
    if current:
        chunks.append(current)
    return chunks


def _compact_source(
    ai: dict[str, Any],
    source: list[dict[str, Any]],
    task_id: str,
) -> list[dict[str, Any]]:
    tm = facade_attr("task_manager", task_manager)
    call_fn = facade_attr("_call_json", _call_json)
    chunks = _source_chunks(source)
    if len(chunks) == 1:
        return source
    compact: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, 1):
        tm.update_task(
            task_id,
            step="content_mapping",
            progress=min(35, index / len(chunks) * 35),
            message=f"正在提炼长字幕 {index}/{len(chunks)}",
        )
        result = call_fn(
            ai,
            system=(
                "你是视频内容编辑。只依据提供字幕做事实保真的分块提炼。"
                "返回严格 JSON，不要 Markdown，不得编造。保留主题起止字幕编号及可引用金句编号。"
            ),
            user={
                "schema": {
                    "topics": [{"start_index": 1, "end_index": 2, "summary": "string"}],
                    "key_points": ["string"],
                    "quote_indices": [1],
                },
                "segments": chunk,
            },
            task_id=task_id,
            max_tokens=4096,
        )
        start, end = chunk[0]["index"], chunk[-1]["index"]
        compact.append(
            {
                "start_index": start,
                "end_index": end,
                "start": chunk[0]["start"],
                "end": chunk[-1]["end"],
                "topics": result.get("topics") if isinstance(result.get("topics"), list) else [],
                "key_points": result.get("key_points")
                if isinstance(result.get("key_points"), list)
                else [],
                "quote_indices": result.get("quote_indices")
                if isinstance(result.get("quote_indices"), list)
                else [],
            }
        )
    return compact


def _find_segment(source: list[dict[str, Any]], index: Any) -> dict[str, Any] | None:
    try:
        numeric = int(index)
    except (TypeError, ValueError):
        return None
    return next((row for row in source if row["index"] == numeric), None)


def _validate_section(
    kind: str,
    payload: dict[str, Any],
    source: list[dict[str, Any]],
) -> dict[str, Any]:
    if kind == "chapters":
        chapters = []
        for item in payload.get("chapters") or []:
            if not isinstance(item, dict):
                continue
            start = _find_segment(source, item.get("start_index"))
            end = _find_segment(source, item.get("end_index"))
            title = str(item.get("title") or "").strip()
            if not start or not end or end["index"] < start["index"] or not title:
                continue
            chapters.append(
                {
                    "start_index": start["index"],
                    "end_index": end["index"],
                    "start": start["start"],
                    "end": end["end"],
                    "title": title[:160],
                    "summary": str(item.get("summary") or "").strip(),
                }
            )
        if not chapters:
            raise ValueError("AI 未返回有效章节")
        return {"chapters": chapters}
    if kind == "summary":
        overview = str(payload.get("overview") or "").strip()
        points = [str(item).strip() for item in payload.get("key_points") or [] if str(item).strip()]
        if not overview or not points:
            raise ValueError("AI 未返回有效摘要与要点")
        return {"overview": overview, "key_points": points[:20]}
    if kind == "quotes":
        quotes = []
        for item in payload.get("quotes") or []:
            if not isinstance(item, dict):
                continue
            row = _find_segment(source, item.get("segment_index"))
            if not row:
                continue
            quotes.append(
                {
                    "segment_index": row["index"],
                    "start": row["start"],
                    "end": row["end"],
                    "speaker": row["speaker"],
                    "text": str(item.get("text") or row["text"]).strip(),
                    "reason": str(item.get("reason") or "").strip(),
                }
            )
        if not quotes:
            raise ValueError("AI 未返回可定位的金句")
        return {"quotes": quotes[:20]}
    if kind == "youtube":
        titles = [str(item).strip() for item in payload.get("titles") or [] if str(item).strip()]
        description = str(payload.get("description") or "").strip()
        if not titles or not description:
            raise ValueError("AI 未返回完整 YouTube 文案")
        return {
            "titles": titles[:10],
            "description": description,
            "chapter_text": str(payload.get("chapter_text") or "").strip(),
            "tags": [str(item).strip() for item in payload.get("tags") or [] if str(item).strip()][
                :30
            ],
        }
    if kind == "podcast":
        title = str(payload.get("title") or "").strip()
        show_notes = str(payload.get("show_notes") or "").strip()
        if not title or not show_notes:
            raise ValueError("AI 未返回完整播客文案")
        return {
            "title": title,
            "intro": str(payload.get("intro") or "").strip(),
            "show_notes": show_notes,
        }
    if kind == "social":
        result: dict[str, str] = {}
        for platform in ("xiaohongshu", "wechat", "generic"):
            value = payload.get(platform)
            if isinstance(value, dict):
                result[platform] = "\n\n".join(
                    str(value.get(key) or "").strip()
                    for key in ("title", "body", "tags")
                    if str(value.get(key) or "").strip()
                )
            else:
                result[platform] = str(value or "").strip()
        if not any(result.values()):
            raise ValueError("AI 未返回社交平台文案")
        return result
    raise ValueError("未知内容区域")


def _section_prompt(kind: str) -> tuple[str, dict[str, Any]]:
    common = (
        "你是严谨的视频内容编辑。只能依据输入字幕，不得添加字幕中不存在的事实。"
        "返回严格 JSON 对象，不要 Markdown。时间定位必须使用输入中的字幕编号。"
    )
    schemas: dict[str, dict[str, Any]] = {
        "chapters": {
            "chapters": [
                {"start_index": 1, "end_index": 10, "title": "string", "summary": "string"}
            ]
        },
        "summary": {"overview": "string", "key_points": ["string"]},
        "quotes": {"quotes": [{"segment_index": 1, "text": "string", "reason": "string"}]},
        "youtube": {
            "titles": ["string"],
            "description": "string",
            "chapter_text": "string",
            "tags": ["string"],
        },
        "podcast": {"title": "string", "intro": "string", "show_notes": "string"},
        "social": {
            "xiaohongshu": {"title": "string", "body": "string", "tags": "string"},
            "wechat": {"title": "string", "body": "string", "tags": "string"},
            "generic": {"title": "string", "body": "string", "tags": "string"},
        },
    }
    instructions = {
        "chapters": "按主题变化生成覆盖整段内容的章节，章节按时间递增。",
        "summary": "生成事实保真的摘要与可执行要点。",
        "quotes": "选择可以独立理解且适合引用的原意金句，保留对应字幕编号。",
        "youtube": "生成可供人工选择的标题、简介、章节文本和标签，避免夸大。",
        "podcast": "生成节目标题、简介和结构清晰的 Show Notes。",
        "social": "分别生成小红书、公众号和通用社交文案，保持平台语气但不编造。",
    }
    return f"{common}{instructions[kind]}", schemas[kind]
