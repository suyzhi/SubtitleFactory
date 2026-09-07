"""Local provider secret compatibility helpers; never access the system Keychain.

Secrets remain in the application's local database. Existing Keychain-only
credentials must be entered again; reading them would prompt for a password.
"""


def keychain_enabled() -> bool:
    return False


def get_secret(account: str, legacy: str = "") -> str:
    return legacy


def save_secret(account: str, value: str) -> None:
    # The caller persists the value in the local database transaction.
    return None
