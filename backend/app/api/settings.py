"""AI provider configuration API."""

import time

import httpx
from fastapi import APIRouter, HTTPException

from ..models.schemas import AIConnectionTest, AISettingsUpdate
from ..services.ai_settings import (
    PROVIDER_PRESETS,
    get_ai_settings,
    normalize_base_url,
    record_ai_test,
    save_ai_settings,
)

router = APIRouter(prefix="/api")


@router.get("/settings/ai")
def read_ai_settings():
    return {"settings": get_ai_settings(include_secret=False), "presets": PROVIDER_PRESETS}


@router.put("/settings/ai")
def update_ai_settings(req: AISettingsUpdate):
    try:
        return {"settings": save_ai_settings(req.provider, req.base_url, req.model, req.api_key)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/settings/ai/test")
def test_ai_connection(req: AIConnectionTest):
    current = get_ai_settings(include_secret=True)
    api_key = (req.api_key or "").strip()
    if not api_key and current.get("provider") == req.provider:
        api_key = current.get("api_key", "")
    if not api_key:
        raise HTTPException(400, "请先填写 API Key")
    try:
        base_url = normalize_base_url(req.base_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    started = time.monotonic()
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": req.model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "temperature": 0,
                "max_tokens": 8,
            },
            timeout=30,
        )
        response.raise_for_status()
        response.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        record_ai_test(False)
        raise HTTPException(502, f"连接测试失败：{str(exc)[:240]}") from exc
    latency_ms = round((time.monotonic() - started) * 1000)
    record_ai_test(True, latency_ms)
    return {"ok": True, "latency_ms": latency_ms, "settings": get_ai_settings(include_secret=False)}
