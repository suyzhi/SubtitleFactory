"""
字幕工厂 - 转写模型目录、导入、校验与准备路由
"""

from typing import Optional

from fastapi import APIRouter, HTTPException

from ...models.schemas import (
    ModelImportRequest,
    ModelPrepareRequest,
    ModelScanRequest,
)
from ...services.cloud_asr import FUN_ASR_MODEL_ID
from ...services.distribution import (
    distribution_capabilities,
    is_external_model_reference,
    require_external_runtime_paths,
)
from ...services.local_models import (
    get_imported,
    register_model,
    remove_imported,
    scan_models,
    validate_imported,
)
from ...services.managed_sherpa import (
    managed_model_status,
    prepare_managed_model,
    remove_managed_model,
)
from ...services.model_catalog import (
    MODEL_CATEGORY_ORDER,
    QWEN_ASR_CATALOG_BY_ID,
    QWEN_ASR_MODEL_CATALOG,
    WHISPER_CATALOG_BY_ID,
    WHISPER_MODEL_CATALOG,
    prepare_catalog_model,
    prepare_whisper_model,
    remove_catalog_model,
    runtime_model_status,
)
from ...services.parakeet_transcriber import (
    PARAKEET_MODEL_ID,
    PARAKEET_ONNX_MODEL_ID,
    PARAKEET_SUPPORTED_LANGUAGES,
    prepare_parakeet_model,
)
from ...services.sherpa_catalog import (
    MANAGED_SHERPA_BY_ID,
    MANAGED_SHERPA_MODELS,
)
from ...services.sherpa_catalog import (
    MODEL_CATEGORY_ORDER as SHERPA_CATEGORY_ORDER,
)
from ...services.transcriber import PARAKEET_MODEL_IDS
from .common import (
    _audio_preflight,
    get_app_settings,
    get_db,
    get_model_status,
    get_task_manager,
)
from .model_runtime import (
    RUNTIME_LABELS,
    _catalog_model_definitions,
    _qwen_presentation,
    _resolve_model,
    _runtime_available,
    _runtime_ids,
    _runtime_options,
    _select_runtime,
    _whisper_presentation,
)

router = APIRouter()

__all__ = [
    "RUNTIME_LABELS",
    "_catalog_model_definitions",
    "_do_prepare_transcription_model",
    "_qwen_presentation",
    "_resolve_model",
    "_runtime_available",
    "_runtime_ids",
    "_runtime_options",
    "_select_runtime",
    "_whisper_presentation",
    "delete_imported_model",
    "delete_transcription_model_files",
    "import_local_model",
    "imported_models",
    "prepare_transcription_model",
    "router",
    "scan_local_models",
    "transcription_models",
    "validate_imported_model",
    "validate_transcription_model",
]


@router.get("/transcription/models")
def transcription_models(project_id: Optional[str] = None, language: str = "auto"):
    try:
        settings = get_app_settings()
    except Exception:
        settings = {}
    audio = None
    if project_id:
        db = get_db()
        row = db.execute("SELECT audio_path FROM projects WHERE id=?", (project_id,)).fetchone()
        db.close()
        audio = _audio_preflight(row["audio_path"] if row else None)
    recommended = _resolve_model("auto", language)
    normalized_language = (language or "auto").lower()
    if normalized_language in {"", "auto"}:
        recommendation_reason = "源语言为自动检测，按约定使用 Whisper Small"
    elif recommended in MANAGED_SHERPA_BY_ID:
        recommendation_reason = (
            f"已下载的 {MANAGED_SHERPA_BY_ID[recommended].name} 与所选语言匹配"
        )
    elif recommended == "small":
        recommendation_reason = (
            "没有已下载且匹配所选语言的专用模型，已回退 Whisper Small"
        )
    else:
        recommendation_reason = "使用设置中明确选择的默认模型"

    model_items = []
    external_paths_enabled = distribution_capabilities().external_runtime_paths
    for definition in _catalog_model_definitions():
        model_id = definition["id"]
        if not external_paths_enabled and is_external_model_reference(model_id):
            continue
        status = get_model_status(
            model_id,
            custom_model_path=settings.get("custom_model_path"),
            coreml_model_path=settings.get("coreml_model_path"),
            coreml_cli_path=settings.get("coreml_cli_path"),
        )
        runtimes = _runtime_options(
            model_id,
            model_ready=bool(status.get("ready") or model_id != PARAKEET_MODEL_ID),
        )
        model_items.append(
            {
                **definition,
                **status,
                "status": status.get("state"),
                # Old clients read ``runtime_error`` while v0.2 uses ``error``.
                "runtime_error": status.get("error") or None,
                "runtimes": runtimes,
                "selected_runtime": (
                    settings.get("transcription_runtime_by_model") or {}
                ).get(model_id),
            }
        )
    if settings.get("custom_model_path"):
        status = get_model_status(
            "custom", custom_model_path=settings.get("custom_model_path")
        )
        model_items.append(
            {
                "id": "custom",
                "name": "自定义 Whisper",
                "languages": ["*"],
                "category_id": "local",
                "category_name": "本地与自定义",
                "purpose": "使用用户选择的 CTranslate2 模型目录",
                "language_description": "由本地模型决定",
                "size_label": "本地模型",
                "publisher": "用户自定义",
                "tags": ["本地目录", "CPU"],
                "source_site": "本地目录",
                **status,
                "status": status.get("state"),
                "runtime_error": status.get("error") or None,
                "runtimes": _runtime_options("custom", model_ready=bool(status.get("ready"))),
                "selected_runtime": (
                    settings.get("transcription_runtime_by_model") or {}
                ).get("custom"),
            }
        )
    for imported in get_imported() if external_paths_enabled else []:
        checked = validate_imported(imported["id"])
        model_items.append(
            {
                "id": imported["id"],
                "name": imported["display_name"],
                "languages": ["*"],
                "category_id": "local",
                "category_name": "本地与自定义",
                "purpose": "原地引用已导入模型",
                "language_description": "由本地模型决定",
                "size_label": "本地模型",
                "publisher": "本地导入",
                "tags": ["本地模型", imported["format"]],
                "source_site": "本地目录",
                "ready": checked["ready"],
                "source": "imported_reference",
                "state": checked["status"],
                "status": checked["status"],
                "download_required": False,
                "runtime_error": checked.get("last_error") or None,
                "runtimes": _runtime_options(imported["id"], imported, checked["ready"]),
                "selected_runtime": (
                    settings.get("transcription_runtime_by_model") or {}
                ).get(imported["id"]),
                "format": imported["format"],
                "version": imported["version"],
            }
        )
    return {
        "recommended_model": recommended,
        "recommendation_reason": recommendation_reason,
        "audio": audio,
        "category_order": list(dict.fromkeys((*SHERPA_CATEGORY_ORDER, *MODEL_CATEGORY_ORDER))),
        "models": model_items,
    }


