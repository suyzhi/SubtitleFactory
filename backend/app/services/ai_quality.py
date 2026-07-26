"""Preview-only AI suggestions for one subtitle quality issue."""

from __future__ import annotations

import json
import re

import httpx

from .ai_providers import assigned_provider


def generate_quality_preview(issue: dict) -> dict:
    ai = assigned_provider("quality")
    source = issue.get("clean_text") or issue.get("raw_text") or ""
    translation = issue.get("translated_text") or ""
    prompt = {
        "rule": issue.get("rule_id"), "problem": issue.get("message"),
        "suggestion": issue.get("suggestion"), "clean_text": source,
        "translated_text": translation,
    }
    response = httpx.post(
        f"{ai['base_url'].rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {ai['api_key']}", "Content-Type": "application/json"},
        json={
            "model": ai["model"], "temperature": 0,
            "messages": [
                {"role": "system", "content": "你是字幕质检修复助手。只修复给定问题，不增删事实，不解释。返回 JSON 对象，只能包含 clean_text 和 translated_text。"},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
        }, timeout=45,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("AI 质检返回格式无效")
    clean = str(result.get("clean_text", source)).strip()
    translated = str(result.get("translated_text", translation)).strip()
    if not clean:
        raise ValueError("AI 质检不能清空字幕正文")
    return {
        "before": {"clean_text": source, "translated_text": translation},
        "after": {"clean_text": clean, "translated_text": translated},
        "provider": ai["provider"], "model": ai["model"],
    }
