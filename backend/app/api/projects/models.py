"""
字幕工厂（简洁版）- 本地转写模型目录、运行设备选择与模型准备路由
"""

import importlib.util
from typing import Optional

from fastapi import APIRouter, HTTPException

from ...models.schemas import ModelPrepareRequest
from ...services.distribution import (
    distribution_capabilities,
    is_external_model_reference,
    require_external_runtime_paths,
)
from ...services.managed_sherpa import (
    managed_model_status,
    prepare_managed_model,
)
from ...services.model_catalog import (
    MODEL_CATEGORY_ORDER,
    QWEN_ASR_CATALOG_BY_ID,
    QWEN_ASR_MODEL_CATALOG,
    WHISPER_CATALOG_BY_ID,
    WHISPER_MODEL_CATALOG,
    prepare_catalog_model,
    prepare_whisper_model,
    runtime_model_status,
)
from ...services.parakeet_transcriber import (
    PARAKEET_ARCHIVE_BYTES,
    PARAKEET_COREML_DOWNLOAD_BYTES,
    PARAKEET_COREML_REPOSITORY,
    PARAKEET_COREML_REVISION,
    PARAKEET_COREML_SOURCE_URL,
    PARAKEET_MODEL_ID,
    PARAKEET_ONNX_MODEL_ID,
    PARAKEET_SUPPORTED_LANGUAGES,
    SILERO_VAD_BYTES,
    prepare_parakeet_model,
)
from ...services.sherpa_catalog import (
    MANAGED_SHERPA_BY_ID,
    MANAGED_SHERPA_MODELS,
)
from ...services.sherpa_catalog import (
    MODEL_CATEGORY_ORDER as SHERPA_CATEGORY_ORDER,
)
from ...services.transcriber import (
    PARAKEET_MODEL_IDS,
    QWEN_ASR_MODEL_IDS,
    SUPPORTED_TRANSCRIPTION_MODELS,
    resolve_transcription_model,
)
from .common import (
    _audio_preflight,
    get_app_settings,
    get_db,
    get_model_status,
    get_task_manager,
)

router = APIRouter()

RUNTIME_LABELS = {
    "cpu": ("CPU", "faster-whisper / sherpa-onnx"),
    "mlx": ("Apple GPU", "MLX Whisper · Metal"),
    "coreml": ("Apple GPU / Neural Engine", "sherpa-onnx · Core ML"),
    "external_coreml": ("Apple Neural Engine", "Core ML · FluidAudio"),
}

_WHISPER_TIERS = {
    "tiny": ("很快", "基础", "很低", ("低配置", "快速草稿")),
    "base": ("很快", "基础", "低", ("低配置", "快速草稿")),
    "small": ("快", "均衡", "中", ("通用字幕", "多语言")),
    "medium": ("中等", "高", "高", ("高精度", "嘈杂录音")),
    "large-v3": ("较慢", "很高", "很高", ("高精度", "复杂多语言")),
    "large-v3-turbo": ("中等", "很高", "高", ("高精度", "长节目")),
    "distil-large-v3": ("快", "高", "高", ("英语", "长节目")),
}


def _runtime_ids(model_id: str) -> list[str]:
    if model_id in WHISPER_CATALOG_BY_ID:
        return ["cpu", "mlx"]
    if model_id in MANAGED_SHERPA_BY_ID:
        return list(MANAGED_SHERPA_BY_ID[model_id].runtimes)
    if model_id in QWEN_ASR_CATALOG_BY_ID:
        return ["mlx"]
    if model_id == PARAKEET_MODEL_ID:
        return ["external_coreml"]
    if model_id == PARAKEET_ONNX_MODEL_ID:
        return ["cpu", "coreml"]
    return ["cpu"]


