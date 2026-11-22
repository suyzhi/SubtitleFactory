"""Password-prompt regression: even packaged builds must store API keys locally in SQLite."""
import sys
from unittest.mock import Mock

from app.models import database
from app.services import ai_providers


def test_packaged_app_never_touches_keychain(tmp_path, monkeypatch):
    backend = Mock()
    backend.get_password.side_effect = AssertionError("Unexpected Keychain access")
    backend.set_password.side_effect = AssertionError("Unexpected Keychain access")
    backend.delete_password.side_effect = AssertionError("Unexpected Keychain access")
    monkeypatch.setitem(sys.modules, "keyring", backend)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("SUBTITLE_FACTORY_USE_KEYCHAIN", "1")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "providers.db")
    database.init_db()

    saved = ai_providers.save_provider(
        "deepseek",
        "https://api.deepseek.com/v1",
        "deepseek-flash",
        "local-key",
        True,
    )
    assert saved["has_api_key"] is True
    assert saved["api_key"] == ""
    assert ai_providers.get_provider("deepseek", include_secret=True)["api_key"] == "local-key"
    assert not backend.mock_calls
