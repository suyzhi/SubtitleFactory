"""AI provider configuration and lightweight runtime health APIs."""

import os
import shutil
import time
from importlib import import_module
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from ..models.schemas import AIAssignmentsUpdate, AIProviderUpdate
from ..services.ai_providers import (
    get_provider,
    list_provider_cards,
    prepare_chat_payload,
    raise_for_provider_status,
    record_test,
    save_provider,
)
from ..services.app_settings import get_app_settings, save_app_settings
from ..services.distribution import distribution_capabilities
from ..utils.config import DATA_DIR, DOWNLOADS_DIR, MODELS_DIR

router = APIRouter(prefix="/api")


def _redact_local_paths(value: Any) -> Any:
    """Represent home-directory paths with ``~`` in API/diagnostic payloads."""
    if isinstance(value, dict):
        return {key: _redact_local_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_local_paths(item) for item in value]
    if not isinstance(value, str):
        return value
    home = str(Path.home().resolve())
    if value == home:
        return "~"
    if value.startswith(f"{home}{os.sep}"):
        return f"~{value[len(home):]}"
    return value


def _runtime_item(
    *,
    ok: bool,
    status: str,
    path: str | None,
    source: str,
    message: str,
    **details,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "status": status,
        "path": path,
        "source": source,
        "message": message,
        **details,
    }


