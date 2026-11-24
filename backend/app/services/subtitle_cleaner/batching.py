"""Semantic batching, text normalization, JSON extraction, and shared helpers for subtitle cleaning."""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def facade_attr(name: str, default: Any) -> Any:
    pkg = sys.modules.get(__package__ or "")
    return getattr(pkg, name, default) if pkg is not None else default


def _build_semantic_batches(
    rows: list[dict], max_size: int = 32, max_chars: int = 3500
) -> list[list[dict]]:
    """Locked rows are hard boundaries; unlocked runs end near punctuation when possible."""
    batches = []
    run = []
    for row in rows:
        if row.get("locked"):
            if run:
                batches.extend(_split_run(run, max_size, max_chars))
                run = []
            continue
        run.append(row)
    if run:
        batches.extend(_split_run(run, max_size, max_chars))
    return batches


def _split_run(run: list[dict], max_size: int, max_chars: int = 6000) -> list[list[dict]]:
    result = []
    cursor = 0
    while cursor < len(run):
        remaining = len(run) - cursor
        char_limit = cursor
        chars = 0
        while char_limit < len(run) and char_limit - cursor < max_size:
            next_chars = len(run[char_limit].get("raw_text", ""))
            if char_limit > cursor and chars + next_chars > max_chars:
                break
            chars += next_chars
            char_limit += 1
        if remaining <= max_size and char_limit == len(run):
            result.append(run[cursor:])
            break
        high = max(cursor + 1, char_limit)
        low = min(high, cursor + max(12, (high - cursor) - 12))
        candidates = [
            pos
            for pos in range(low, high + 1)
            if re.search(r"[.!?。！？][\"'”’)]?$", (run[pos - 1]["raw_text"] or "").strip())
        ]
        cut = candidates[-1] if candidates else high
        result.append(run[cursor:cut])
        cursor = cut
    return result


def _output_token_budget(prompt_batch: list[dict]) -> int:
    """Conservative output allowance, capped at the provider-safe 8192 ceiling."""
    serialized = json.dumps(prompt_batch, ensure_ascii=False, separators=(",", ":"))
    cjk_count = sum(1 for char in serialized if "\u3400" <= char <= "\u9fff")
    non_cjk_count = len(serialized) - cjk_count
    estimated_tokens = cjk_count + non_cjk_count // 3 + len(prompt_batch) * 24
    estimated = int(estimated_tokens * 1.35)
    return min(8192, max(2048, estimated))


def _batch_character_count(batch: list[dict]) -> int:
    return sum(len(str(row.get("raw_text") or "")) for row in batch)


def _split_batch_for_recovery(batch: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split near the midpoint, preferring a sentence ending or a real pause."""
    midpoint = len(batch) / 2
    best_position = 1
    best_score = float("-inf")
    for position in range(1, len(batch)):
        previous = batch[position - 1]
        following = batch[position]
        distance_penalty = abs(position - midpoint) * 3
        sentence_bonus = (
            18
            if re.search(
                r"[.!?。！？][\"'”’)]?$", str(previous.get("raw_text") or "").strip()
            )
            else 0
        )
        try:
            gap = float(following.get("start") or 0) - float(previous.get("end") or 0)
        except (TypeError, ValueError):
            gap = 0
        pause_bonus = min(18, max(0, gap) * 12)
        score = sentence_bonus + pause_bonus - distance_penalty
        if score > best_score:
            best_position = position
            best_score = score
    return batch[:best_position], batch[best_position:]


def _fingerprint(rows: list[dict]):
    return [
        (
            row["id"],
            row["idx"],
            row["start"],
            row["end"],
            row.get("raw_text", ""),
            row.get("clean_text", ""),
            row.get("translated_text", ""),
            int(bool(row.get("locked"))),
        )
        for row in rows
    ]


def _join_text(parts: list[str]) -> str:
    result = ""
    for part in parts:
        part = (part or "").strip()
        if not part:
            continue
        if not result:
            result = part
        elif re.search(r"[\u3400-\u9fff]$", result) and re.match(r"^[\u3400-\u9fff]", part):
            result += part
        else:
            result += " " + part
    return result


def _extract_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start_candidates = [value for value in (text.find("["), text.find("{")) if value >= 0]
        if not start_candidates:
            return None
        start = min(start_candidates)
        for closing in ("]", "}"):
            end = text.rfind(closing)
            if end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
        return None


def _validate_batch_results(batch: list, parsed: list, text_field: str) -> list:
    """Strict one-to-one result validation reused by subtitle translation."""
    expected = [str(item["id"]) for item in batch]
    normalized = {}
    duplicates = set()
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", ""))
        value = item.get(text_field)
        if item_id in expected and isinstance(value, str) and value.strip():
            if item_id in normalized:
                duplicates.add(item_id)
            normalized[item_id] = {"id": item_id, text_field: value.strip()}
    if set(normalized) != set(expected) or duplicates:
        raise ValueError("AI 返回的字幕 ID 有遗漏、重复或顺序变化")
    return [normalized[item_id] for item_id in expected]