@router.post("/transcription/models/scan")
def scan_local_models(request: ModelScanRequest):
    require_external_runtime_paths()
    try:
        models = scan_models(request.root_path)
        return {"models": models, "candidates": models}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/transcription/models/import")
def import_local_model(request: ModelImportRequest):
    require_external_runtime_paths()
    try:
        return {"model": register_model(request.path, request.cli_path, request.display_name)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/transcription/models/imported")
def imported_models():
    require_external_runtime_paths()
    return {"models": get_imported()}


@router.post("/transcription/models/imported/{model_id:path}/validate")
def validate_imported_model(model_id: str):
    require_external_runtime_paths()
    try:
        return validate_imported(model_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/transcription/models/imported/{model_id:path}")
def delete_imported_model(model_id: str):
    require_external_runtime_paths()
    remove_imported(model_id)
    return {"message": "已从字幕工厂移除登记，源模型未被删除"}


@router.get("/transcription/models/{model_id}/validate")
def validate_transcription_model(model_id: str):
    if is_external_model_reference(model_id):
        require_external_runtime_paths()
    try:
        settings = get_app_settings()
    except Exception:
        settings = {}
    status = get_model_status(
        model_id,
        custom_model_path=settings.get("custom_model_path"),
        coreml_model_path=settings.get("coreml_model_path"),
        coreml_cli_path=settings.get("coreml_cli_path"),
    )
    if model_id in MANAGED_SHERPA_BY_ID:
        managed_status = managed_model_status(model_id, deep=True)
        if model_id in QWEN_ASR_CATALOG_BY_ID:
            mlx_status = runtime_model_status(model_id, "mlx")
            ready = bool(managed_status.get("ready") or mlx_status.get("model_ready"))
            status = {
                **managed_status,
                "ready": ready,
                "state": "ready" if ready else managed_status.get("state"),
                "download_required": not ready,
                "error": "" if ready else managed_status.get("error", "模型尚未下载"),
            }
        else:
            status = managed_status
    names = {
        **{item.id: item.name for item in WHISPER_MODEL_CATALOG},
        **{item.id: item.name for item in QWEN_ASR_MODEL_CATALOG},
        **{item.id: item.name for item in MANAGED_SHERPA_MODELS},
        FUN_ASR_MODEL_ID: "Fun-Realtime-ASR",
        "custom": "自定义 Whisper",
        PARAKEET_MODEL_ID: "Parakeet V3 Core ML",
        PARAKEET_ONNX_MODEL_ID: "Parakeet V3 ONNX",
    }
    languages = (
        sorted(PARAKEET_SUPPORTED_LANGUAGES)
        if model_id in PARAKEET_MODEL_IDS
        else list(MANAGED_SHERPA_BY_ID[model_id].languages)
        if model_id in MANAGED_SHERPA_BY_ID
        else ["*"]
    )
    return {
        "id": model_id,
        "name": names.get(model_id, model_id),
        "languages": languages,
        **status,
        "status": status.get("state"),
        "runtime_error": status.get("error") or None,
    }


def _do_prepare_transcription_model(
    task_id: str,
    model_id: str,
    runtime: str,
    repair: bool,
    settings: dict,
):
    if model_id in QWEN_ASR_CATALOG_BY_ID and runtime == "mlx":
        prepare_catalog_model(task_id, model_id, runtime, repair=repair)
    elif model_id in WHISPER_CATALOG_BY_ID:
        prepare_whisper_model(task_id, model_id, runtime, repair=repair)
    elif model_id in MANAGED_SHERPA_BY_ID:
        prepare_managed_model(task_id, model_id, repair=repair)
    else:
        prepare_parakeet_model(
            task_id,
            model_id,
            repair=repair,
            coreml_model_dir=settings.get("coreml_model_path"),
            coreml_cli_path=settings.get("coreml_cli_path"),
        )


@router.post("/transcription/models/{model_id}/prepare")
def prepare_transcription_model(model_id: str, request: ModelPrepareRequest):
    if is_external_model_reference(model_id):
        require_external_runtime_paths()
    if (
        model_id not in PARAKEET_MODEL_IDS
        and model_id not in WHISPER_CATALOG_BY_ID
        and model_id not in QWEN_ASR_CATALOG_BY_ID
        and model_id not in MANAGED_SHERPA_BY_ID
    ):
        raise HTTPException(
            400,
            detail={
                "code": "MODEL_PREPARE_UNSUPPORTED",
                "message": "此模型不支持由 App 下载或修复",
            },
        )
    try:
        settings = get_app_settings()
    except Exception:
        settings = {}
    remembered = (settings.get("transcription_runtime_by_model") or {}).get(model_id)
    runtime = request.runtime or remembered
    if model_id in WHISPER_CATALOG_BY_ID:
        runtime = runtime or "cpu"
        if runtime not in {"cpu", "mlx"}:
            raise HTTPException(
                400,
                detail={
                    "code": "RUNTIME_NOT_SUPPORTED",
                    "message": "Whisper 下载格式只能选择 CPU 或 Apple GPU",
                },
            )
    elif model_id in MANAGED_SHERPA_BY_ID:
        runtime = runtime or "cpu"
        if runtime not in MANAGED_SHERPA_BY_ID[model_id].runtimes:
            raise HTTPException(
                400,
                detail={
                    "code": "RUNTIME_NOT_SUPPORTED",
                    "message": "所选运行设备不支持当前模型",
                },
            )
    elif model_id in QWEN_ASR_CATALOG_BY_ID:
        runtime = runtime or "mlx"
        if runtime != "mlx":
            raise HTTPException(
                400,
                detail={
                    "code": "RUNTIME_NOT_SUPPORTED",
                    "message": "Qwen3-ASR 1.7B 只能选择 Apple GPU（MLX）",
                },
            )
    elif model_id == PARAKEET_ONNX_MODEL_ID:
        runtime = runtime or "cpu"
        if runtime not in {"cpu", "coreml"}:
            raise HTTPException(
                400,
                detail={
                    "code": "RUNTIME_NOT_SUPPORTED",
                    "message": "Parakeet ONNX 只能选择 CPU 或 Core ML",
                },
            )
    else:
        runtime = "external_coreml"
    tm = get_task_manager()
    task_id = tm.create_task(None, "prepare_model")
    tm.update_task(
        task_id,
        details={"model_id": model_id, "runtime": runtime},
    )
    tm.run_background(
        task_id,
        _do_prepare_transcription_model,
        model_id,
        runtime,
        request.repair,
        settings,
    )
    return {
        "task_id": task_id,
        "model_id": model_id,
        "runtime": runtime,
        "message": "正在修复模型" if request.repair else "正在准备模型",
    }


@router.delete("/transcription/models/{model_id}/files")
def delete_transcription_model_files(model_id: str):
    try:
        if model_id in QWEN_ASR_CATALOG_BY_ID:
            removed_bytes = 0
            if model_id in MANAGED_SHERPA_BY_ID:
                removed_bytes += int(remove_managed_model(model_id)["removed_bytes"])
            catalog_result = remove_catalog_model(model_id)
            removed_bytes += int(catalog_result["removed_bytes"])
            return {**catalog_result, "removed_bytes": removed_bytes}
        return remove_managed_model(model_id)
    except Exception as exc:
        status_code = 409 if getattr(exc, "error_code", "") == "MODEL_IN_USE" else 400
        raise HTTPException(
            status_code,
            detail={
                "code": getattr(exc, "error_code", "MODEL_REMOVE_FAILED"),
                "message": str(exc),
                "suggestion": getattr(exc, "suggestion", "请检查模型状态后重试"),
            },
        ) from exc
