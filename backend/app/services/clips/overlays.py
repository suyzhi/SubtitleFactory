"""Pillow subtitle overlay rendering and FFmpeg filter graph construction for short clips."""

from __future__ import annotations

import json
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...models.database import get_db
from .common import ASPECT_DIMENSIONS, ClipError


def _subtitle_rows(
    candidate: dict[str, Any],
    layout: dict[str, Any],
    project_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    db = get_db()
    try:
        rows = [
            dict(row)
            for row in db.execute(
                """SELECT * FROM segments
                    WHERE project_id=? AND end>? AND start<?
                    ORDER BY idx""",
                (project_id, candidate["start"], candidate["end"]),
            ).fetchall()
        ]
        style_row = db.execute(
            "SELECT settings_json FROM project_styles WHERE project_id=?", (project_id,)
        ).fetchone()
    finally:
        db.close()
    shifted = []
    duration = float(candidate["end"]) - float(candidate["start"])
    for row in rows:
        row["start"] = max(0, float(row["start"]) - float(candidate["start"]))
        row["end"] = min(duration, float(row["end"]) - float(candidate["start"]))
        if row["end"] > row["start"]:
            shifted.append(row)
    style = json.loads(style_row["settings_json"]) if style_row else {}
    style.update(layout.get("style") or {})
    return shifted, style


@lru_cache(maxsize=32)
def _font_path(font_family: str, needs_cjk: bool) -> str:
    # Match actual font families, never a substring (Inter used to match SignPainter).
    from PIL import ImageFont

    roots = [
        Path.home() / "Library/Fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
        Path("/System/Library/Fonts/Supplemental"),
    ]

    def normalize(value: str | None) -> str:
        return "".join(c for c in (value or "").lower() if c.isalnum())

    requested = [normalize(value.strip().strip("\"'")) for value in font_family.split(",")]
    fonts: dict[str, str] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if path.suffix.lower() not in {".ttf", ".ttc", ".otf"}:
                continue
            try:
                family, variant = ImageFont.truetype(str(path), 20).getname()
                token = normalize(family)
                if token not in fonts or (variant or "").lower() in {"regular", "normal", "book"}:
                    fonts[token] = str(path)
            except (OSError, ValueError):
                continue
    candidates = requested + (
        ["pingfangsc", "hiraginosansgb", "arialunicode"] if needs_cjk else []
    )
    candidates += ["helveticaneue", "arial", "helvetica"]
    for token in candidates:
        if token in fonts:
            return fonts[token]
    raise RuntimeError("找不到可用的字幕字体，请安装 Arial 或系统黑体")


def _hex_rgba(value: Any, fallback: str, alpha: int = 255) -> tuple[int, int, int, int]:
    raw = str(value or fallback).strip().lstrip("#")
    try:
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16), alpha
    except (ValueError, IndexError):
        default = fallback.lstrip("#")
        return int(default[0:2], 16), int(default[2:4], 16), int(default[4:6], 16), alpha


