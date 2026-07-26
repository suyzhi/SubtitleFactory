"""Local AI provider settings shared by cleaner and translator."""

import time
from urllib.parse import urlparse

from ..models.database import get_db
from ..utils.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from .secret_store import get_secret, keychain_enabled, save_secret


PROVIDER_PRESETS = [
    {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "models": ["deepseek-chat", "deepseek-reasoner"]},
    {"id": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini", "models": ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini"]},
    {"id": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4.1-mini", "models": ["openai/gpt-4.1-mini", "anthropic/claude-sonnet-4", "google/gemini-2.5-flash"]},
    {"id": "siliconflow", "name": "SiliconFlow", "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3", "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen3-32B"]},
    {"id": "moonshot", "name": "Moonshot", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "models": ["moonshot-v1-8k", "moonshot-v1-32k"]},
    {"id": "dashscope", "name": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "models": ["qwen-plus", "qwen-max", "qwen-turbo"]},
    {"id": "gemini", "name": "Gemini (OpenAI 兼容)", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-flash", "models": ["gemini-2.5-flash", "gemini-2.5-pro"]},
    {"id": "custom", "name": "自定义 OpenAI 兼容服务", "base_url": "http://127.0.0.1:11434/v1", "model": "", "models": []},
]


def normalize_base_url(base_url: str) -> str:
    value = (base_url or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Base URL 必须是有效的 http/https 地址")
    return value


def get_ai_settings(include_secret: bool = True) -> dict:
    db = get_db()
    row = db.execute("SELECT * FROM ai_settings WHERE id = 1").fetchone()
    if not row:
        db.execute(
            "INSERT INTO ai_settings (id, provider, base_url, api_key, model, updated_at) VALUES (1, ?, ?, ?, ?, ?)",
            ("deepseek", LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        db.commit()
        row = db.execute("SELECT * FROM ai_settings WHERE id = 1").fetchone()
    result = dict(row)
    db.close()
    result["api_key"] = get_secret(f"legacy:{result['provider']}", result.get("api_key", ""))
    if not include_secret:
        result["has_api_key"] = bool(result.get("api_key"))
        result["api_key"] = ""
    return result


def save_ai_settings(provider: str, base_url: str, model: str, api_key: str | None = None) -> dict:
    base_url = normalize_base_url(base_url)
    model = (model or "").strip()
    if not model:
        raise ValueError("模型名称不能为空")
    current = get_ai_settings(include_secret=True)
    if api_key is None or api_key == "":
        secret = current.get("api_key", "") if current.get("provider") == provider else ""
    else:
        secret = api_key.strip()
    db = get_db()
    changed = any(current.get(key) != value for key, value in {
        "provider": provider, "base_url": base_url, "model": model,
    }.items())
    save_secret(f"legacy:{provider}", secret)
    stored_secret = "" if keychain_enabled() else secret
    db.execute(
        """INSERT INTO ai_settings (id, provider, base_url, api_key, model, updated_at,has_api_key,keychain_ref)
           VALUES (1, ?, ?, ?, ?, ?,?,?)
           ON CONFLICT(id) DO UPDATE SET provider=excluded.provider, base_url=excluded.base_url,
             api_key=excluded.api_key, model=excluded.model, updated_at=excluded.updated_at,
             has_api_key=excluded.has_api_key,keychain_ref=excluded.keychain_ref""",
        ((provider or "custom").strip(), base_url, stored_secret, model, time.strftime("%Y-%m-%d %H:%M:%S"), int(bool(secret)), f"legacy:{provider}" if keychain_enabled() else None),
    )
    if changed:
        db.execute("UPDATE ai_settings SET last_test_status = '', last_test_at = '', last_latency_ms = 0 WHERE id = 1")
    db.commit()
    db.close()
    return get_ai_settings(include_secret=False)


def record_ai_test(ok: bool, latency_ms: int = 0):
    db = get_db()
    db.execute(
        "UPDATE ai_settings SET last_test_status = ?, last_test_at = ?, last_latency_ms = ? WHERE id = 1",
        ("success" if ok else "failed", time.strftime("%Y-%m-%d %H:%M:%S"), int(latency_ms)),
    )
    db.commit()
    db.close()
