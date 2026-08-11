"""Local, non-secret application runtime settings persistence."""

from __future__ import annotations

import json
import time
from typing import Any, Mapping

from ..models.database import get_db
from .distribution import distribution_capabilities, is_external_model_reference


APP_SETTINGS_DEFAULTS: dict[str, Any] = {
    "default_workflow": "automatic",
    "auto_save": True,
    "startup_behavior": "restore_last",
    # Automatic selection never downloads a model. It uses a ready
    # language-specific model when available and otherwise falls back to Small.
    "default_model": "auto",
    "source_language": "auto",
    "custom_model_path": None,
    "coreml_model_path": None,
    "coreml_cli_path": None,
    "translation_target_language": "zh",
    "bilingual_order": "original_first",
    "favorite_languages": ["zh", "en", "ja", "ko"],
    "youtube_media_mode": "local",
    "download_quality": "best",
    "download_container": "mp4",
    "ffmpeg_path": None,
    "yt_dlp_path": None,
    "download_directory": None,
    "clean_provider_id": "deepseek",
    "translate_provider_id": "deepseek",
    "content_provider_id": "deepseek",
    "transcription_runtime_by_model": {},
}

_EXTERNAL_PATH_FIELDS = {
    "custom_model_path", "coreml_model_path", "coreml_cli_path",
    "ffmpeg_path", "yt_dlp_path", "download_directory",
}


def _decode_settings(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in APP_SETTINGS_DEFAULTS if key in value}


def get_app_settings() -> dict[str, Any]:
    """Read settings merged with safe release defaults.

    User-selected paths live only in the local SQLite data store. No path is
    sourced from repository configuration or embedded in a release default.
    """
    db = get_db()
    try:
        row = db.execute(
            "SELECT settings_json FROM app_settings WHERE id=1"
        ).fetchone()
    finally:
        db.close()
    settings = dict(APP_SETTINGS_DEFAULTS)
    if row:
        settings.update(_decode_settings(row["settings_json"]))
    return settings


def effective_app_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Project persisted preferences onto capabilities of this distribution."""
    effective = dict(settings)
    if distribution_capabilities().external_runtime_paths:
        return effective
    for field in _EXTERNAL_PATH_FIELDS:
        effective[field] = None
    if is_external_model_reference(effective.get("default_model")):
        effective["default_model"] = "auto"
    runtimes = effective.get("transcription_runtime_by_model")
    if isinstance(runtimes, dict):
        effective["transcription_runtime_by_model"] = {
            model_id: runtime
            for model_id, runtime in runtimes.items()
            if not is_external_model_reference(model_id)
            and runtime != "external_coreml"
        }
    return effective


def get_effective_app_settings() -> dict[str, Any]:
    return effective_app_settings(get_app_settings())


def save_app_settings(updates: Mapping[str, Any]) -> dict[str, Any]:
    """Merge and persist already-validated updates."""
    unknown = set(updates) - set(APP_SETTINGS_DEFAULTS)
    if unknown:
        raise ValueError(f"不支持的设置字段: {', '.join(sorted(unknown))}")
    settings = get_app_settings()
    effective_updates = dict(updates)
    if not distribution_capabilities().external_runtime_paths:
        for field in _EXTERNAL_PATH_FIELDS:
            effective_updates.pop(field, None)
        if is_external_model_reference(effective_updates.get("default_model")):
            effective_updates.pop("default_model", None)
        requested_runtimes = effective_updates.get("transcription_runtime_by_model")
        if isinstance(requested_runtimes, dict):
            existing_runtimes = settings.get("transcription_runtime_by_model")
            preserved = {
                model_id: runtime
                for model_id, runtime in (
                    existing_runtimes.items()
                    if isinstance(existing_runtimes, dict) else []
                )
                if is_external_model_reference(model_id)
                or runtime == "external_coreml"
            }
            allowed = {
                model_id: runtime
                for model_id, runtime in requested_runtimes.items()
                if not is_external_model_reference(model_id)
                and runtime != "external_coreml"
            }
            effective_updates["transcription_runtime_by_model"] = {
                **preserved, **allowed,
            }
    settings.update(effective_updates)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    db = get_db()
    try:
        db.execute(
            """INSERT INTO app_settings (id, settings_json, updated_at)
               VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   settings_json=excluded.settings_json,
                   updated_at=excluded.updated_at""",
            (json.dumps(settings, ensure_ascii=False, separators=(",", ":")), now),
        )
        db.commit()
    finally:
        db.close()
    return settings
