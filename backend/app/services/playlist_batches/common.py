"""Shared constants, errors, download facade, and time helper for playlist batches."""

from __future__ import annotations

import sys
import time
from importlib import import_module
from typing import Any

from ..distribution import require_youtube_feature

PLAYLIST_KIND = "youtube_playlist"
STAGE_ORDER = ("download", "extract_audio", "transcribe", "clean", "translate")
TERMINAL_STAGE_STATES = {"success", "partial", "failed", "cancelled", "skipped"}


def _download_service():
    require_youtube_feature()
    return import_module("..downloader", __package__)


def download_audio_source(*args, **kwargs):
    return _download_service().download_audio_source(*args, **kwargs)


def download_video(*args, **kwargs):
    return _download_service().download_video(*args, **kwargs)


def extract_youtube_info(*args, **kwargs):
    return _download_service().extract_youtube_info(*args, **kwargs)


def remove_managed_download_file(*args, **kwargs):
    return _download_service().remove_managed_download_file(*args, **kwargs)


class PlaylistBatchError(RuntimeError):
    def __init__(
        self,
        message: str,
        code: str,
        *,
        recoverable: bool = True,
        suggestion: str = "",
        actions: list[str] | None = None,
        automatic_retry: bool = False,
    ):
        super().__init__(message)
        self.error_code = code
        self.recoverable = recoverable
        self.automatic_retry = automatic_retry
        self.available_actions = actions if actions is not None else (
            ["retry"] if recoverable else []
        )
        self.suggestion = suggestion or "请检查播放列表状态后重试"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def facade_attr(name: str, default: Any) -> Any:
    pkg = sys.modules.get(__package__ or "")
    return getattr(pkg, name, default) if pkg is not None else default
