"""
字幕工厂 - 字幕导出服务

支持导出 SRT、VTT、ASS、双语字幕格式。
"""

import os
import re
import logging
from typing import List, Optional
from datetime import timedelta

from ..utils.config import EXPORTS_DIR
from ..utils.task_manager import task_manager

logger = logging.getLogger(__name__)


def export_srt(segments: list, output_path: str, bilingual: bool = False,
               primary_lang: str = "original", task_id: str = None):
    """
    导出 SRT 格式字幕。
    bilingual=True 时生成双语字幕（原文+译文 或 译文+原文）。
    """
    lines = []
    for seg in segments:
        idx = seg["index"]
        start = _format_srt_time(seg["start"])
        end = _format_srt_time(seg["end"])

        text = seg.get("clean_text") or seg["raw_text"]
        translated = seg.get("translated_text", "")

        if bilingual and translated:
            if primary_lang == "original":
                subtitle_text = f"{text}\n{translated}"
            else:
                subtitle_text = f"{translated}\n{text}"
        else:
            subtitle_text = text

        lines.append(f"{idx}")
        lines.append(f"{start} --> {end}")
        lines.append(subtitle_text)
        lines.append("")

    content = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    file_size = os.path.getsize(output_path)
    logger.info(f"[Export] SRT 导出完成: {output_path}")
    if task_id:
        task_manager.update_task(
            task_id,
            details={
                "format": "srt",
                "segments_count": len(segments),
                "output_path": output_path,
                "file_size": file_size,
                "bilingual": bilingual,
            }
        )
        task_manager.add_log(
            task_id, "info", "exporting", "SRT 导出完成",
            detail=f"路径: {output_path}, 大小: {file_size/1024:.1f}KB, 字幕数: {len(segments)}"
        )
    return output_path


def export_vtt(segments: list, output_path: str, bilingual: bool = False,
               primary_lang: str = "original", task_id: str = None):
    """导出 VTT 格式字幕"""
    lines = ["WEBVTT", ""]

    for seg in segments:
        start = _format_vtt_time(seg["start"])
        end = _format_vtt_time(seg["end"])

        text = seg.get("clean_text") or seg["raw_text"]
        translated = seg.get("translated_text", "")

        if bilingual and translated:
            if primary_lang == "original":
                subtitle_text = f"{text}\n{translated}"
            else:
                subtitle_text = f"{translated}\n{text}"
        else:
            subtitle_text = text

        lines.append(f"{start} --> {end}")
        lines.append(subtitle_text)
        lines.append("")

    content = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    file_size = os.path.getsize(output_path)
    logger.info(f"[Export] VTT 导出完成: {output_path}")
    if task_id:
        task_manager.update_task(
            task_id,
            details={
                "format": "vtt",
                "segments_count": len(segments),
                "output_path": output_path,
                "file_size": file_size,
                "bilingual": bilingual,
            }
        )
        task_manager.add_log(
            task_id, "info", "exporting", "VTT 导出完成",
            detail=f"路径: {output_path}, 大小: {file_size/1024:.1f}KB, 字幕数: {len(segments)}"
        )
    return output_path


