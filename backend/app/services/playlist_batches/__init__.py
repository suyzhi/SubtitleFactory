"""YouTube playlist discovery and durable per-video batch orchestration.

Split by concern into:
- common: constants, error types, download facade, and time helper
- preview: playlist URL check, yt-dlp flat preview, validation, and sync
- scheduler: resource-bounded stage queuing, execution, and rollup
- controls: batch queries, pause/resume/cancel/retry, stage enable, recovery
"""

from __future__ import annotations

from ...utils.config import PROJECTS_DIR
from ...utils.task_manager import task_manager
from ..ai_providers import assigned_provider
from ..app_settings import get_effective_app_settings as get_app_settings
from .common import (
    PLAYLIST_KIND,
    STAGE_ORDER,
    TERMINAL_STAGE_STATES,
    PlaylistBatchError,
    _download_service,
    _now,
    download_audio_source,
    download_video,
    extract_youtube_info,
    remove_managed_download_file,
)
from .controls import (
    _task_progress,
    cancel_pending_batch,
    enable_batch_stage,
    get_batch_detail,
    list_playlist_batches,
    pause_batch,
    recover_playlist_batches,
    resume_batch,
    retry_failed,
    sync_playlist_batch,
)
from .preview import (
    _entry_thumbnail,
    _insert_stage_rows,
    _selected_stages,
    _validate_configuration,
    create_or_sync_playlist,
    is_youtube_playlist_url,
    preview_playlist,
)
from .scheduler import (
    _dispatch_batch,
    _dispatch_when_task_terminal,
    _execute_stage,
    _mark_later_stages,
    _queue_next_stage,
    _queue_stage,
    _refresh_batch,
    _refresh_item,
    _resolve_previous_stage_attempts,
    _run_stage,
    _stage_context,
)

__all__ = [
    "PLAYLIST_KIND",
    "PROJECTS_DIR",
    "PlaylistBatchError",
    "STAGE_ORDER",
    "TERMINAL_STAGE_STATES",
    "_dispatch_batch",
    "_dispatch_when_task_terminal",
    "_download_service",
    "_entry_thumbnail",
    "_execute_stage",
    "_insert_stage_rows",
    "_mark_later_stages",
    "_now",
    "_queue_next_stage",
    "_queue_stage",
    "_refresh_batch",
    "_refresh_item",
    "_resolve_previous_stage_attempts",
    "_run_stage",
    "_selected_stages",
    "_stage_context",
    "_task_progress",
    "_validate_configuration",
    "assigned_provider",
    "cancel_pending_batch",
    "create_or_sync_playlist",
    "download_audio_source",
    "download_video",
    "enable_batch_stage",
    "extract_youtube_info",
    "get_app_settings",
    "get_batch_detail",
    "is_youtube_playlist_url",
    "list_playlist_batches",
    "pause_batch",
    "preview_playlist",
    "recover_playlist_batches",
    "remove_managed_download_file",
    "resume_batch",
    "retry_failed",
    "sync_playlist_batch",
    "task_manager",
]
