"""Subtitle-grounded short-clip recommendations and local FFmpeg rendering.

Split by concern into:
- common: aspect ratios, errors, source queries, and fingerprinting
- recommendations: window discovery, AI ranking, and clip-set / layout CRUD
- overlays: Pillow caption overlay rendering and FFmpeg filter graph assembly
- renderer: FFmpeg batch execution, ffprobe validation, and render lifecycle
"""

from __future__ import annotations

from ...utils.config import EXPORTS_DIR
from ...utils.task_manager import task_manager
from ..ai_providers import assigned_provider
from ..content_packs import _call_json
from ..runtime_diagnostics import resolve_ffmpeg_path, resolve_ffprobe_path
from .common import ASPECT_DIMENSIONS, ClipError, _fingerprint, _now, _source
from .overlays import (
    _filter_graph,
    _font_path,
    _hex_rgba,
    _subtitle_overlay_paths,
    _subtitle_rows,
    _wrap_caption,
)
from .recommendations import (
    _candidate_dict,
    _candidate_windows,
    _rank_candidates,
    create_clip_set,
    get_clip_set,
    list_clip_sets,
    recommend_clips,
    update_candidate,
    update_layout,
)
from .renderer import (
    _configuration,
    _probe,
    _run_process,
    create_render_batch,
    delete_render,
    get_render,
    render_batch,
)

__all__ = [
    "ASPECT_DIMENSIONS",
    "ClipError",
    "EXPORTS_DIR",
    "_call_json",
    "_candidate_dict",
    "_candidate_windows",
    "_configuration",
    "_filter_graph",
    "_fingerprint",
    "_font_path",
    "_hex_rgba",
    "_now",
    "_probe",
    "_rank_candidates",
    "_run_process",
    "_source",
    "_subtitle_overlay_paths",
    "_subtitle_rows",
    "_wrap_caption",
    "assigned_provider",
    "create_clip_set",
    "create_render_batch",
    "delete_render",
    "get_clip_set",
    "get_render",
    "list_clip_sets",
    "recommend_clips",
    "render_batch",
    "resolve_ffmpeg_path",
    "resolve_ffprobe_path",
    "task_manager",
    "update_candidate",
    "update_layout",
]