def _wrap_caption(draw: Any, text: str, font: Any, maximum: float) -> list[str]:
    text = " ".join(str(text or "").split())
    if not text:
        return []
    units = text.split(" ") if " " in text else list(text)
    separator = " " if " " in text else ""
    lines: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}{separator if current else ''}{unit}"
        if current and draw.textlength(candidate, font=font) > maximum:
            lines.append(current)
            current = unit
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _subtitle_overlay_paths(
    task_id: str,
    candidate: dict[str, Any],
    layout: dict[str, Any],
    project_id: str,
) -> list[dict[str, Any]]:
    """Pre-render captions when the bundled FFmpeg has no libass filter."""
    mode = layout["subtitle_mode"]
    if mode == "off":
        return []
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise ClipError(
            "短片字幕渲染组件缺失",
            error_code="CLIP_SUBTITLE_RUNTIME_MISSING",
            recoverable=False,
        ) from exc
    rows, style = _subtitle_rows(candidate, layout, project_id)
    render_size = layout.get("render_size")
    width, height = (
        (int(render_size[0]), int(render_size[1]))
        if isinstance(render_size, (list, tuple)) and len(render_size) == 2
        else ASPECT_DIMENSIONS[layout["aspect_ratio"]]
    )
    scale = height / 360 if render_size else min(width, height) / 1080
    original_size = max(
        18, round(float(style.get("originalFontSize") or style.get("fontSize") or 46) * scale)
    )
    translated_size = max(
        16, round(float(style.get("translatedFontSize") or original_size * 0.82) * scale)
    )
    font_family = str(style.get("fontFamily") or "Arial")
    vertical_position = max(10, min(94, float(style.get("verticalPosition", 88))))
    margin = max(round(height * 0.055), 28)
    maximum_width = min(
        width - margin * 2, width * max(0.2, min(1, float(style.get("maxWidth", 85)) / 100))
    )
    background_mode = str(style.get("backgroundMode") or "none")
    shadow = bool(style.get("shadow", True))
    assets: list[dict[str, Any]] = []
    for row in rows:
        original = str(row.get("clean_text") or row.get("raw_text") or "")
        translated = str(row.get("translated_text") or "")
        lines: list[tuple[str, str]] = []
        if mode == "translated":
            lines.append(("translated", translated or original))
        elif mode == "bilingual" and translated:
            lines.extend((("original", original), ("translated", translated)))
        else:
            lines.append(("original", original))
        needs_cjk = any(
            "\u3400" <= character <= "\u9fff" for _kind, text in lines for character in text
        )
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        fitted_original, fitted_translated = original_size, translated_size
        rendered: list[tuple[str, str, Any]] = []
        dimensions: list[tuple[float, float, float, float]] = []
        maximum_height = height * (0.32 if height > width else 0.28)
        maximum_lines = 5 if height > width else 4
        while True:
            original_font = ImageFont.truetype(_font_path(font_family, needs_cjk), fitted_original)
            translated_font = ImageFont.truetype(
                _font_path(font_family, needs_cjk), fitted_translated
            )
            rendered = []
            for kind, text in lines:
                font = translated_font if kind == "translated" else original_font
                rendered.extend(
                    (kind, wrapped, font)
                    for wrapped in _wrap_caption(draw, text, font, maximum_width)
                )
            spacing = max(5, round(fitted_original * 0.18))
            dimensions = [
                draw.textbbox(
                    (0, 0),
                    text,
                    font=font,
                    stroke_width=max(1, round(font.size * 0.055)),
                )
                for _kind, text, font in rendered
            ]
            measured_height = sum(box[3] - box[1] for box in dimensions) + spacing * max(
                0, len(rendered) - 1
            )
            if (
                measured_height <= maximum_height and len(rendered) <= maximum_lines
            ) or fitted_original <= 20:
                break
            fitted_original = max(20, round(fitted_original * 0.88))
            fitted_translated = max(18, round(fitted_translated * 0.88))
        if not rendered:
            continue
        total_height = sum(box[3] - box[1] for box in dimensions) + spacing * (len(rendered) - 1)
        top = min(
            height - margin - total_height, vertical_position / 100 * (height - total_height)
        )
        top = max(margin, top)
        if background_mode in {"black", "white"}:
            widest = max(box[2] - box[0] for box in dimensions)
            padding = max(10, round(original_size * 0.28))
            fill = (255, 255, 255, 218) if background_mode == "white" else (0, 0, 0, 205)
            draw.rounded_rectangle(
                (
                    (width - widest) / 2 - padding,
                    top - padding,
                    (width + widest) / 2 + padding,
                    top + total_height + padding,
                ),
                radius=padding,
                fill=fill,
            )
        cursor = top
        for (kind, text, font), box in zip(rendered, dimensions, strict=True):
            line_width = box[2] - box[0]
            line_height = box[3] - box[1]
            color = _hex_rgba(
                style.get("translatedTextColor")
                if kind == "translated"
                else style.get("originalTextColor") or style.get("textColor"),
                "#dddddd" if kind == "translated" else "#ffffff",
            )
            stroke_width = max(1, round(font.size * 0.055))
            draw.text(
                ((width - line_width) / 2, cursor - box[1]),
                text,
                font=font,
                fill=color,
                stroke_width=stroke_width,
                stroke_fill=(0, 0, 0, 225)
                if shadow or background_mode == "none"
                else (0, 0, 0, 0),
            )
            cursor += line_height + spacing
        path = Path(tempfile.gettempdir()) / (
            f"subtitle-factory-clip-{task_id}-{candidate['id']}-"
            f"{layout['aspect_ratio'].replace(':', 'x')}-{row['idx']}.tiff"
        )
        canvas.save(path, format="TIFF", compression="tiff_lzw")
        assets.append({"path": str(path), "start": row["start"], "end": row["end"]})
    return assets


def _filter_graph(
    width: int,
    height: int,
    layout: dict[str, Any],
    subtitle_path: str | None,
    subtitle_overlays: list[dict[str, Any]] | None = None,
) -> str:
    if layout["composition"] == "passthrough":
        graph = "[0:v]null[base]"
    elif layout["composition"] == "blur":
        graph = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma=24:steps=2[bg2];"
            f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fg2];"
            f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2[base]"
        )
    else:
        x = max(0, min(1, float(layout["focal_x"])))
        y = max(0, min(1, float(layout["focal_y"])))
        graph = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:x='max(0,(in_w-out_w)*{x:.4f})':"
            f"y='max(0,(in_h-out_h)*{y:.4f})'[base]"
        )
    if subtitle_path:
        escaped = subtitle_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\''")
        graph += f";[base]subtitles=filename='{escaped}'[v]"
    elif subtitle_overlays:
        previous = "base"
        for index, overlay in enumerate(subtitle_overlays):
            escaped = (
                str(overlay["path"])
                .replace("\\", "\\\\")
                .replace(":", "\\:")
                .replace("'", "'\\''")
            )
            output = "v" if index == len(subtitle_overlays) - 1 else f"caption{index}"
            graph += (
                f";movie=filename='{escaped}',format=rgba[subtitle{index}];"
                f"[{previous}][subtitle{index}]overlay=0:0:eof_action=repeat:shortest=0:"
                f"enable='between(t,{float(overlay['start']):.3f},{float(overlay['end']):.3f})'[{output}]"
            )
            previous = output
    else:
        graph += ";[base]null[v]"
    return graph
