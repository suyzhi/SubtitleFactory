"""
字幕工厂（简洁版）- 字幕导出服务

支持导出单语与双语 SRT 字幕格式。
"""

import logging
import math
import os

from ..utils.config import EXPORTS_DIR
from ..utils.task_manager import task_manager

logger = logging.getLogger(__name__)


def _cue_text(value: str) -> str:
    """SRT 以空行分隔字幕块，正文里的空行会把一条字幕截断。

    导出时只去掉空行与仅含空白的行，其余换行保持不变。
    """
    lines = str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line for line in lines if line.strip())


def export_srt(
    segments: list,
    output_path: str,
    bilingual: bool = False,
    primary_lang: str = "original",
    task_id: str | None = None,
):
    """导出 SRT 格式字幕；bilingual=True 时生成双语字幕。"""
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
        elif primary_lang == "translated" and translated:
            subtitle_text = translated
        else:
            subtitle_text = text

        lines.append(f"{idx}")
        lines.append(f"{start} --> {end}")
        lines.append(_cue_text(subtitle_text))
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
            },
        )
        task_manager.add_log(
            task_id,
            "info",
            "exporting",
            "SRT 导出完成",
            detail=f"路径: {output_path}, 大小: {file_size / 1024:.1f}KB, 字幕数: {len(segments)}",
        )
    return output_path


def export_bilingual_srt(
    segments: list,
    output_path: str,
    primary_lang: str = "original",
    task_id: str | None = None,
):
    """导出双语 SRT（等同于 bilingual=True 的 SRT 导出）。"""
    return export_srt(
        segments,
        output_path,
        bilingual=True,
        primary_lang=primary_lang,
        task_id=task_id,
    )


def get_subtitle_path(project_id: str, fmt: str) -> str:
    """获取字幕导出路径。"""
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    return os.path.join(EXPORTS_DIR, f"{project_id}_subtitles.{fmt}")


def _rounded_units(seconds: float, units_per_second: int) -> int:
    """把秒四舍五入为整数个时间单位（毫秒），负值按 0 处理。

    多数毫秒值无法用浮点秒精确表示（1.001 实际是 1.000999…），直接截断会让
    这类时间码提前一个单位。先取整、再拆分时/分/秒，进位也随之正确
    （59.9996 秒 → 00:01:00,000）。
    """
    return max(0, math.floor(float(seconds) * units_per_second + 0.5))


def _milliseconds(seconds: float) -> int:
    return _rounded_units(seconds, 1000)


def _format_srt_time(seconds: float) -> str:
    """将秒转换为 SRT 时间格式: HH:MM:SS,mmm"""
    total_ms = _milliseconds(seconds)
    h = total_ms // 3600000
    m = (total_ms % 3600000) // 60000
    s = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
