"""Pinned transcription-model catalog and verified App-managed downloader.

Split by concern into:
- definitions: pinned model metadata, checksums, dataclasses, and error types
- storage: resumable downloads, integrity verification, legacy discovery, removal
"""

from __future__ import annotations

import shutil
import urllib.error
import urllib.parse
import urllib.request

from ...utils.config import MLX_MODELS_DIR, QWEN_MLX_MODELS_DIR, WHISPER_MODELS_DIR
from ...utils.task_manager import task_manager
from .definitions import (
    MODEL_CATALOG_BY_ID,
    MODEL_CATEGORY_ORDER,
    QWEN_ASR_CATALOG_BY_ID,
    QWEN_ASR_MODEL_CATALOG,
    WHISPER_CATALOG_BY_ID,
    WHISPER_MODEL_CATALOG,
    CatalogFile,
    ModelDefinition,
    ModelDownloadError,
    ModelVariant,
    model_definition,
    variant_for,
)
from .storage import (
    _DOWNLOAD_LOCK,
    _MANIFEST_NAME,
    _download_file,
    _files_have_expected_sizes,
    _manifest_identity,
    _read_manifest,
    _sha256,
    _verify_file,
    _write_manifest,
    find_legacy_model,
    managed_model_dir,
    managed_model_ready,
    prepare_catalog_model,
    prepare_whisper_model,
    remove_catalog_model,
    resolve_local_model,
    runtime_model_status,
)

__all__ = [
    "CatalogFile",
    "MLX_MODELS_DIR",
    "MODEL_CATALOG_BY_ID",
    "MODEL_CATEGORY_ORDER",
    "ModelDefinition",
    "ModelDownloadError",
    "ModelVariant",
    "QWEN_ASR_CATALOG_BY_ID",
    "QWEN_ASR_MODEL_CATALOG",
    "QWEN_MLX_MODELS_DIR",
    "WHISPER_CATALOG_BY_ID",
    "WHISPER_MODEL_CATALOG",
    "WHISPER_MODELS_DIR",
    "_DOWNLOAD_LOCK",
    "_MANIFEST_NAME",
    "_download_file",
    "_files_have_expected_sizes",
    "_manifest_identity",
    "_read_manifest",
    "_sha256",
    "_verify_file",
    "_write_manifest",
    "find_legacy_model",
    "managed_model_dir",
    "managed_model_ready",
    "model_definition",
    "prepare_catalog_model",
    "prepare_whisper_model",
    "remove_catalog_model",
    "resolve_local_model",
    "runtime_model_status",
    "shutil",
    "task_manager",
    "urllib",
    "variant_for",
]
