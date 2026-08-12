VERSION = "0.4.1"
PRODUCT_HTTP_NAME = "SubtitleFactory"


def product_user_agent(purpose: str) -> str:
    """Return a release-identifiable User-Agent from the canonical version."""
    normalized = purpose.strip()
    return f"{PRODUCT_HTTP_NAME}/{VERSION}{f' {normalized}' if normalized else ''}"
