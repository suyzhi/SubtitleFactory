"""Local, non-secret application runtime settings persistence."""

from __future__ import annotations

import json
import time
from typing import Any, Mapping

from ..models.database import get_db


APP_SETTINGS_DEFAULTS: dict[str, Any] = {
    "default_workflow": "automatic",
    "auto_save": True,
    "startup_behavior": "restore_last",
    # A release install must be usable without Memo or a developer-only path.
    "default_model": "small",
    "source_language": "auto",
    "custom_model_path": None,
    "coreml_model_path": None,
    "coreml_cli_path": None,
    "translation_target_language": "zh",
    "bilingual_order": "original_first",
    "favorite_languages": ["zh", "en", "ja", "ko"],
    "download_quality": "best",
    "download_container": "mp4",
    "ffmpeg_path": None,
    "yt_dlp_path": None,
    "download_directory": None,
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


def save_app_settings(updates: Mapping[str, Any]) -> dict[str, Any]:
    """Merge and persist already-validated updates."""
    unknown = set(updates) - set(APP_SETTINGS_DEFAULTS)
    if unknown:
        raise ValueError(f"不支持的设置字段: {', '.join(sorted(unknown))}")
    settings = get_app_settings()
    settings.update(updates)
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
