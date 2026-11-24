"""Transactional subtitle editing, drafts, and persistent history.

Split by concern into:
- operations: segment mutation appliers, timeline validation, snapshot helpers
- service: transactional operation execution, snapshot import, undo/redo, drafts
"""

from __future__ import annotations

from .operations import (
    APPLIERS,
    SEGMENT_COLUMNS,
    EditorServiceError,
    _apply_assign_speaker,
    _apply_delete,
    _apply_merge,
    _apply_replace,
    _apply_shift,
    _apply_split,
    _apply_update_many,
    _language_join,
    _now,
    _now_milliseconds,
    _project_revision,
    _remember_human_translations,
    _renumber,
    _restore_snapshot,
    _rows,
    _snapshot,
    _speaker,
    _split_offset,
    _split_text,
    _validate_timeline,
)
from .service import (
    commit_draft,
    discard_draft,
    execute_operation,
    get_draft,
    history_step,
    import_segment_snapshot,
    save_draft,
)

__all__ = [
    "APPLIERS",
    "EditorServiceError",
    "SEGMENT_COLUMNS",
    "_apply_assign_speaker",
    "_apply_delete",
    "_apply_merge",
    "_apply_replace",
    "_apply_shift",
    "_apply_split",
    "_apply_update_many",
    "_language_join",
    "_now",
    "_now_milliseconds",
    "_project_revision",
    "_remember_human_translations",
    "_renumber",
    "_restore_snapshot",
    "_rows",
    "_snapshot",
    "_speaker",
    "_split_offset",
    "_split_text",
    "_validate_timeline",
    "commit_draft",
    "discard_draft",
    "execute_operation",
    "get_draft",
    "history_step",
    "import_segment_snapshot",
    "save_draft",
]
