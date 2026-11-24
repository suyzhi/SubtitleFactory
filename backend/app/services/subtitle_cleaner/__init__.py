"""AI subtitle restructuring: join ASR fragments into complete spoken sentences.

Split by concern into:
- batching: semantic batch construction, recovery split, token budgets, JSON helpers
- llm: prompt definition, LLM calls, adaptive splitting, faithful-rewrite checks
- pipeline: full clean workflow, failed-batch retry, undo, atomic DB reconciliation
"""

from __future__ import annotations

from ...utils.task_manager import task_manager
from ..ai_providers import assigned_provider
from ..ai_settings import get_ai_settings
from .batching import (
    _batch_character_count,
    _build_semantic_batches,
    _extract_json,
    _fingerprint,
    _join_text,
    _now,
    _output_token_budget,
    _split_batch_for_recovery,
    _split_run,
    _validate_batch_results,
)
from .llm import (
    CLEANER_SYSTEM_PROMPT,
    AIOutputLengthError,
    _call_llm_group,
    _is_faithful_rewrite,
    _validate_grouped_results,
    logger,
)
from .pipeline import (
    BatchRetryError,
    _commit_restructured_segments,
    _compose_final_segments,
    _insert_segment,
    clean_subtitles,
    retry_clean_batch,
    undo_last_clean,
)

__all__ = [
    "AIOutputLengthError",
    "BatchRetryError",
    "CLEANER_SYSTEM_PROMPT",
    "_batch_character_count",
    "_build_semantic_batches",
    "_call_llm_group",
    "_commit_restructured_segments",
    "_compose_final_segments",
    "_extract_json",
    "_fingerprint",
    "_insert_segment",
    "_is_faithful_rewrite",
    "_join_text",
    "_now",
    "_output_token_budget",
    "_split_batch_for_recovery",
    "_split_run",
    "_validate_batch_results",
    "_validate_grouped_results",
    "assigned_provider",
    "clean_subtitles",
    "get_ai_settings",
    "logger",
    "retry_clean_batch",
    "task_manager",
    "undo_last_clean",
]
