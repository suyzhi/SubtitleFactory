"""AI provider and local App runtime settings APIs."""

import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from ..models.schemas import (
    AIConnectionTest, AISettingsUpdate, AIProviderUpdate, AIAssignmentsUpdate,
    AppSettingsUpdate, PathValidationRequest,
)
from ..services.app_settings import get_app_settings, save_app_settings
from ..services.local_models import get_imported
from ..services.ai_settings import (
    PROVIDER_PRESETS,
    get_ai_settings,
    normalize_base_url,
    record_ai_test,
    save_ai_settings,
)
from ..utils.config import DATA_DIR, DOWNLOADS_DIR, MODELS_DIR
from ..services.ai_providers import (
    get_provider, list_provider_cards, record_test, save_provider,
)

router = APIRouter(prefix="/api")

_PARAKEET_COREML_ID = "parakeet-tdt-0.6b-v3-coreml"
_PARAKEET_ONNX_ID = "parakeet-tdt-0.6b-v3-int8"
_MODEL_IDS = {
    "tiny", "base", "small", "medium", "large-v3",
    _PARAKEET_COREML_ID, _PARAKEET_ONNX_ID,
}


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


def _resolved_candidate(value: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(value))
    if not os.path.isabs(expanded) and os.sep not in expanded:
        discovered = shutil.which(expanded)
        if discovered:
            expanded = discovered
    return Path(expanded).resolve(strict=False)


