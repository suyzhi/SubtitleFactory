"""macOS Keychain-backed provider secrets for packaged builds."""

from __future__ import annotations

import os

from ..models.database import get_db
from ..utils.config import is_frozen_app


SERVICE = "com.subtitlefactory.desktop.ai"


def keychain_enabled() -> bool:
    override = os.getenv("SUBTITLE_FACTORY_USE_KEYCHAIN", "").strip().lower()
    return is_frozen_app() or override in {"1", "true", "yes", "on"}


def _backend():
    if not keychain_enabled():
        return None
    try:
        import keyring
    except ImportError as error:
        raise RuntimeError("发布版缺少 macOS Keychain 运行时") from error
    return keyring


def get_secret(account: str, legacy: str = "") -> str:
    backend = _backend()
    if backend is None:
        return legacy
    value = backend.get_password(SERVICE, account) or ""
    if not value and legacy:
        backend.set_password(SERVICE, account, legacy)
        value = legacy
    return value


def save_secret(account: str, value: str) -> None:
    backend = _backend()
    if backend is None:
        return
    if value:
        backend.set_password(SERVICE, account, value)
    else:
        try:
            backend.delete_password(SERVICE, account)
        except Exception:
            pass


def migrate_database_secrets() -> int:
    """Move legacy plaintext provider keys into Keychain and clear SQLite."""
    if not keychain_enabled():
        return 0
    db = get_db()
    migrated = 0
    try:
        legacy = db.execute("SELECT provider,api_key FROM ai_settings WHERE id=1").fetchone()
        if legacy and legacy["api_key"]:
            save_secret(f"legacy:{legacy['provider']}", legacy["api_key"])
            db.execute("UPDATE ai_settings SET api_key='',has_api_key=1,keychain_ref=? WHERE id=1", (f"legacy:{legacy['provider']}",))
            migrated += 1
        rows = db.execute(
            "SELECT provider_id,api_key FROM ai_provider_configs WHERE api_key<>''"
        ).fetchall()
        for row in rows:
            save_secret(f"provider:{row['provider_id']}", row["api_key"])
            db.execute(
                "UPDATE ai_provider_configs SET api_key='',has_api_key=1,keychain_ref=? WHERE provider_id=?",
                (f"provider:{row['provider_id']}", row["provider_id"]),
            )
            migrated += 1
        db.commit()
        return migrated
    finally:
        db.close()
