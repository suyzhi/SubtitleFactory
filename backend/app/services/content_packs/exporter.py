"""ZIP and Markdown export formatting for generated content packs."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from ...utils.config import EXPORTS_DIR
from .prompts import facade_attr


def _timestamp(value: float) -> str:
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _section(pack: dict[str, Any], kind: str) -> dict[str, Any]:
    item = next((section for section in pack["sections"] if section["kind"] == kind), None)
    return dict(item.get("content") or {}) if item else {}


def export_content_pack(pack_id: str) -> Path:
    from .service import _load_pack

    pack = _load_pack(pack_id)
    chapters = _section(pack, "chapters").get("chapters") or []
    summary = _section(pack, "summary")
    quotes = _section(pack, "quotes").get("quotes") or []
    youtube = _section(pack, "youtube")
    podcast = _section(pack, "podcast")
    social = _section(pack, "social")
    chapter_text = "\n".join(f"{_timestamp(item['start'])} {item['title']}" for item in chapters)
    summary_text = "\n\n".join(
        [
            str(summary.get("overview") or ""),
            "\n".join(f"- {item}" for item in summary.get("key_points") or []),
            "\n".join(
                f"- [{_timestamp(item['start'])}] {item.get('speaker') or ''}：{item.get('text') or ''}"
                for item in quotes
            ),
        ]
    ).strip()
    youtube_text = "\n\n".join(
        [
            "# 标题候选\n" + "\n".join(f"- {item}" for item in youtube.get("titles") or []),
            "# 简介\n" + str(youtube.get("description") or ""),
            "# 章节\n" + str(youtube.get("chapter_text") or chapter_text),
            "# 标签\n" + " ".join(f"#{item.lstrip('#')}" for item in youtube.get("tags") or []),
        ]
    )
    podcast_text = "\n\n".join(
        [
            f"# {podcast.get('title') or pack['name']}",
            str(podcast.get("intro") or ""),
            str(podcast.get("show_notes") or ""),
        ]
    )
    readme = "\n\n".join(
        [
            f"# {pack['name']}",
            "# 摘要",
            summary_text,
            "# 章节",
            chapter_text,
            "# YouTube",
            youtube_text,
            "# 播客",
            podcast_text,
            "# 小红书",
            str(social.get("xiaohongshu") or ""),
            "# 公众号",
            str(social.get("wechat") or ""),
            "# 通用社交",
            str(social.get("generic") or ""),
        ]
    )
    exports_dir = facade_attr("EXPORTS_DIR", EXPORTS_DIR)
    output = Path(exports_dir) / "content-packs" / f"subtitle-factory-content-{pack_id}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "format": "subtitle-factory-content-pack",
        "version": 1,
        "pack": {
            key: pack[key]
            for key in (
                "id",
                "project_id",
                "name",
                "input_mode",
                "output_language",
                "allow_translation_fallback",
                "source_revision",
                "provider_id",
                "model",
                "created_at",
                "updated_at",
            )
        },
        "sections": pack["sections"],
    }
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.md", readme)
        archive.writestr("summary.txt", summary_text)
        archive.writestr("chapters.txt", chapter_text)
        archive.writestr("youtube.md", youtube_text)
        archive.writestr("podcast.md", podcast_text)
        archive.writestr("social/xiaohongshu.md", str(social.get("xiaohongshu") or ""))
        archive.writestr("social/wechat.md", str(social.get("wechat") or ""))
        archive.writestr("social/generic.md", str(social.get("generic") or ""))
        archive.writestr("data.json", json.dumps(data, ensure_ascii=False, indent=2))
    return output