def _executable_architecture(path: Path) -> tuple[bool, dict[str, Any]]:
    """Best-effort native executable architecture check without executing it."""
    details: dict[str, Any] = {"host_architecture": platform.machine() or "unknown"}
    if platform.system() != "Darwin" or not Path("/usr/bin/file").is_file():
        return True, details
    try:
        result = subprocess.run(
            ["/usr/bin/file", "-b", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return True, details
    description = result.stdout.strip()
    details["format"] = description[:160]
    if "Mach-O" not in description:
        # Scripts and other launcher formats are architecture-independent.
        return True, details
    host_arch = platform.machine().lower()
    compatible = not host_arch or host_arch in description.lower()
    details["architecture_compatible"] = compatible
    return compatible, details


def _validate_executable(kind: str, value: str) -> dict[str, Any]:
    path = _resolved_candidate(value)
    if not path.is_file():
        return _path_result(kind, value, path, False, "not_found", "文件不存在")
    if not os.access(path, os.X_OK):
        return _path_result(kind, value, path, False, "not_executable", "文件没有可执行权限")
    compatible, details = _executable_architecture(path)
    if not compatible:
        return _path_result(
            kind, value, path, False, "architecture_mismatch",
            "可执行文件与当前 Mac 架构不兼容", details,
        )
    return _path_result(kind, value, path, True, "ready", "可执行文件可用", details)


def _validate_model_path(kind: str, value: str) -> dict[str, Any]:
    path = _resolved_candidate(value)
    if not path.exists():
        return _path_result(kind, value, path, False, "not_found", "模型路径不存在")
    if kind == "coreml_model":
        if not path.is_dir():
            return _path_result(kind, value, path, False, "not_directory", "Core ML 模型必须是目录")
        try:
            from ..services.parakeet_transcriber import _valid_coreml_model_dir
            valid = bool(_valid_coreml_model_dir(path))
        except Exception:
            required = {
                "Encoder.mlmodelc", "Decoder.mlmodelc", "Preprocessor.mlmodelc",
                "JointDecision.mlmodelc", "config.json",
            }
            valid = all((path / name).exists() for name in required)
        if not valid:
            return _path_result(
                kind, value, path, False, "missing_model_files",
                "Core ML 目录缺少必要模型文件",
            )
        return _path_result(
            kind, value, path, True, "ready", "已验证 Core ML 模型目录",
            {"source": "custom"},
        )

    if path.is_file():
        recognized = path.suffix.lower() in {".bin", ".onnx", ".mlmodel", ".mlpackage"}
    else:
        markers = (
            "model.bin", "model.onnx", "encoder.int8.onnx", "config.json",
            "Encoder.mlmodelc",
        )
        recognized = any((path / marker).exists() for marker in markers)
    if not recognized:
        return _path_result(
            kind, value, path, False, "missing_model_files",
            "未在路径中找到可识别的模型文件",
        )
    return _path_result(
        kind, value, path, True, "ready", "模型路径可用", {"source": "custom"},
    )


def _validate_output_directory(kind: str, value: str) -> dict[str, Any]:
    path = _resolved_candidate(value)
    probe = path if path.exists() else path.parent
    if path.exists() and not path.is_dir():
        return _path_result(kind, value, path, False, "not_directory", "输出路径不是目录")
    if not probe.exists() or not probe.is_dir():
        return _path_result(kind, value, path, False, "parent_missing", "输出目录的父目录不存在")
    if not os.access(probe, os.W_OK):
        return _path_result(kind, value, path, False, "not_writable", "输出目录不可写")
    usage = shutil.disk_usage(probe)
    return _path_result(
        kind, value, path, True, "ready" if path.exists() else "creatable",
        "输出目录可用",
        {"free_bytes": usage.free, "total_bytes": usage.total},
    )


def _path_result(
    kind: str,
    original: str,
    resolved: Path,
    ok: bool,
    status: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "kind": kind,
        "path": original,
        "resolved_path": str(resolved),
        "status": status,
        "reason": reason,
        "details": details or {},
    }


def validate_app_path(kind: str, value: str) -> dict[str, Any]:
    if not value:
        return {
            "ok": False, "kind": kind, "path": value, "resolved_path": None,
            "status": "empty", "reason": "请选择路径", "details": {},
        }
    if kind in {"ffmpeg", "yt_dlp", "cli"}:
        return _validate_executable(kind, value)
    if kind in {"model", "coreml_model"}:
        return _validate_model_path(kind, value)
    return _validate_output_directory(kind, value)


def _repair_invalid_settings(settings: dict[str, Any]) -> tuple[dict[str, Any], list[dict]]:
    repaired = dict(settings)
    warnings: list[dict] = []
    if repaired.get("default_model") == "auto":
        repaired["default_model"] = "small"
    elif repaired.get("default_model") not in _MODEL_IDS and not _is_registered_model(repaired.get("default_model")):
        warnings.append({
            "field": "default_model", "code": "UNSUPPORTED_MODEL",
            "message": "默认模型不可用，已回退到 Whisper Small",
            "fallback": "small",
        })
        repaired["default_model"] = "small"

    path_kinds = {
        "custom_model_path": "model",
        "coreml_model_path": "coreml_model",
        "coreml_cli_path": "cli",
        "ffmpeg_path": "ffmpeg",
        "yt_dlp_path": "yt_dlp",
        "download_directory": "download_directory",
    }
    invalid_model_runtime = False
    for field, kind in path_kinds.items():
        value = repaired.get(field)
        if not value:
            continue
        result = validate_app_path(kind, value)
        if result["ok"]:
            repaired[field] = result["resolved_path"]
            continue
        repaired[field] = None
        invalid_model_runtime = invalid_model_runtime or field in {
            "custom_model_path", "coreml_model_path", "coreml_cli_path",
        }
        warnings.append({
            "field": field,
            "code": "INVALID_PATH",
            "message": f"{result['reason']}；已回退到安全默认设置",
            "fallback": "small" if field in {
                "custom_model_path", "coreml_model_path", "coreml_cli_path",
            } else "automatic",
        })
    if invalid_model_runtime and repaired.get("default_model") in {
        _PARAKEET_COREML_ID, _PARAKEET_ONNX_ID,
    }:
        repaired["default_model"] = "small"
    return repaired, warnings


def _is_registered_model(model_id: Any) -> bool:
    if not isinstance(model_id, str) or not model_id.startswith("local:"):
        return False
    try:
        get_imported(model_id)
    except ValueError:
        return False
    return True


def read_validated_app_settings(*, persist_repairs: bool = True) -> tuple[dict, list[dict]]:
    settings = get_app_settings()
    repaired, warnings = _repair_invalid_settings(settings)
    if persist_repairs and repaired != settings:
        save_app_settings(repaired)
    return repaired, warnings


def _runtime_item(
    *, ok: bool, status: str, path: str | None, source: str, message: str,
    **details,
) -> dict[str, Any]:
    return {
        "ok": ok, "status": status, "path": path, "source": source,
        "message": message, **details,
    }


def _fallback_executable_status(name: str, configured: str | None = None) -> dict[str, Any]:
    candidate = configured or shutil.which(name)
    if candidate:
        result = validate_app_path(name if name in {"ffmpeg", "yt_dlp"} else "cli", candidate)
        if result["ok"]:
            return _runtime_item(
                ok=True, status="ready", path=result["resolved_path"],
                source="custom" if configured else "system_path",
                message=f"{name} 可用",
            )
    if name == "yt_dlp":
        try:
            import yt_dlp
            return _runtime_item(
                ok=True, status="ready", path=str(Path(yt_dlp.__file__).resolve()),
                source="bundled_python", message="yt-dlp 内置模块可用",
                version=getattr(getattr(yt_dlp, "version", None), "__version__", None),
            )
        except Exception:
            pass
    return _runtime_item(
        ok=False, status="missing", path=None, source="unavailable",
        message=f"未找到可用的 {name}",
    )


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


def _fallback_model_status(settings: dict[str, Any]) -> dict[str, Any]:
    coreml = None
    coreml_error = None
    try:
        from ..services.parakeet_transcriber import (
            _asset_paths, _model_cache_is_valid, discover_coreml_runtime,
        )
        coreml = discover_coreml_runtime(
            settings.get("coreml_model_path"), settings.get("coreml_cli_path")
        )
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
            ok=True, status="available", path=None, source="app_managed",
            message="Whisper Small 可按需在 App 数据目录中准备",
        ),
        "parakeet_onnx": _runtime_item(
            ok=onnx_ready, status="ready" if onnx_ready else "download_required",
            path=onnx_path, source="app_download" if onnx_ready else "app_managed",
            message="Parakeet ONNX 已就绪" if onnx_ready else "Parakeet ONNX 需下载",
        ),
        "parakeet_coreml": _runtime_item(
            ok=coreml is not None, status="ready" if coreml else "not_detected",
            path=str(coreml.model_dir) if coreml else None,
            source="external_detected" if coreml else "unavailable",
            message="已发现外部 Core ML 模型" if coreml else "未发现可选 Core ML 运行时",
            runtime_error=coreml_error,
        ),
    }
    selected = settings.get("default_model") or "small"
    return _runtime_item(
        ok=True, status="ready", path=str(MODELS_DIR), source="app_data",
        message=f"默认转写模型: {selected}", selected=selected, items=models,
    )