def _runtime_available(runtime_id: str, model_id: str = "") -> tuple[bool, str]:
    if runtime_id == "mlx":
        module = "mlx_qwen3_asr" if model_id in QWEN_ASR_MODEL_IDS else "mlx_whisper"
        ok = importlib.util.find_spec(module) is not None
        label = "MLX Qwen3-ASR" if module == "mlx_qwen3_asr" else "MLX Whisper"
        return ok, f"{label} 已随 App 提供" if ok else f"当前运行包缺少 {label}"
    if runtime_id == "coreml":
        try:
            import onnxruntime

            ok = "CoreMLExecutionProvider" in onnxruntime.get_available_providers()
        except Exception:
            ok = False
        return (
            ok,
            "Core ML Execution Provider 可用"
            if ok
            else "当前 ONNX Runtime 不支持 Core ML",
        )
    return True, "可用"


def _runtime_options(model_id: str, model_ready: bool = True) -> list[dict]:
    result = []
    for runtime_id in _runtime_ids(model_id):
        available, reason = _runtime_available(runtime_id, model_id)
        coreml_status = (
            get_model_status(model_id)
            if runtime_id == "external_coreml" and model_id == PARAKEET_MODEL_ID
            else None
        )
        if runtime_id == "external_coreml" and coreml_status is not None:
            if not model_ready and not coreml_status.get("download_required"):
                available, reason = (
                    False,
                    str(coreml_status.get("error") or "Core ML 转写组件不可用"),
                )
            elif not model_ready:
                reason = "首次使用时从 Hugging Face 下载官方 Core ML 模型"
            else:
                reason = "Core ML 模型与转写组件已就绪"
        label, engine = RUNTIME_LABELS.get(runtime_id, (runtime_id, runtime_id))
        if runtime_id == "mlx" and model_id in QWEN_ASR_MODEL_IDS:
            engine = "Qwen3-ASR · MLX · Metal"
        item = {
            "id": runtime_id,
            "name": label,
            "engine": engine,
            "available": available,
            "reason": reason,
        }
        if model_id in WHISPER_CATALOG_BY_ID:
            item.update(runtime_model_status(model_id, runtime_id))
        elif model_id in MANAGED_SHERPA_BY_ID:
            definition = MANAGED_SHERPA_BY_ID[model_id]
            status = (
                runtime_model_status(model_id, "mlx")
                if runtime_id == "mlx" and model_id in QWEN_ASR_CATALOG_BY_ID
                else managed_model_status(model_id)
            )
            item.update(
                {
                    "model_ready": bool(
                        status.get("model_ready", status.get("ready"))
                    ),
                    "download_required": bool(
                        status.get("download_required", not status.get("ready"))
                    ),
                    "download_bytes": status.get(
                        "download_bytes", definition.archive_size
                    ),
                    "installed_bytes": definition.installed_bytes,
                    "repository": status.get("repository", "k2-fsa/sherpa-onnx"),
                    "revision": status.get("revision", "asr-models"),
                    "source_url": status.get("source_url", definition.source_url),
                    "source": status.get("source", "github"),
                    "status": status.get("status", status.get("state")),
                }
            )
        elif model_id in QWEN_ASR_CATALOG_BY_ID:
            item.update(runtime_model_status(model_id, runtime_id))
        elif model_id == PARAKEET_ONNX_MODEL_ID:
            status = get_model_status(model_id)
            item.update(
                {
                    "model_ready": bool(status.get("ready")),
                    "download_required": not bool(status.get("ready")),
                    "download_bytes": PARAKEET_ARCHIVE_BYTES + SILERO_VAD_BYTES,
                    "repository": "k2-fsa/sherpa-onnx",
                    "revision": "asr-models",
                    "source_url": "https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models",
                    "source": "app_download" if status.get("ready") else "github",
                    "status": status.get("state"),
                }
            )
        elif model_id == PARAKEET_MODEL_ID:
            status = coreml_status or {}
            item.update(
                {
                    "model_ready": bool(model_ready),
                    "download_required": bool(
                        not model_ready and status.get("download_required")
                    ),
                    "download_bytes": PARAKEET_COREML_DOWNLOAD_BYTES,
                    "repository": PARAKEET_COREML_REPOSITORY,
                    "revision": PARAKEET_COREML_REVISION,
                    "source_url": PARAKEET_COREML_SOURCE_URL,
                    "source": status.get("source")
                    or ("app_download" if model_ready else "huggingface"),
                    "status": "ready"
                    if model_ready
                    else status.get("state", "unavailable"),
                }
            )
        result.append(item)
    return result


