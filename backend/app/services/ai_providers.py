"""Independent local configurations for AI providers."""

import time
from urllib.parse import urlparse

from ..models.database import get_db
from .ai_settings import PROVIDER_PRESETS


PRESETS = {item["id"]: item for item in PROVIDER_PRESETS}


def _validate_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Base URL 必须是有效的 http/https 地址")
    return value


def ensure_provider_cards() -> None:
    db = get_db()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for provider_id, preset in PRESETS.items():
        db.execute(
            """INSERT OR IGNORE INTO ai_provider_configs
               (provider_id,base_url,model,updated_at) VALUES (?,?,?,?)""",
            (provider_id, preset["base_url"], preset["model"] or "custom-model", now),
        )
    db.commit(); db.close()


def list_provider_cards(include_secret: bool = False) -> list[dict]:
    ensure_provider_cards()
    db = get_db(); rows = db.execute("SELECT * FROM ai_provider_configs ORDER BY rowid").fetchall(); db.close()
    result = []
    for row in rows:
        item = dict(row); preset = PRESETS.get(item["provider_id"], {})
        item.update({"name": preset.get("name", item["provider_id"]), "models": preset.get("models", [])})
        item["enabled"] = bool(item["enabled"]); item["has_api_key"] = bool(item["api_key"])
        if not include_secret: item["api_key"] = ""
        result.append(item)
    return result


def get_provider(provider_id: str, include_secret: bool = True) -> dict:
    ensure_provider_cards(); db = get_db()
    row = db.execute("SELECT * FROM ai_provider_configs WHERE provider_id=?", (provider_id,)).fetchone(); db.close()
    if not row: raise ValueError("AI 供应商不存在")
    result = dict(row)
    if not include_secret:
        result["has_api_key"] = bool(result["api_key"]); result["api_key"] = ""
    return result


def save_provider(provider_id: str, base_url: str, model: str, api_key: str | None, enabled: bool = True) -> dict:
    current = get_provider(provider_id, True)
    secret = current["api_key"] if not api_key else api_key.strip()
    model = (model or "").strip()
    if not model: raise ValueError("模型名称不能为空")
    db = get_db(); db.execute(
        """UPDATE ai_provider_configs SET base_url=?,api_key=?,model=?,enabled=?,updated_at=?,
           last_test_status='',last_test_at='',last_latency_ms=0 WHERE provider_id=?""",
        (_validate_url(base_url), secret, model, int(enabled), time.strftime("%Y-%m-%d %H:%M:%S"), provider_id),
    ); db.commit(); db.close()
    return get_provider(provider_id, False)


def assigned_provider(operation: str, override: str | None = None, model_override: str | None = None) -> dict:
    from .app_settings import get_app_settings
    settings = get_app_settings()
    provider_id = override or settings.get(f"{operation}_provider_id") or "deepseek"
    provider = get_provider(provider_id, True)
    if model_override: provider["model"] = model_override.strip()
    if not provider.get("api_key"): raise ValueError(f"{provider_id} API Key 未配置")
    provider["provider"] = provider_id
    return provider


def record_test(provider_id: str, ok: bool, latency_ms: int = 0) -> None:
    db = get_db(); db.execute(
        "UPDATE ai_provider_configs SET last_test_status=?,last_test_at=?,last_latency_ms=? WHERE provider_id=?",
        ("success" if ok else "failed", time.strftime("%Y-%m-%d %H:%M:%S"), latency_ms, provider_id),
    ); db.commit(); db.close()