def get_runtime_health() -> dict[str, Any]:
    """Return a failure-tolerant preflight snapshot for Settings and health API."""
    settings, warnings = read_validated_app_settings()
    configured_download_dir = settings.get("download_directory")
    output_dir = Path(configured_download_dir or DOWNLOADS_DIR).expanduser().resolve(strict=False)
    output_validation = validate_app_path("download_directory", str(output_dir))
    disk_probe = output_dir if output_dir.exists() else output_dir.parent
    try:
        usage = shutil.disk_usage(disk_probe)
        disk = _runtime_item(
            ok=True, status="ready", path=str(disk_probe), source="filesystem",
            message=f"可用空间 {usage.free / (1024 ** 3):.1f} GiB",
            free_bytes=usage.free, total_bytes=usage.total,
        )
    except OSError as exc:
        disk = _runtime_item(
            ok=False, status="unavailable", path=str(disk_probe), source="filesystem",
            message="无法读取磁盘空间", reason=str(exc),
        )

    # Prefer the richer release-runtime resolver when it is available. Keep a
    # fallback so older sidecars and isolated tests retain a stable health API.
    download_runtime = None
    try:
        from ..services.runtime_diagnostics import get_download_runtime_status
        download_runtime = get_download_runtime_status(
            user_ffmpeg_path=settings.get("ffmpeg_path"),
            user_download_dir=str(output_dir),
        )
    except (ImportError, AttributeError, TypeError, OSError, RuntimeError):
        download_runtime = None
    ffmpeg = _normalize_runtime_tool(
        download_runtime.get("ffmpeg") if isinstance(download_runtime, dict) else None,
        "ffmpeg",
    ) or _fallback_executable_status("ffmpeg", settings.get("ffmpeg_path"))
    yt_dlp_status = _normalize_runtime_tool(
        download_runtime.get("yt_dlp") if isinstance(download_runtime, dict) else None,
        "yt-dlp",
    ) or _fallback_executable_status("yt_dlp", settings.get("yt_dlp_path"))
    models = _fallback_model_status(settings)
    return _redact_local_paths({
        "ffmpeg": ffmpeg,
        "yt_dlp": yt_dlp_status,
        "output_directory": _runtime_item(
            ok=output_validation["ok"], status=output_validation["status"],
            path=output_validation["resolved_path"],
            source="custom" if configured_download_dir else "app_data",
            message=output_validation["reason"],
        ),
        "disk": disk,
        "models": models,
        "settings_warnings": warnings,
        "data_directory": str(Path(DATA_DIR).resolve()),
    })


