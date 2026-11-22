"""
字幕工厂 - 转写结果时间轴与文本后处理模块

纯函数集合：规范时间区间、词/字元级时间戳归一化、短句碎片智能合并、
长句拆分与基于词元时间戳的边界分配。
"""

from __future__ import annotations

import json
import logging
import re
from typing import List

from ..utils.config import MAX_CHARS_CN, MAX_CHARS_EN, MAX_DURATION, MIN_DURATION

logger = logging.getLogger("app.services.transcriber")


def _decode_timings(value) -> list[dict]:
    try:
        parsed = json.loads(value or "[]") if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _segment_word_timings(segment, time_offset: float = 0.0) -> list[dict]:
    """Normalize Parakeet/Whisper token objects into one durable timing format."""
    source = getattr(segment, "timings", None) or getattr(segment, "words", None) or []
    result: list[dict] = []
    for item in source:
        getter = (
            item.get
            if isinstance(item, dict)
            else lambda key, default=None, item=item: getattr(item, key, default)
        )
        text = str(getter("text") or getter("word") or getter("token") or "")
        try:
            start = float(getter("start", getter("startTime", 0.0))) + time_offset
            end = float(getter("end", getter("endTime", start - time_offset + 0.04))) + time_offset
        except (TypeError, ValueError):
            continue
        if not text or end <= start:
            continue
        result.append({"text": text, "start": round(max(0.0, start), 4), "end": round(end, 4)})
    return result


def _fmt_time(seconds: float) -> str:
    """格式化时间: 00:04:32"""
    if not seconds:
        return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _has_sentence_end(text: str) -> bool:
    return bool(re.search(r"[.!?。！？][\"'”’)]?$", (text or "").strip()))


def _smart_merge(text1: str, text2: str) -> str:
    if text1 and text1[-1] in ".。!！?？,，":
        return f"{text1} {text2}"
    return f"{text1} {text2}"


