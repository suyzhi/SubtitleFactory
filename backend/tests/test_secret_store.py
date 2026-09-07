"""Password-prompt regression: even packaged builds must stay local."""
import sys
from unittest.mock import Mock

from app.services import secret_store


def test_packaged_app_never_touches_keychain(monkeypatch):
    backend = Mock()
    backend.get_password.side_effect = AssertionError("Unexpected Keychain access")
    backend.set_password.side_effect = AssertionError("Unexpected Keychain access")
    backend.delete_password.side_effect = AssertionError("Unexpected Keychain access")
    monkeypatch.setitem(sys.modules, "keyring", backend)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("SUBTITLE_FACTORY_USE_KEYCHAIN", "1")
    assert secret_store.keychain_enabled() is False
    assert secret_store.get_secret("provider:test", "local-key") == "local-key"
    assert secret_store.get_secret("provider:keychain-only") == ""
    secret_store.save_secret("provider:test", "updated-key")
    secret_store.save_secret("provider:test", "")
    assert not backend.mock_calls