def export_ass(segments: list, output_path: str, bilingual: bool = False,
               primary_lang: str = "original",
               font_name: str = "Arial", font_size: int = 16,
               style_name: str = "Default",
               secondary_style: str = "Secondary",
               settings: dict | None = None,
               task_id: str = None):
    """
    导出 ASS 格式字幕。
    支持双语双样式：原文一种样式，译文另一种样式（小字号、灰白色）。
    """
    import pysubs2

    settings = settings or {}
    raw_font = str(settings.get("fontFamily") or font_name)
    font_name = raw_font.split(",", 1)[0].strip().strip('"\'') or font_name
    font_size = int(settings.get("originalFontSize") or settings.get("fontSize") or font_size)
    secondary_size = int(settings.get("translatedFontSize") or max(8, font_size - 2))
    vertical_position = max(5, min(95, float(settings.get("verticalPosition", 88))))
    margin_vertical = max(8, round((100 - vertical_position) / 100 * 1080))
    background_mode = settings.get("backgroundMode", "none")
    shadow_enabled = bool(settings.get("shadow", True))

    def color(value: str, fallback: tuple[int, int, int]):
        value = str(value or "").lstrip("#")
        try:
            return pysubs2.Color(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
        except (ValueError, IndexError):
            return pysubs2.Color(*fallback)

    original_color = color(settings.get("originalTextColor") or settings.get("textColor"), (255, 255, 255))
    translated_color = color(settings.get("translatedTextColor") or settings.get("textColor"), (220, 220, 220))
    def ass_color(value: str, fallback: str) -> str:
        raw = str(value or fallback).lstrip("#")
        try: red, green, blue = int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
        except (ValueError, IndexError): red, green, blue = 255, 255, 255
        return f"&H{blue:02X}{green:02X}{red:02X}&"
    original_override = ass_color(settings.get("originalTextColor") or settings.get("textColor"), "#ffffff")
    translated_override = ass_color(settings.get("translatedTextColor") or settings.get("textColor"), "#dddddd")
    box_color = pysubs2.Color(255, 255, 255) if background_mode == "white" else pysubs2.Color(0, 0, 0)

    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = "1920"
    subs.info["PlayResY"] = "1080"
    subs.styles[style_name] = pysubs2.SSAStyle(
        fontname=font_name,
        fontsize=font_size,
        primarycolor=original_color,
        secondarycolor=pysubs2.Color(255, 255, 255),
        outlinecolor=pysubs2.Color(0, 0, 0),
        backcolor=box_color,
        bold=False,
        italic=False,
        underline=False,
        strikeout=False,
        scalex=100,
        scaley=100,
        spacing=0,
        angle=0,
        borderstyle=3 if background_mode in {"black", "white"} else 1,
        outline=1.5 if background_mode == "none" else 0,
        shadow=1 if shadow_enabled else 0,
        alignment=pysubs2.Alignment.BOTTOM_CENTER,
        marginl=20,
        marginr=20,
        marginv=margin_vertical,
        alphalevel=0,
        encoding=1,
    )

    subs.styles[secondary_style] = pysubs2.SSAStyle(
        fontname=font_name,
        fontsize=secondary_size,
        primarycolor=translated_color,
        secondarycolor=pysubs2.Color(200, 200, 200),
        outlinecolor=pysubs2.Color(0, 0, 0),
        backcolor=box_color,
        bold=False,
        italic=False,
        underline=False,
        strikeout=False,
        scalex=100,
        scaley=100,
        spacing=0,
        angle=0,
        borderstyle=3 if background_mode in {"black", "white"} else 1,
        outline=1.0 if background_mode == "none" else 0,
        shadow=1 if shadow_enabled else 0,
        alignment=pysubs2.Alignment.BOTTOM_CENTER,
        marginl=20,
        marginr=20,
        marginv=margin_vertical,
        alphalevel=0,
        encoding=1,
    )

    for seg in segments:
        start_ms = int(seg["start"] * 1000)
        end_ms = int(seg["end"] * 1000)

        text = seg.get("clean_text") or seg["raw_text"]
        translated = seg.get("translated_text", "")

        if bilingual and translated:
            if primary_lang == "original":
                subtitle_text = f"{{\\fs{font_size}\\c{original_override}}}{text}\\N{{\\fs{secondary_size}\\c{translated_override}}}{translated}"
            else:
                subtitle_text = f"{{\\fs{secondary_size}\\c{translated_override}}}{translated}\\N{{\\fs{font_size}\\c{original_override}}}{text}"
        else:
            subtitle_text = text

        event = pysubs2.SSAEvent(
            start=start_ms,
            end=end_ms,
            text=subtitle_text,
            style=style_name,
        )
        subs.events.append(event)

    subs.save(output_path)
    file_size = os.path.getsize(output_path)
    logger.info(f"[Export] ASS 导出完成: {output_path}")
    if task_id:
        task_manager.update_task(
            task_id,
            details={
                "format": "ass",
                "segments_count": len(segments),
                "output_path": output_path,
                "file_size": file_size,
                "bilingual": bilingual,
            }
        )
        task_manager.add_log(
            task_id, "info", "exporting", "ASS 导出完成",
            detail=f"路径: {output_path}, 大小: {file_size/1024:.1f}KB, 字幕数: {len(segments)}"
        )
    return output_path


def export_bilingual_srt(segments: list, output_path: str,
                         primary_lang: str = "original", task_id: str = None):
    """导出双语 SRT（等同于 bilingual=True 的 SRT 导出）"""
    return export_srt(segments, output_path, bilingual=True, primary_lang=primary_lang, task_id=task_id)


def get_subtitle_path(project_id: str, fmt: str) -> str:
    """获取字幕导出路径"""
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    return os.path.join(EXPORTS_DIR, f"{project_id}_subtitles.{fmt}")


# ── 时间格式化工具 ──

def _format_srt_time(seconds: float) -> str:
    """将秒转换为 SRT 时间格式: HH:MM:SS,mmm"""
    td = timedelta(seconds=seconds)
    total_ms = int(td.total_seconds() * 1000)
    h = total_ms // 3600000
    m = (total_ms % 3600000) // 60000
    s = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_vtt_time(seconds: float) -> str:
    """将秒转换为 VTT 时间格式: HH:MM:SS.mmm"""
    td = timedelta(seconds=seconds)
    total_ms = int(td.total_seconds() * 1000)
    h = total_ms // 3600000
    m = (total_ms % 3600000) // 60000
    s = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
