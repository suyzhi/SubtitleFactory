"""YouTube download service using the bundled yt-dlp Python API.

Split by concern into:
- error_classifier: exception unwrapping, redaction, and user-facing classification
- media_probe: ffprobe integrity verification, staging lookup, promotion, cleanup
- ytdlp_runner: URL normalization, yt-dlp options/execution, download orchestration
"""

from __future__ import annotations

import time

from ...utils.config import DOWNLOADS_DIR
from ...utils.task_manager import task_manager
from ..download_errors import DownloadServiceError
from ..runtime_diagnostics import (
    resolve_deno_path,
    resolve_ffmpeg_path,
    resolve_ffprobe_path,
    resolve_js_runtime,
    resolve_quickjs_path,
)
from .error_classifier import (
    _AUTH_CHALLENGE_MARKERS,
    _COOKIE_ACCESS_MARKERS,
    _MEDIA_STREAM_AUTH_MARKERS,
    _classify_download_error,
    _exception_chain,
    _has_exception_type,
    _needs_browser_auth,
    _safe_error_text,
)
from .media_probe import (
    _IMAGE_EXTENSIONS,
    _cleanup_task_staging,
    _find_downloaded_audio,
    _find_final_video,
    _find_thumbnail,
    _probe_media,
    _promote_candidate,
    _selected_format_id,
    remove_managed_download_file,
)
from .ytdlp_runner import (
    _MEDIA_RETRY_CLIENTS,
    _PLAYBACK_QUERY_KEYS,
    _YOUTUBE_HOSTS,
    _download_options,
    _execute_youtube_operation,
    _LazyYtDlp,
    _resolve_deno_path,
    _resolve_js_runtime,
    _update_download_details,
    _with_player_client,
    download_audio_source,
    download_video,
    extract_youtube_info,
    get_video_info,
    logger,
    normalize_youtube_url,
    yt_dlp,
)

__all__ = [
    "DOWNLOADS_DIR",
    "DownloadServiceError",
    "_AUTH_CHALLENGE_MARKERS",
    "_COOKIE_ACCESS_MARKERS",
    "_IMAGE_EXTENSIONS",
    "_LazyYtDlp",
    "_MEDIA_RETRY_CLIENTS",
    "_MEDIA_STREAM_AUTH_MARKERS",
    "_PLAYBACK_QUERY_KEYS",
    "_YOUTUBE_HOSTS",
    "_classify_download_error",
    "_cleanup_task_staging",
    "_download_options",
    "_exception_chain",
    "_execute_youtube_operation",
    "_find_downloaded_audio",
    "_find_final_video",
    "_find_thumbnail",
    "_has_exception_type",
    "_needs_browser_auth",
    "_probe_media",
    "_promote_candidate",
    "_resolve_deno_path",
    "_resolve_js_runtime",
    "_safe_error_text",
    "_selected_format_id",
    "_update_download_details",
    "_with_player_client",
    "download_audio_source",
    "download_video",
    "extract_youtube_info",
    "get_video_info",
    "logger",
    "normalize_youtube_url",
    "remove_managed_download_file",
    "resolve_deno_path",
    "resolve_ffmpeg_path",
    "resolve_ffprobe_path",
    "resolve_js_runtime",
    "resolve_quickjs_path",
    "task_manager",
    "time",
    "yt_dlp",
]