def _max_chars_for_text(text: str) -> int:
    cn_count = sum(1 for c in text if "\u3400" <= c <= "\u9fff")
    return MAX_CHARS_CN if cn_count >= max(1, len(text) // 4) else MAX_CHARS_EN


def _post_process_segments(raw_segments: list) -> tuple:
    """
    后处理字幕段：
    1. 规范非法或重叠的时间区间
    2. 只合并没有完整句界、持续时间 < MIN_DURATION 的相邻碎片
    3. 保留模型识别出的完整语义句，不再按显示长度或时长硬拆

    Returns: (final_segments, merge_count, split_count)
    """
    logger.info("[Transcriber] 后处理: 规范时间轴并保留完整语义句")
    merge_count = 0
    split_count = 0

    normalized: List[dict] = []
    previous_end = 0.0
    for raw in sorted(
        raw_segments,
        key=lambda item: (float(item.get("start", 0)), float(item.get("end", 0))),
    ):
        text = str(raw.get("text", "")).strip()
        if not text:
            continue
        start = max(0.0, float(raw.get("start", 0)))
        end = max(start + 0.05, float(raw.get("end", start)))
        if start < previous_end:
            start = previous_end
        if end <= start:
            continue
        timings = [
            item
            for item in _decode_timings(raw.get("timings", []))
            if isinstance(item, dict)
            and float(item.get("end", 0) or 0) > start
            and float(item.get("start", 0) or 0) < end
        ]
        normalized.append({"start": start, "end": end, "text": text, "timings": timings})
        previous_end = end

    merged: List[dict] = []
    i = 0
    while i < len(normalized):
        seg = dict(normalized[i])
        duration = seg["end"] - seg["start"]
        has_sentence_end = _has_sentence_end(seg["text"])
        if duration < MIN_DURATION and not has_sentence_end and merged:
            prev = merged[-1]
            gap = seg["start"] - prev["end"]
            if (
                not _has_sentence_end(prev["text"])
                and gap <= 0.75
                and seg["end"] - prev["start"] <= MAX_DURATION
            ):
                prev["end"] = seg["end"]
                prev["text"] = _smart_merge(prev["text"], seg["text"])
                prev["timings"] = [*prev.get("timings", []), *seg.get("timings", [])]
                merge_count += 1
                i += 1
                continue
        if duration < MIN_DURATION and not has_sentence_end and i + 1 < len(normalized):
            nxt = normalized[i + 1]
            gap = nxt["start"] - seg["end"]
            if gap <= 0.75 and nxt["end"] - seg["start"] <= MAX_DURATION:
                seg["end"] = nxt["end"]
                seg["text"] = _smart_merge(seg["text"], nxt["text"])
                seg["timings"] = [*seg.get("timings", []), *nxt.get("timings", [])]
                merge_count += 1
                i += 1
        merged.append(seg)
        i += 1

    final = merged
    output = []
    for idx, seg in enumerate(final):
        output.append(
            {
                "index": idx + 1,
                "start": round(seg["start"], 3),
                "end": round(seg["end"], 3),
                "text": seg["text"].strip(),
            }
        )

    return output, merge_count, split_count


def _split_text_into_pieces(text: str, max_chars: int, min_parts: int = 1) -> List[str]:
    """Split at punctuation/words first, then fall back to character chunks."""
    clauses = [p.strip() for p in re.findall(r".+?(?:[.。!！?？，,;；]+|$)", text) if p.strip()]
    atoms: List[str] = []
    for clause in clauses or [text]:
        if len(clause) <= max_chars:
            atoms.append(clause)
            continue
        words = clause.split()
        if len(words) > 1:
            current = ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if current and len(candidate) > max_chars:
                    atoms.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                atoms.append(current)
        else:
            atoms.extend(clause[pos : pos + max_chars] for pos in range(0, len(clause), max_chars))

    pieces: List[str] = []
    for atom in atoms:
        candidate = _smart_merge(pieces[-1], atom) if pieces else atom
        if pieces and len(candidate) <= max_chars and len(pieces) + 1 >= min_parts:
            pieces[-1] = candidate
        else:
            pieces.append(atom)

    while len(pieces) < min_parts:
        idx = max(range(len(pieces)), key=lambda n: len(pieces[n]))
        value = pieces[idx]
        if len(value) < 2:
            break
        midpoint = len(value) // 2
        whitespace = [match.start() for match in re.finditer(r"\s+", value)]
        cut = min(whitespace, key=lambda position: abs(position - midpoint)) if whitespace else midpoint
        if cut <= 0 or cut >= len(value):
            cut = midpoint
        left = value[:cut].rstrip()
        right = value[cut:].lstrip()
        pieces[idx : idx + 1] = [left, right]
    return [piece for piece in pieces if piece]


def _allocate_piece_times(
    start: float,
    end: float,
    pieces: List[str],
    timings: list[dict] | None = None,
) -> List[dict]:
    """Allocate text pieces on real token boundaries, never equal-duration slices."""
    if not pieces:
        return []
    if len(pieces) == 1:
        return [{"start": round(start, 3), "end": round(end, 3), "text": pieces[0]}]

    valid_timings = []
    for item in timings or []:
        try:
            token_start = max(start, float(item.get("start")))
            token_end = min(end, float(item.get("end")))
        except (AttributeError, TypeError, ValueError):
            continue
        token_text = str(item.get("text") or item.get("word") or item.get("token") or "")
        if token_text and token_end > token_start:
            valid_timings.append({"text": token_text, "start": token_start, "end": token_end})
    valid_timings.sort(key=lambda item: (item["start"], item["end"]))

    boundaries: list[float] = []
    if len(valid_timings) >= len(pieces):
        token_weights = [max(1, len(re.sub(r"\s+", "", item["text"]))) for item in valid_timings]
        piece_weights = [max(1, len(re.sub(r"\s+", "", piece))) for piece in pieces]
        total_token_weight = sum(token_weights)
        total_piece_weight = sum(piece_weights)
        token_cumulative = []
        running = 0
        for weight in token_weights:
            running += weight
            token_cumulative.append(running)
        previous_cut = 0
        piece_running = 0
        for piece_index, piece_weight in enumerate(piece_weights[:-1]):
            piece_running += piece_weight
            target = total_token_weight * piece_running / total_piece_weight
            minimum_cut = previous_cut + 1
            maximum_cut = len(valid_timings) - (len(pieces) - piece_index - 1)
            cut = min(
                range(minimum_cut, maximum_cut + 1),
                key=lambda value: abs(token_cumulative[value - 1] - target),
            )
            left = valid_timings[cut - 1]
            right = valid_timings[cut]
            boundary = right["start"] if right["start"] >= left["end"] else left["end"]
            boundaries.append(max(start, min(end, boundary)))
            previous_cut = cut

    if len(boundaries) != len(pieces) - 1:
        duration = max(0.001, end - start)
        weights = [max(1, len(re.sub(r"\s+", "", piece))) for piece in pieces]
        total_weight = sum(weights)
        running = 0
        boundaries = []
        for weight in weights[:-1]:
            running += weight
            boundaries.append(start + duration * running / total_weight)

    cursor = start
    result = []
    for index, piece in enumerate(pieces):
        piece_end = end if index == len(pieces) - 1 else boundaries[index]
        piece_end = max(cursor + 0.001, min(end, piece_end))
        result.append({"start": round(cursor, 3), "end": round(piece_end, 3), "text": piece})
        cursor = piece_end
    return result