def _select_runtime(model_id: str, requested: str | None, settings: dict) -> str:
    if is_external_model_reference(model_id):
        require_external_runtime_paths()
    remembered = (settings.get("transcription_runtime_by_model") or {}).get(model_id)
    selected = requested or remembered
    model_ready = True
    if model_id == PARAKEET_MODEL_ID:
        model_ready = bool(get_model_status(model_id).get("ready"))
    options = _runtime_options(model_id, model_ready)
    allowed = {item["id"]: item for item in options}
    if not selected:
        raise HTTPException(
            409,
            detail={
                "code": "RUNTIME_SELECTION_REQUIRED",
                "message": "首次使用此模型前请选择运行设备",
                "model_id": model_id,
                "runtimes": options,
            },
        )
    if selected not in allowed:
        raise HTTPException(
            400,
            detail={
                "code": "RUNTIME_NOT_SUPPORTED",
                "message": "所选运行设备不支持当前模型",
                "model_id": model_id,
                "runtimes": options,
            },
        )
    if not allowed[selected]["available"]:
        raise HTTPException(
            409,
            detail={
                "code": "RUNTIME_UNAVAILABLE",
                "message": allowed[selected]["reason"],
                "model_id": model_id,
                "runtimes": options,
            },
        )
    return selected


def _resolve_model(model: str, language: str) -> str:
    if model != "auto" and model not in SUPPORTED_TRANSCRIPTION_MODELS:
        return model
    if model != "auto":
        return model
    resolution = resolve_transcription_model(model, language)
    return resolution.model_id


def _whisper_presentation(model_id: str) -> dict:
    speed, accuracy, memory, scenarios = _WHISPER_TIERS[model_id]
    definition = WHISPER_CATALOG_BY_ID[model_id]
    english_only = model_id == "distil-large-v3"
    return {
        "family": "Whisper",
        "scenarios": list(scenarios),
        "strengths": [
            "成熟的多语言字幕模型" if not english_only else "英语转写速度与精度均衡",
            "原生逐词时间戳",
            "支持 CPU 与 Apple GPU",
        ],
        "limitations": [
            "不会参与专业场景自动选择",
            "方言或专业术语可改用对应专用模型",
        ]
        if not english_only
        else ["仅支持英语", "不适合中文和其他语言"],
        "speed_tier": speed,
        "accuracy_tier": accuracy,
        "memory_tier": memory,
        "timestamp_mode": "word",
        "punctuation_mode": "native",
        "installed_bytes": min(
            variant.download_bytes for variant in definition.variants.values()
        ),
        "license": "MIT",
    }


def _qwen_presentation(model_id: str) -> dict:
    definition = QWEN_ASR_CATALOG_BY_ID[model_id]
    return {
        "family": "Qwen3-ASR",
        "scenarios": ["高精度", "方言", "歌词与说唱", "复杂语音"],
        "strengths": [
            "30 种语言和 22 种中文方言",
            "Apple Silicon 上使用 MLX Metal GPU 推理",
            "复杂语音、歌词和快速语流表现好",
        ],
        "limitations": [
            "只提供本地 VAD 片段级时间轴",
            "模型较大且不会参与自动下载",
            "仅支持 Apple Silicon 的 MLX 运行时",
        ],
        "speed_tier": "较慢",
        "accuracy_tier": "很高" if model_id.endswith("1.7b") else "高",
        "memory_tier": "很高" if model_id.endswith("1.7b") else "高",
        "timestamp_mode": "segment",
        "punctuation_mode": "native",
        "installed_bytes": definition.variants["mlx"].download_bytes,
        "license": "Apache-2.0",
        "removable": True,
    }