def _normalize_runtime_tool(raw: Any, name: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if "ok" in raw and "status" in raw:
        return raw
    available = bool(raw.get("available"))
    path = raw.get("path")
    if not path and isinstance(raw.get("cli"), dict):
        path = raw["cli"].get("path")
    message = raw.get("message") or raw.get("error")
    if not message:
        message = f"{name} 可用" if available else f"未找到可用的 {name}"
    return _runtime_item(
        ok=available,
        status="ready" if available else "missing",
        path=path or None,
        source=raw.get("source") or "unavailable",
        message=message,
        version=raw.get("version") or "",
        architectures=raw.get("architectures") or [],
        cli=raw.get("cli"),
    )


def _fallback_yt_dlp_status() -> dict[str, Any]:
    try:
        yt_dlp = import_module("yt" + "_dlp")
        yt_dlp_path = (
            str(Path(yt_dlp.__file__).resolve()) if yt_dlp.__file__ else "内置模块"
        )
        return _runtime_item(
            ok=True,
            status="ready",
            path=yt_dlp_path,
            source="bundled_python",
            message="yt-dlp 内置模块可用",
            version=getattr(getattr(yt_dlp, "version", None), "__version__", None),
        )
    except Exception:
        return _runtime_item(
            ok=False,
            status="missing",
            path=None,
            source="unavailable",
            message="未找到可用的 yt-dlp",
        )


def _model_runtime_summary() -> dict[str, Any]:
    coreml = None
    coreml_error = None
    try:
        from ..services.parakeet_transcriber import (
            _asset_paths,
            _model_cache_is_valid,
            discover_coreml_runtime,
        )

        if distribution_capabilities().external_runtime_paths:
            coreml = discover_coreml_runtime(None, None)
        assets = _asset_paths(MODELS_DIR)
        onnx_ready = bool(_model_cache_is_valid(assets))
        onnx_path = str(assets.model_dir)
    except RuntimeError as exc:
        coreml_error = str(exc)
        onnx_path = str(Path(MODELS_DIR) / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8")
        onnx_ready = False
    except Exception:
        onnx_path = str(Path(MODELS_DIR) / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8")
        onnx_ready = False

    models = {
        "whisper_small": _runtime_item(
            ok=True,
            status="available",
            path=None,
            source="app_managed",
            message="Whisper Small 可按需在 App 数据目录中准备",
        ),
        "parakeet_onnx": _runtime_item(
            ok=onnx_ready,
            status="ready" if onnx_ready else "download_required",
            path=onnx_path,
            source="app_download" if onnx_ready else "app_managed",
            message="Parakeet ONNX 已就绪" if onnx_ready else "Parakeet ONNX 需下载",
        ),
        "parakeet_coreml": _runtime_item(
            ok=coreml is not None,
            status="ready" if coreml else "not_detected",
            path=str(coreml.model_dir) if coreml else None,
            source="external_detected" if coreml else "unavailable",
            message="已发现 Core ML 模型" if coreml else "未发现可选 Core ML 运行时",
            runtime_error=coreml_error,
        ),
    }
    return _runtime_item(
        ok=True,
        status="ready",
        path=str(MODELS_DIR),
        source="app_data",
        message="本地转写模型目录可用",
        selected="small",
        items=models,
    )


def get_runtime_health() -> dict[str, Any]:
    """Return a failure-tolerant runtime snapshot for ``GET /api/health``."""
    capabilities = distribution_capabilities()
    output_dir = Path(DOWNLOADS_DIR).expanduser().resolve(strict=False)
    disk_probe = output_dir if output_dir.exists() else output_dir.parent
    writable = disk_probe.is_dir() and os.access(disk_probe, os.W_OK)
    try:
        usage = shutil.disk_usage(disk_probe)
        disk = _runtime_item(
            ok=True,
            status="ready",
            path=str(disk_probe),
            source="filesystem",
            message=f"可用空间 {usage.free / (1024 ** 3):.1f} GiB",
            free_bytes=usage.free,
            total_bytes=usage.total,
        )
    except OSError as exc:
        disk = _runtime_item(
            ok=False,
            status="unavailable",
            path=str(disk_probe),
            source="filesystem",
            message="无法读取磁盘空间",
            reason=str(exc),
        )

    download_runtime = None
    try:
        if capabilities.youtube:
            from ..services.runtime_diagnostics import get_download_runtime_status

            download_runtime = get_download_runtime_status(
                user_download_dir=str(output_dir),
            )
        else:
            from ..services.runtime_diagnostics import (
                resolve_ffmpeg_path,
                resolve_ffprobe_path,
            )

            ffmpeg_runtime = resolve_ffmpeg_path(None)
            ffprobe_runtime = resolve_ffprobe_path(None)
            download_runtime = {
                "ffmpeg": ffmpeg_runtime.to_dict() if ffmpeg_runtime else None,
                "ffprobe": ffprobe_runtime.to_dict() if ffprobe_runtime else None,
            }
    except (ImportError, AttributeError, TypeError, OSError, RuntimeError):
        download_runtime = None

    ffmpeg = _normalize_runtime_tool(
        download_runtime.get("ffmpeg") if isinstance(download_runtime, dict) else None,
        "ffmpeg",
    ) or _runtime_item(
        ok=False,
        status="missing",
        path=None,
        source="bundled_runtime" if not capabilities.external_runtime_paths else "unavailable",
        message="App 内置 FFmpeg 缺失"
        if not capabilities.external_runtime_paths
        else "未找到可用的 ffmpeg",
    )
    yt_dlp_status = (
        _normalize_runtime_tool(
            download_runtime.get("yt_dlp")
            if isinstance(download_runtime, dict)
            else None,
            "yt-dlp",
        )
        or _fallback_yt_dlp_status()
        if capabilities.youtube
        else _runtime_item(
            ok=False,
            status="disabled",
            path=None,
            source="distribution_policy",
            message="Mac App Store 版本未包含 yt-dlp",
        )
    )
    ffprobe = _normalize_runtime_tool(
        download_runtime.get("ffprobe") if isinstance(download_runtime, dict) else None,
        "ffprobe",
    )
    deno = (
        _normalize_runtime_tool(
            download_runtime.get("deno")
            if isinstance(download_runtime, dict)
            else None,
            "deno",
        )
        if capabilities.youtube
        else _runtime_item(
            ok=False,
            status="disabled",
            path=None,
            source="distribution_policy",
            message="Mac App Store 版本未包含 Deno",
        )
    )
    ejs = (
        _normalize_runtime_tool(
            download_runtime.get("ejs") if isinstance(download_runtime, dict) else None,
            "EJS",
        )
        if capabilities.youtube
        else _runtime_item(
            ok=False,
            status="disabled",
            path=None,
            source="distribution_policy",
            message="Mac App Store 版本未包含 YouTube 挑战组件",
        )
    )
    return _redact_local_paths(
        {
            "ffmpeg": ffmpeg,
            "ffprobe": ffprobe,
            "yt_dlp": yt_dlp_status,
            "deno": deno,
            "ejs": ejs,
            "output_directory": _runtime_item(
                ok=writable,
                status="ready" if writable else "not_writable",
                path=str(output_dir),
                source="app_data",
                message="输出目录可用" if writable else "输出目录不可写",
            ),
            "disk": disk,
            "models": _model_runtime_summary(),
            "data_directory": str(Path(DATA_DIR).resolve()),
        }
    )


@router.get("/settings/ai/providers")
def read_ai_provider_cards():
    settings = get_app_settings()
    return {
        "providers": list_provider_cards(False),
        "assignments": {
            "clean_provider_id": settings.get("clean_provider_id") or "deepseek",
            "translate_provider_id": settings.get("translate_provider_id")
            or "deepseek",
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
        settings = save_app_settings(req.model_dump())
        return {
            "assignments": {
                "clean_provider_id": settings["clean_provider_id"],
                "translate_provider_id": settings["translate_provider_id"],
            }
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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
    return {
        "ok": True,
        "latency_ms": latency,
        "provider": get_provider(provider_id, False),
    }
