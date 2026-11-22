"""
字幕工厂 - 转写模型目录校验、状态查询与自动回退解析模块
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .cloud_asr import FUN_ASR_MODEL_ID, fun_asr_status
from .managed_sherpa import managed_model_status, recommended_ready_model
from .model_catalog import (
    QWEN_ASR_CATALOG_BY_ID,
    WHISPER_CATALOG_BY_ID,
    resolve_local_model,
    runtime_model_status,
)
from .parakeet_transcriber import (
    PARAKEET_MODEL_ID,
    PARAKEET_ONNX_MODEL_ID,
    PARAKEET_SUPPORTED_LANGUAGES,
)
from .parakeet_transcriber import (
    get_parakeet_model_status as _default_get_parakeet_model_status,
)
from .sherpa_catalog import MANAGED_SHERPA_BY_ID

WHISPER_MODEL_IDS = frozenset(WHISPER_CATALOG_BY_ID)
QWEN_ASR_MODEL_IDS = frozenset(QWEN_ASR_CATALOG_BY_ID)
PARAKEET_MODEL_IDS = frozenset({PARAKEET_MODEL_ID, PARAKEET_ONNX_MODEL_ID})
MANAGED_SHERPA_MODEL_IDS = frozenset(MANAGED_SHERPA_BY_ID)
SUPPORTED_TRANSCRIPTION_MODELS = (
    WHISPER_MODEL_IDS
    | QWEN_ASR_MODEL_IDS
    | PARAKEET_MODEL_IDS
    | MANAGED_SHERPA_MODEL_IDS
    | {FUN_ASR_MODEL_ID, "custom"}
)
SAFE_TRANSCRIPTION_MODEL = "small"
_WHISPER_REQUIRED_FILES = frozenset({"model.bin", "config.json", "tokenizer.json"})


def _get_parakeet_status(*args, **kwargs) -> dict:
    # Support test patches targeting ``app.services.transcriber.get_parakeet_model_status``.
    transcriber_mod = sys.modules.get("app.services.transcriber")
    fn = (
        getattr(transcriber_mod, "get_parakeet_model_status", _default_get_parakeet_model_status)
        if transcriber_mod is not None
        else _default_get_parakeet_model_status
    )
    return fn(*args, **kwargs)


@dataclass(frozen=True)
class ModelResolution:
    requested_model: str
    model_id: str
    load_target: str
    source: str
    fallback_reason: str = ""

    @property
    def fell_back(self) -> bool:
        return bool(self.fallback_reason)

    def to_details(self) -> dict:
        # load_target may be a user-selected local path; never persist it.
        value = asdict(self)
        value.pop("load_target", None)
        value["fell_back"] = self.fell_back
        return value


def validate_whisper_model_path(path: str | Path | None) -> dict:
    """Validate a local faster-whisper CTranslate2 model directory."""
    if not path:
        return {"ok": False, "source": "unavailable", "error": "尚未选择模型目录"}
    candidate = Path(path).expanduser()
    missing = sorted(name for name in _WHISPER_REQUIRED_FILES if not (candidate / name).is_file())
    if not candidate.is_dir() or missing:
        return {
            "ok": False,
            "source": "unavailable",
            "error": "模型目录缺少必要文件" if missing else "模型目录不存在",
            "missing_files": missing,
        }
    return {"ok": True, "source": "custom_path", "error": "", "missing_files": []}


def _bundled_whisper_model(model_id: str) -> Path | None:
    roots: list[Path] = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        roots.append(Path(frozen_root))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    for root in roots:
        for candidate in (
            root / "models" / "whisper" / model_id,
            root / "models" / f"faster-whisper-{model_id}",
        ):
            if validate_whisper_model_path(candidate)["ok"]:
                return candidate.resolve()
    return None


def _managed_whisper_model_ready(model_id: str) -> bool:
    return bool(resolve_local_model(model_id, "cpu") or resolve_local_model(model_id, "mlx"))


def get_transcription_model_status(
    model_id: str,
    *,
    custom_model_path: str | Path | None = None,
    coreml_model_path: str | Path | None = None,
    coreml_cli_path: str | Path | None = None,
) -> dict:
    """Return model state with explicit source categories for the settings UI."""
    if model_id == FUN_ASR_MODEL_ID:
        return fun_asr_status()
    if model_id in MANAGED_SHERPA_MODEL_IDS:
        return managed_model_status(model_id)
    if model_id in QWEN_ASR_MODEL_IDS:
        status = runtime_model_status(model_id, "mlx")
        return {
            "model_id": model_id,
            "ready": bool(status["model_ready"]),
            "source": status["source"],
            "state": status["status"],
            "download_required": status["download_required"],
            "download_bytes": status["download_bytes"],
            "error": "" if status["model_ready"] else "首次使用时将下载到 App 模型目录",
        }
    if model_id in PARAKEET_MODEL_IDS:
        return _get_parakeet_status(
            model_id,
            coreml_model_dir=coreml_model_path,
            coreml_cli_path=coreml_cli_path,
        )
    if model_id == "custom":
        validation = validate_whisper_model_path(custom_model_path)
        return {
            "model_id": model_id,
            "ready": validation["ok"],
            "source": "custom_path" if validation["ok"] else "unavailable",
            "state": "ready" if validation["ok"] else "invalid",
            "download_required": False,
            "error": validation["error"],
        }
    if model_id not in WHISPER_MODEL_IDS:
        return {
            "model_id": model_id,
            "ready": False,
            "source": "unavailable",
            "state": "unavailable",
            "download_required": False,
            "error": "不支持的转写模型",
        }
    bundled = _bundled_whisper_model(model_id)
    cpu_status = runtime_model_status(model_id, "cpu")
    mlx_status = runtime_model_status(model_id, "mlx")
    managed_ready = bool(cpu_status["model_ready"] or mlx_status["model_ready"])
    return {
        "model_id": model_id,
        "ready": bool(bundled or managed_ready),
        "source": "built_in" if bundled else ("app_download" if managed_ready else "huggingface"),
        "state": "ready" if bundled or managed_ready else "not_downloaded",
        "download_required": not bool(bundled or managed_ready),
        "download_bytes": min(cpu_status["download_bytes"], mlx_status["download_bytes"]),
        "error": "" if bundled or managed_ready else "首次使用时将下载到 App 模型目录",
    }


def resolve_transcription_model(
    model_size: str | None,
    language: str = "auto",
    *,
    default_model: str | None = None,
    custom_model_path: str | Path | None = None,
    coreml_model_path: str | Path | None = None,
    coreml_cli_path: str | Path | None = None,
) -> ModelResolution:
    """Resolve to a safe runtime model and explain every automatic fallback."""
    requested = (model_size or "auto").strip()
    candidate = requested
    if candidate == "auto":
        configured = (default_model or "auto").strip()
        candidate = (
            configured
            if configured and configured != "auto"
            else recommended_ready_model(language) or SAFE_TRANSCRIPTION_MODEL
        )

    normalized_language = (language or "auto").lower()
    if candidate.startswith("local:"):
        try:
            from .local_models import validate_imported

            imported = validate_imported(candidate)
            if imported["ready"]:
                return ModelResolution(requested, candidate, imported["path"], "imported_reference")
        except Exception:
            pass
        return ModelResolution(
            requested, candidate, candidate, "unavailable", "导入模型路径失效，需要重新定位"
        )
    if (
        candidate in PARAKEET_MODEL_IDS
        and normalized_language != "auto"
        and normalized_language not in PARAKEET_SUPPORTED_LANGUAGES
    ):
        return ModelResolution(
            requested,
            SAFE_TRANSCRIPTION_MODEL,
            SAFE_TRANSCRIPTION_MODEL,
            "app_download",
            "Parakeet 不支持所选源语言，已切换到 Whisper Small",
        )
    if candidate == "distil-large-v3" and normalized_language not in {"auto", "", "en", "english"}:
        return ModelResolution(
            requested,
            SAFE_TRANSCRIPTION_MODEL,
            SAFE_TRANSCRIPTION_MODEL,
            "app_download",
            "Distil-Whisper Large V3 仅支持英语，已切换到 Whisper Small",
        )

    if candidate in MANAGED_SHERPA_MODEL_IDS:
        definition = MANAGED_SHERPA_BY_ID[candidate]
        if (
            normalized_language not in {"", "auto"}
            and "*" not in definition.languages
            and normalized_language not in definition.languages
        ):
            return ModelResolution(
                requested,
                SAFE_TRANSCRIPTION_MODEL,
                SAFE_TRANSCRIPTION_MODEL,
                "app_download",
                f"{definition.name} 不支持所选源语言，已切换到 Whisper Small",
            )
        return ModelResolution(requested, candidate, candidate, "app_download")

    if candidate in QWEN_ASR_MODEL_IDS:
        return ModelResolution(requested, candidate, candidate, "huggingface")

    if candidate == FUN_ASR_MODEL_ID:
        return ModelResolution(requested, candidate, candidate, "dashscope_cloud")

    if candidate == PARAKEET_MODEL_ID:
        status = _get_parakeet_status(
            candidate,
            coreml_model_dir=coreml_model_path,
            coreml_cli_path=coreml_cli_path,
        )
        if not status["ready"]:
            return ModelResolution(
                requested,
                SAFE_TRANSCRIPTION_MODEL,
                SAFE_TRANSCRIPTION_MODEL,
                "app_download",
                "外部 Core ML 模型或 CLI 无效，已切换到 Whisper Small",
            )
        return ModelResolution(requested, candidate, candidate, status["source"])

    if candidate == PARAKEET_ONNX_MODEL_ID:
        return ModelResolution(requested, candidate, candidate, "app_download")

    if candidate == "custom" or Path(candidate).expanduser().is_absolute():
        selected_path = custom_model_path if candidate == "custom" else candidate
        validation = validate_whisper_model_path(selected_path)
        if validation["ok"]:
            return ModelResolution(
                requested,
                "custom",
                str(Path(selected_path).expanduser().resolve()),
                "custom_path",
            )
        return ModelResolution(
            requested,
            SAFE_TRANSCRIPTION_MODEL,
            SAFE_TRANSCRIPTION_MODEL,
            "app_download",
            "自定义模型目录无效，已切换到 Whisper Small",
        )

    if candidate in WHISPER_MODEL_IDS:
        bundled = _bundled_whisper_model(candidate)
        return ModelResolution(
            requested,
            candidate,
            str(bundled) if bundled else candidate,
            "built_in" if bundled else "app_download",
        )

    return ModelResolution(
        requested,
        SAFE_TRANSCRIPTION_MODEL,
        SAFE_TRANSCRIPTION_MODEL,
        "app_download",
        "所选模型不可用，已切换到 Whisper Small",
    )