@router.get("/settings/app")
def read_app_settings():
    settings, warnings = read_validated_app_settings()
    return {"settings": _redact_local_paths(settings), "warnings": warnings}


@router.put("/settings/app")
def update_app_settings(req: AppSettingsUpdate):
    updates = req.model_dump(exclude_unset=True)
    current = get_app_settings()
    current.update(updates)
    repaired, warnings = _repair_invalid_settings(current)
    try:
        saved = save_app_settings(repaired)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"settings": _redact_local_paths(saved), "warnings": warnings}


@router.post("/settings/app/validate-path")
def validate_settings_path(req: PathValidationRequest):
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
    return {"providers": list_provider_cards(False), "assignments": {
        "clean_provider_id": settings.get("clean_provider_id") or "deepseek",
        "translate_provider_id": settings.get("translate_provider_id") or "deepseek",
    }}


@router.put("/settings/ai/providers/{provider_id}")
def update_ai_provider_card(provider_id: str, req: AIProviderUpdate):
    try:
        return {"provider": save_provider(provider_id, req.base_url, req.model, req.api_key, req.enabled)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/settings/ai/assignments")
def update_ai_assignments(req: AIAssignmentsUpdate):
    try:
        get_provider(req.clean_provider_id, False); get_provider(req.translate_provider_id, False)
        settings = save_app_settings(req.model_dump())
        return {"assignments": {"clean_provider_id": settings["clean_provider_id"], "translate_provider_id": settings["translate_provider_id"]}}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/settings/ai/providers/{provider_id}/models")
def read_remote_models(provider_id: str):
    provider = get_provider(provider_id, True)
    if not provider.get("api_key"): raise HTTPException(400, "请先保存 API Key")
    try:
        response = httpx.get(f"{provider['base_url'].rstrip('/')}/models", headers={"Authorization": f"Bearer {provider['api_key']}"}, timeout=20)
        response.raise_for_status(); payload = response.json()
        return {"models": sorted({str(item.get("id")) for item in payload.get("data", []) if item.get("id")})}
    except Exception as exc:
        raise HTTPException(502, f"读取模型列表失败：{str(exc)[:200]}") from exc


@router.post("/settings/ai/providers/{provider_id}/test")
def test_ai_provider_card(provider_id: str):
    provider = get_provider(provider_id, True)
    if not provider.get("api_key"): raise HTTPException(400, "请先保存 API Key")
    started = time.monotonic()
    try:
        response = httpx.post(f"{provider['base_url'].rstrip('/')}/chat/completions", headers={"Authorization": f"Bearer {provider['api_key']}", "Content-Type": "application/json"}, json={"model": provider["model"], "messages": [{"role": "user", "content": "Reply with OK."}], "temperature": 0, "max_tokens": 8}, timeout=30)
        response.raise_for_status(); response.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        record_test(provider_id, False); raise HTTPException(502, f"连接测试失败：{str(exc)[:200]}") from exc
    latency = round((time.monotonic()-started)*1000); record_test(provider_id, True, latency)
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
