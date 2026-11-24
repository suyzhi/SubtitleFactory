"""AI provider and local App runtime settings APIs."""

import time

import httpx
from fastapi import APIRouter, HTTPException

from ..models.schemas import (
    AIAssignmentsUpdate,
    AIConnectionTest,
    AIProviderUpdate,
    AISettingsUpdate,
    AppSettingsUpdate,
    PathValidationRequest,
)
from ..services.ai_providers import (
    get_provider,
    list_provider_cards,
    prepare_chat_payload,
    raise_for_provider_status,
    record_test,
    save_provider,
)
from ..services.ai_settings import (
    PROVIDER_PRESETS,
    get_ai_settings,
    normalize_base_url,
    record_ai_test,
    save_ai_settings,
)
from ..services.app_settings import (
    effective_app_settings,
    get_app_settings,
    get_effective_app_settings,
    save_app_settings,
)
from ..services.distribution import (
    distribution_capabilities,
    is_external_model_reference,
    require_external_runtime_paths,
)
from ..services.settings_runtime import (
    _redact_local_paths,
    _repair_invalid_settings,
    get_runtime_health,
    read_validated_app_settings,
    validate_app_path,
)

router = APIRouter(prefix="/api")

__all__ = [
    "router",
    "get_runtime_health",
    "read_validated_app_settings",
    "validate_app_path",
    "_redact_local_paths",
    "_repair_invalid_settings",
]


@router.get("/settings/app")
def read_app_settings():
    settings, warnings = read_validated_app_settings()
    return {"settings": _redact_local_paths(settings), "warnings": warnings}


@router.put("/settings/app")
def update_app_settings(req: AppSettingsUpdate):
    updates = req.model_dump(exclude_unset=True)
    path_fields = {
        "custom_model_path",
        "coreml_model_path",
        "coreml_cli_path",
        "download_directory",
        "ffmpeg_path",
        "yt_dlp_path",
    }
    capabilities = distribution_capabilities()
    if not capabilities.external_runtime_paths:
        if any(updates.get(field) for field in path_fields):
            require_external_runtime_paths()
        if is_external_model_reference(updates.get("default_model")):
            require_external_runtime_paths()
        runtimes = updates.get("transcription_runtime_by_model")
        if isinstance(runtimes, dict) and any(
            is_external_model_reference(model_id) or runtime == "external_coreml"
            for model_id, runtime in runtimes.items()
        ):
            require_external_runtime_paths()
        for field in path_fields:
            updates.pop(field, None)
    current = get_effective_app_settings()
    previous = dict(current)
    current.update(updates)
    repaired, warnings = _repair_invalid_settings(current)
    try:
        saved = save_app_settings(
            {field: value for field, value in repaired.items() if previous.get(field) != value}
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "settings": _redact_local_paths(effective_app_settings(saved)),
        "warnings": warnings,
    }


@router.post("/settings/app/validate-path")
def validate_settings_path(req: PathValidationRequest):
    require_external_runtime_paths()
    return _redact_local_paths(validate_app_path(req.kind, req.path))


@router.get("/settings/ai")
def read_ai_settings():
    return {"settings": get_ai_settings(include_secret=False), "presets": PROVIDER_PRESETS}


@router.put("/settings/ai")
def update_ai_settings(req: AISettingsUpdate):
    try:
        return {"settings": save_ai_settings(req.provider, req.base_url, req.model, req.api_key)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/settings/ai/providers")
def read_ai_provider_cards():
    settings = get_app_settings()
    return {
        "providers": list_provider_cards(False),
        "assignments": {
            "clean_provider_id": settings.get("clean_provider_id") or "deepseek",
            "translate_provider_id": settings.get("translate_provider_id") or "deepseek",
            "content_provider_id": settings.get("content_provider_id") or "deepseek",
        },
    }


@router.put("/settings/ai/providers/{provider_id}")
def update_ai_provider_card(provider_id: str, req: AIProviderUpdate):
    try:
        return {
            "provider": save_provider(
                provider_id,
                req.base_url,
                req.model,
                req.api_key,
                req.enabled,
                req.thinking_enabled,
            )
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/settings/ai/assignments")
def update_ai_assignments(req: AIAssignmentsUpdate):
    try:
        get_provider(req.clean_provider_id, False)
        get_provider(req.translate_provider_id, False)
        get_provider(req.content_provider_id, False)
        settings = save_app_settings(req.model_dump())
        return {
            "assignments": {
                "clean_provider_id": settings["clean_provider_id"],
                "translate_provider_id": settings["translate_provider_id"],
                "content_provider_id": settings["content_provider_id"],
            }
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/settings/ai/providers/{provider_id}/models")
def read_remote_models(provider_id: str):
    provider = get_provider(provider_id, True)
    if not provider.get("api_key"):
        raise HTTPException(400, "请先保存 API Key")
    try:
        response = httpx.get(
            f"{provider['base_url'].rstrip('/')}/models",
            headers={"Authorization": f"Bearer {provider['api_key']}"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "models": sorted({str(item.get("id")) for item in payload.get("data", []) if item.get("id")})
        }
    except Exception as exc:
        raise HTTPException(502, f"读取模型列表失败：{str(exc)[:200]}") from exc


@router.post("/settings/ai/providers/{provider_id}/test")
def test_ai_provider_card(provider_id: str):
    provider = get_provider(provider_id, True)
    if not provider.get("api_key"):
        raise HTTPException(400, "请先保存 API Key")
    started = time.monotonic()
    try:
        payload = prepare_chat_payload(
            provider,
            {
                "model": provider["model"],
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "temperature": 0,
                "max_tokens": 8,
            },
        )
        response = httpx.post(
            f"{provider['base_url'].rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {provider['api_key']}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        raise_for_provider_status(response)
        response.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        record_test(provider_id, False)
        raise HTTPException(502, f"连接测试失败：{str(exc)[:200]}") from exc
    latency = round((time.monotonic() - started) * 1000)
    record_test(provider_id, True, latency)
    return {"ok": True, "latency_ms": latency, "provider": get_provider(provider_id, False)}


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
        provider = {"provider": req.provider, "model": req.model}
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=prepare_chat_payload(
                provider,
                {
                    "model": req.model,
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "temperature": 0,
                    "max_tokens": 8,
                },
            ),
            timeout=30,
        )
        raise_for_provider_status(response)
        response.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        record_ai_test(False)
        raise HTTPException(502, f"连接测试失败：{str(exc)[:240]}") from exc
    latency_ms = round((time.monotonic() - started) * 1000)
    record_ai_test(True, latency_ms)
    return {"ok": True, "latency_ms": latency_ms, "settings": get_ai_settings(include_secret=False)}