def _catalog_model_definitions() -> list[dict]:
    return (
        [
            {
                "id": item.id,
                "name": item.name,
                "languages": list(item.languages),
                "category_id": item.category_id,
                "category_name": item.category_name,
                "purpose": item.purpose,
                "language_description": item.language_description,
                "size_label": item.size_label,
                "publisher": item.publisher,
                "tags": list(item.tags),
                "source_site": "Hugging Face",
                **_whisper_presentation(item.id),
            }
            for item in WHISPER_MODEL_CATALOG
        ]
        + [
            {
                "id": item.id,
                "name": item.name,
                "languages": list(item.languages),
                "category_id": item.category_id,
                "category_name": item.category_name,
                "purpose": item.purpose,
                "language_description": item.language_description,
                "size_label": item.size_label,
                "publisher": item.publisher,
                "tags": list(item.tags),
                "source_site": "Hugging Face 官方模型库",
                **_qwen_presentation(item.id),
            }
            for item in QWEN_ASR_MODEL_CATALOG
            if item.id not in MANAGED_SHERPA_BY_ID
        ]
        + [
            {
                "id": item.id,
                "name": item.name,
                "languages": list(item.languages),
                "category_id": item.category_id,
                "category_name": item.category_name,
                "purpose": item.purpose,
                "language_description": item.language_description,
                "size_label": (
                    f"下载约 {item.archive_size / 1024 ** 2:.0f} MiB，"
                    f"安装约 {item.installed_bytes / 1024 ** 2:.0f} MiB"
                ),
                "publisher": "官方 sherpa-onnx 模型",
                "tags": list(
                    dict.fromkeys(
                        (
                            *item.tags,
                            *(
                                ("Apple GPU", "Core ML")
                                if "coreml" in item.runtimes
                                else ()
                            ),
                            *(("Apple GPU", "MLX") if "mlx" in item.runtimes else ()),
                        )
                    )
                ),
                "source_site": "GitHub 官方发布页",
                "family": item.family,
                "scenarios": list(item.scenarios),
                "strengths": list(item.strengths)
                + (
                    ["支持 CPU 与 Apple Core ML（GPU / Neural Engine）"]
                    if "coreml" in item.runtimes
                    else ["支持 CPU 与 Apple MLX Metal GPU"]
                    if "mlx" in item.runtimes
                    else []
                ),
                "limitations": list(item.limitations)
                + (
                    [
                        "Core ML 会按算子兼容性调度 GPU / Neural Engine，部分算子可能使用 CPU"
                    ]
                    if "coreml" in item.runtimes
                    else []
                ),
                "speed_tier": item.speed_tier,
                "accuracy_tier": item.accuracy_tier,
                "memory_tier": item.memory_tier,
                "timestamp_mode": item.timestamp_mode,
                "punctuation_mode": item.punctuation_mode,
                "installed_bytes": item.installed_bytes,
                "license": item.license,
                "removable": True,
            }
            for item in MANAGED_SHERPA_MODELS
        ]
        + [
            {
                "id": PARAKEET_ONNX_MODEL_ID,
                "name": "Parakeet V3 ONNX",
                "languages": sorted(PARAKEET_SUPPORTED_LANGUAGES),
                "category_id": "parakeet",
                "category_name": "Parakeet",
                "purpose": "欧洲语种的快速本地转写",
                "language_description": "英语及 25 种欧洲语言",
                "size_label": "下载约 488 MB，安装约 672 MB",
                "publisher": "NVIDIA / k2-fsa",
                "tags": ["欧洲语种", "CPU", "Core ML", "ONNX"],
                "source_site": "GitHub 官方发布页",
                "family": "NeMo TDT",
                "scenarios": ["欧洲语种", "通用字幕", "长节目"],
                "strengths": [
                    "英语和欧洲语种速度快",
                    "原生词元时间戳",
                    "支持 CPU 与 Core ML",
                ],
                "limitations": ["不支持中文、日韩和俄语", "不是小语种通用兜底"],
                "speed_tier": "很快",
                "accuracy_tier": "高",
                "memory_tier": "中",
                "timestamp_mode": "token",
                "punctuation_mode": "native",
                "installed_bytes": 672 * 1024 * 1024,
                "license": "以上游 NVIDIA Parakeet 模型许可为准",
            },
            {
                "id": PARAKEET_MODEL_ID,
                "name": "Parakeet V3 Core ML",
                "languages": sorted(PARAKEET_SUPPORTED_LANGUAGES),
                "category_id": "parakeet",
                "category_name": "Parakeet",
                "purpose": "在 Apple 神经网络引擎上快速转写欧洲语种",
                "language_description": "英语及 25 种欧洲语言",
                "size_label": "下载约 461 MB",
                "publisher": "NVIDIA / FluidInference",
                "tags": ["欧洲语种", "Neural Engine", "Core ML"],
                "source_site": "Hugging Face 官方仓库",
                "family": "NeMo TDT",
                "scenarios": ["欧洲语种", "通用字幕", "长节目"],
                "strengths": [
                    "使用 Apple Neural Engine，速度快且省电",
                    "原生词元时间戳",
                    "随 App 附带开源转写组件",
                ],
                "limitations": [
                    "不支持中文、日韩和俄语",
                    "需要 Apple Silicon 与 macOS 14",
                ],
                "speed_tier": "很快",
                "accuracy_tier": "高",
                "memory_tier": "中",
                "timestamp_mode": "token",
                "punctuation_mode": "native",
                "installed_bytes": PARAKEET_COREML_DOWNLOAD_BYTES,
                "license": "CC-BY-4.0（NVIDIA Parakeet；Core ML 转换：FluidInference）",
            },
        ]
    )


