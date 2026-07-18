"""Strict SRT, WebVTT, and ASS subtitle parsing."""

from __future__ import annotations

import re


TIME_RE = re.compile(r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{1,3})")


def _seconds(value: str) -> float:
    match = TIME_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"无法识别时间码：{value}")
    parts = match.groupdict()
    milliseconds = int(parts["ms"].ljust(3, "0"))
    return int(parts["h"]) * 3600 + int(parts["m"]) * 60 + int(parts["s"]) + milliseconds / 1000


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\{\\[^}]+\}", "", value)
    return value.replace("\\N", "\n").strip()


def parse_srt_or_vtt(content: str) -> list[dict]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    normalized = re.sub(r"^WEBVTT[^\n]*\n+", "", normalized)
    cues = []
    for block in re.split(r"\n{2,}", normalized):
        lines = [line for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        timing = lines[timing_index].split("-->")
        start_text = timing[0].strip()
        end_text = timing[1].strip().split()[0]
        cues.append({"start": _seconds(start_text), "end": _seconds(end_text), "text": _clean_text("\n".join(lines[timing_index + 1:]))})
    return cues


def parse_ass(content: str) -> list[dict]:
    cues = []
    for raw in content.replace("\r\n", "\n").splitlines():
        if not raw.lstrip().lower().startswith("dialogue:"):
            continue
        fields = raw.split(":", 1)[1].split(",", 9)
        if len(fields) < 10:
            continue
        start, end, text = fields[1], fields[2], fields[9]
        # ASS uses centiseconds; normalize to the shared parser.
        def ass_time(value: str) -> str:
            head, fraction = value.strip().split(".", 1)
            return f"{head},{fraction.ljust(3, '0')[:3]}"
        cues.append({"start": _seconds(ass_time(start)), "end": _seconds(ass_time(end)), "text": _clean_text(text)})
    return cues


def parse_subtitle(content: bytes, filename: str) -> list[dict]:
    text = None
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("字幕文件编码无法识别")
    extension = filename.lower().rsplit(".", 1)[-1]
    if extension in {"srt", "vtt"}:
        cues = parse_srt_or_vtt(text)
    elif extension == "ass":
        cues = parse_ass(text)
    else:
        raise ValueError("仅支持 SRT、VTT 和 ASS 字幕")
    if not cues:
        raise ValueError("字幕文件中没有可导入的时间段")
    previous_end = -1.0
    for cue in cues:
        if cue["start"] < 0 or cue["end"] <= cue["start"]:
            raise ValueError("字幕包含非法时间范围")
        if cue["start"] < previous_end - 0.001:
            raise ValueError("字幕包含重叠时间段，请先在字幕软件中修复")
        previous_end = cue["end"]
    return cues
