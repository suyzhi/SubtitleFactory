"""Verified downloads and long-audio inference for managed sherpa-onnx models.

Split by concern into:
- types: errors, dataclasses, locks, and facade lookup
- storage: manifest verification, resumable download, extraction, preparation, removal
- inference: Silero VAD streaming/prefetch and sherpa-onnx recognizer session factory
"""

from __future__ import annotations

import shutil
import urllib.error
import urllib.request

from ...utils.config import MODELS_DIR
from ...utils.task_manager import task_manager
from .inference import (
    _create_recognizer,
    _find_path,
    _iter_vad_segments,
    _paths,
    _timings_from_result,
    _wave_info,
    create_managed_session,
    iter_vad_audio_segments,
    prefetch_vad_segments,
)
from .storage import (
    _download_verified_file,
    _ensure_vad,
    _manifest_identity,
    _read_manifest,
    _safe_extract_required,
    _sha256,
    _staging_dir,
    _verify_file,
    _write_manifest,
    managed_model_dir,
    managed_model_ready,
    managed_model_status,
    prepare_managed_model,
    recommended_ready_model,
    remove_managed_model,
)
from .types import (
    _DOWNLOAD_LOCK,
    _MANIFEST_NAME,
    _PUNCTUATION_ONLY,
    ManagedModelError,
    ManagedSegment,
    ManagedSession,
)

__all__ = [
    "MODELS_DIR",
    "ManagedModelError",
    "ManagedSegment",
    "ManagedSession",
    "_DOWNLOAD_LOCK",
    "_MANIFEST_NAME",
    "_PUNCTUATION_ONLY",
    "_create_recognizer",
    "_download_verified_file",
    "_ensure_vad",
    "_find_path",
    "_iter_vad_segments",
    "_manifest_identity",
    "_paths",
    "_read_manifest",
    "_safe_extract_required",
    "_sha256",
    "_staging_dir",
    "_timings_from_result",
    "_verify_file",
    "_wave_info",
    "_write_manifest",
    "create_managed_session",
    "iter_vad_audio_segments",
    "managed_model_dir",
    "managed_model_ready",
    "managed_model_status",
    "prefetch_vad_segments",
    "prepare_managed_model",
    "recommended_ready_model",
    "remove_managed_model",
    "shutil",
    "task_manager",
    "urllib",
]
