"""Editable AI-assisted publication packs derived from reviewed subtitles.

Split by concern into:
- prompts: section schemas, prompts, source chunking/compaction, JSON LLM validation
- service: pack/section CRUD and background generation orchestration
- exporter: Markdown and ZIP archive generation
"""

from __future__ import annotations

from ...utils.config import EXPORTS_DIR
from ...utils.task_manager import task_manager
from ..ai_providers import assigned_provider
from .exporter import _section, _timestamp, export_content_pack
from .prompts import (
    SECTION_ORDER,
    SECTION_TITLES,
    ContentPackError,
    _call_json,
    _compact_source,
    _extract_json,
    _find_segment,
    _fingerprint,
    _now,
    _project_source,
    _section_prompt,
    _source_chunks,
    _validate_section,
)
from .service import (
    _load_pack,
    create_content_pack,
    delete_pack,
    generate_content_pack,
    list_content_packs,
    regenerate_section,
    update_pack,
    update_section,
)

__all__ = [
    "ContentPackError",
    "EXPORTS_DIR",
    "SECTION_ORDER",
    "SECTION_TITLES",
    "_call_json",
    "_compact_source",
    "_extract_json",
    "_find_segment",
    "_fingerprint",
    "_load_pack",
    "_now",
    "_project_source",
    "_section",
    "_section_prompt",
    "_source_chunks",
    "_timestamp",
    "_validate_section",
    "assigned_provider",
    "create_content_pack",
    "delete_pack",
    "export_content_pack",
    "generate_content_pack",
    "list_content_packs",
    "regenerate_section",
    "task_manager",
    "update_pack",
    "update_section",
]