@router.get("/transcription/models")
def transcription_models(project_id: Optional[str] = None, language: str = "auto"):
    try:
        settings = get_app_settings()
    except Exception:
        settings = {}
    audio = None
    if project_id:
        db = get_db()
        row = db.execute(
            "SELECT audio_path FROM projects WHERE id=?", (project_id,)
        ).fetchone()
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
    else:
        recommendation_reason = (
            "没有已下载且匹配所选语言的专用模型，已回退 Whisper Small"
        )

    model_items = []
    external_paths_enabled = distribution_capabilities().external_runtime_paths
    for definition in _catalog_model_definitions():
        model_id = definition["id"]
        if not external_paths_enabled and is_external_model_reference(model_id):
            continue
        status = get_model_status(model_id)
        runtimes = _runtime_options(
            model_id,
            model_ready=bool(status.get("ready") or model_id != PARAKEET_MODEL_ID),
        )
        model_items.append(
            {
                **definition,
                **status,
                "status": status.get("state"),
                "runtime_error": status.get("error") or None,
                "runtimes": runtimes,
                "selected_runtime": (
                    settings.get("transcription_runtime_by_model") or {}
                ).get(model_id),
            }
        )
    return {
        "recommended_model": recommended,
        "recommendation_reason": recommendation_reason,
        "audio": audio,
        "category_order": list(
            dict.fromkeys((*SHERPA_CATEGORY_ORDER, *MODEL_CATEGORY_ORDER))
        ),
        "models": model_items,
    }


def _do_prepare_transcription_model(
    task_id: str,
    model_id: str,
    runtime: str,
    repair: bool,
):
    if model_id in QWEN_ASR_CATALOG_BY_ID and runtime == "mlx":
        prepare_catalog_model(task_id, model_id, runtime, repair=repair)
    elif model_id in WHISPER_CATALOG_BY_ID:
        prepare_whisper_model(task_id, model_id, runtime, repair=repair)
    elif model_id in MANAGED_SHERPA_BY_ID:
        prepare_managed_model(task_id, model_id, repair=repair)
    else:
        prepare_parakeet_model(task_id, model_id, repair=repair)


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
    )
    return {
        "task_id": task_id,
        "model_id": model_id,
        "runtime": runtime,
        "message": "正在修复模型" if request.repair else "正在准备模型",
    }
