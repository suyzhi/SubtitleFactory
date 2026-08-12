"""Pinned transcription-model catalog and verified App-managed downloader.

Every network model is addressed by an immutable repository revision. Downloads
land in a resumable staging directory, every required file is verified by size
and SHA-256, and only then is the live model directory replaced atomically.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..models.database import get_db
from ..utils.config import MLX_MODELS_DIR, QWEN_MLX_MODELS_DIR, WHISPER_MODELS_DIR
from ..utils.task_manager import TaskCancelled, task_manager
from ..version import product_user_agent


@dataclass(frozen=True)
class CatalogFile:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ModelVariant:
    runtime: str
    repository: str
    revision: str
    files: tuple[CatalogFile, ...]

    @property
    def download_bytes(self) -> int:
        return sum(item.size for item in self.files)

    @property
    def source_url(self) -> str:
        return f"https://huggingface.co/{self.repository}/tree/{self.revision}"


@dataclass(frozen=True)
class ModelDefinition:
    id: str
    name: str
    category_id: str
    category_name: str
    purpose: str
    language_description: str
    languages: tuple[str, ...]
    size_label: str
    publisher: str
    tags: tuple[str, ...]
    variants: dict[str, ModelVariant]


def _f(name: str, size: int, sha256: str) -> CatalogFile:
    return CatalogFile(name, size, sha256)


_COMMON_TOKENIZER = _f(
    "tokenizer.json", 2_203_239,
    "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
)
_COMMON_VOCABULARY = _f(
    "vocabulary.txt", 459_861,
    "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
)
_V3_PREPROCESSOR = _f(
    "preprocessor_config.json", 340,
    "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
)
_V3_TOKENIZER = _f(
    "tokenizer.json", 2_480_617,
    "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca",
)
_V3_VOCABULARY = _f(
    "vocabulary.json", 1_068_114,
    "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
)


def _variant(
    runtime: str,
    repository: str,
    revision: str,
    *files: CatalogFile,
) -> ModelVariant:
    return ModelVariant(runtime, repository, revision, tuple(files))


WHISPER_MODEL_CATALOG: tuple[ModelDefinition, ...] = (
    ModelDefinition(
        "tiny", "Whisper Tiny", "lightweight", "轻量快速",
        "预览、短音频和低配置设备", "多语言", ("*",), "约 75 MB", "OpenAI / Systran",
        ("多语言", "低内存", "CPU", "Apple GPU"),
        {
            "cpu": _variant(
                "cpu", "Systran/faster-whisper-tiny",
                "d90ca5fe260221311c53c58e660288d3deb8d356",
                _f("config.json", 2_249, "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8"),
                _f("model.bin", 75_538_270, "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"),
                _COMMON_TOKENIZER, _COMMON_VOCABULARY,
            ),
            "mlx": _variant(
                "mlx", "mlx-community/whisper-tiny-mlx",
                "6caf9c55601caafbe6508a8b0d216bdf4783c4e8",
                _f("config.json", 262, "aaff20ce8f69beddee3fe0cc1e08f4e92f58586cb9f12ba00a6f73cbfec1cb1c"),
                _f("weights.npz", 74_418_540, "0e03a5993d6eea43b07ee2dcc772b0e4cef5bb227257dacc24bf289387d49186"),
            ),
        },
    ),
    ModelDefinition(
        "base", "Whisper Base", "lightweight", "轻量快速",
        "快速草稿与日常短视频", "多语言", ("*",), "约 145 MB", "OpenAI / Systran",
        ("多语言", "快速", "CPU", "Apple GPU"),
        {
            "cpu": _variant(
                "cpu", "Systran/faster-whisper-base",
                "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
                _f("config.json", 2_309, "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a"),
                _f("model.bin", 145_217_532, "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9"),
                _COMMON_TOKENIZER, _COMMON_VOCABULARY,
            ),
            "mlx": _variant(
                "mlx", "mlx-community/whisper-base-mlx",
                "1e3e249fb8d01c655324bd6841b1deadffd6d04c",
                _f("config.json", 262, "737220a6d958b3ad48e78f840fa991556266983c84ea2ca40e413389c62e4c2f"),
                _f("weights.npz", 143_724_204, "2f57d5f3ef473054c638961f90716f4ee415e8108de81313eccb2c5fd62eff0b"),
            ),
        },
    ),
    ModelDefinition(
        "small", "Whisper Small", "balanced", "日常均衡",
        "日常字幕的默认选择", "多语言", ("*",), "约 484 MB", "OpenAI / Systran",
        ("推荐", "多语言", "均衡", "CPU", "Apple GPU"),
        {
            "cpu": _variant(
                "cpu", "Systran/faster-whisper-small",
                "536b0662742c02347bc0e980a01041f333bce120",
                _f("config.json", 2_370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
                _f("model.bin", 483_546_902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
                _COMMON_TOKENIZER, _COMMON_VOCABULARY,
            ),
            "mlx": _variant(
                "mlx", "mlx-community/whisper-small-mlx",
                "45f3915923c7a79a5a5b5a7d909d39aeb0e5630e",
                _f("config.json", 266, "e8f58e638208af66d5d5d67801259dc7a12d199e971967a9f9d33a8e3635668e"),
                _f("weights.npz", 481_307_592, "55b6674c9b339702d486e2b1573839a66f8ec8f821ed2886993ef717a86b09f5"),
            ),
        },
    ),
    ModelDefinition(
        "medium", "Whisper Medium", "balanced", "日常均衡",
        "更复杂口音和嘈杂录音", "多语言", ("*",), "约 1.53 GB", "OpenAI / Systran",
        ("多语言", "高质量", "CPU", "Apple GPU"),
        {
            "cpu": _variant(
                "cpu", "Systran/faster-whisper-medium",
                "08e178d48790749d25932bbc082711ddcfdfbc4f",
                _f("config.json", 2_257, "3622a2ddc41ec0e0fd4e68c13c6830f03b90c38d89aaad184de02c8c642cf807"),
                _f("model.bin", 1_527_906_378, "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae"),
                _COMMON_TOKENIZER, _COMMON_VOCABULARY,
            ),
            "mlx": _variant(
                "mlx", "mlx-community/whisper-medium-mlx",
                "7fc08c4eac4c316526498f147dfdee6f6303f975",
                _f("config.json", 268, "3ff0b3f17a5a3a614327ffd835a3c8f6c78f39cbd39e84dbff4b0ae267c4d2e4"),
                _f("weights.npz", 1_524_924_912, "10b597c2bcb1bcc38b2d3d24cd4f0885f461a7cd70e8444d6ad5a763ece549ea"),
            ),
        },
    ),
    ModelDefinition(
        "large-v3", "Whisper Large V3", "performance", "高性能 / 高精度",
        "最高精度与复杂多语言内容", "多语言", ("*",), "约 3.09 GB", "OpenAI / Systran",
        ("多语言", "最高精度", "CPU", "Apple GPU"),
        {
            "cpu": _variant(
                "cpu", "Systran/faster-whisper-large-v3",
                "edaa852ec7e145841d8ffdb056a99866b5f0a478",
                _f("config.json", 2_394, "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9"),
                _f("model.bin", 3_087_284_237, "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"),
                _V3_PREPROCESSOR, _V3_TOKENIZER, _V3_VOCABULARY,
            ),
            "mlx": _variant(
                "mlx", "mlx-community/whisper-large-v3-mlx",
                "49e6aa286ad60c14352c404340ded53710378a11",
                _f("config.json", 269, "34982ce6ae286095000f82ae9583b3431639e8b092bf60c961f203745e6500e3"),
                _f("weights.npz", 3_083_520_416, "05ff791ce3630fae47e7c51004e9666204d786246ec07cac6110af768099b40d"),
            ),
        },
    ),
    ModelDefinition(
        "large-v3-turbo", "Whisper Large V3 Turbo", "performance", "高性能 / 高精度",
        "接近 Large V3 精度的高速转写", "多语言", ("*",), "约 1.62 GB", "OpenAI / Dropbox Dash",
        ("多语言", "高速", "CPU", "Apple GPU"),
        {
            "cpu": _variant(
                "cpu", "dropbox-dash/faster-whisper-large-v3-turbo",
                "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
                _f("config.json", 2_263, "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
                _f("model.bin", 1_617_884_929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
                _V3_PREPROCESSOR,
                _f("tokenizer.json", 2_710_337, "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
                _V3_VOCABULARY,
            ),
            "mlx": _variant(
                "mlx", "mlx-community/whisper-large-v3-turbo",
                "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
                _f("config.json", 268, "b34fc29e4e11e0a25e812775dd67f4dd16fc2c8eb43d28ae25ff7d660ecb6379"),
                _f("weights.safetensors", 1_613_977_612, "951ed3fc1203e6a62467abb2144a96ce7eafca8fa77e3704fdb8635ff3e7f8a6"),
            ),
        },
    ),
    ModelDefinition(
        "distil-large-v3", "Distil-Whisper Large V3", "english", "英语专用",
        "英语内容的高质量高速转写", "仅英语；其他语言请使用 Whisper Small", ("en",),
        "约 1.51 GB", "Hugging Face / Systran",
        ("英语专用", "高速", "CPU", "Apple GPU"),
        {
            "cpu": _variant(
                "cpu", "Systran/faster-distil-whisper-large-v3",
                "c3058b475261292e64a0412df1d2681c06260fab",
                _f("config.json", 2_690, "90c55f775cc4e0bb17293d0bf12f96557a486f20dea886fabd8e6075a3588b21"),
                _f("model.bin", 1_512_927_867, "b79368e19b6623813609431a6e5ee309a71506701ebc49fd7820e692dec7c5f5"),
                _V3_PREPROCESSOR, _V3_TOKENIZER, _V3_VOCABULARY,
            ),
            "mlx": _variant(
                "mlx", "mlx-community/distil-whisper-large-v3",
                "e1c3c155644be59f8b477c0186719442f7e3fbb0",
                _f("config.json", 268, "ee36db379cd5a9aec87c890dc87199e48affac2a5d8f077e1f2b98068a95afd7"),
                _f("weights.npz", 1_509_130_112, "dacfaf0b80d75c8b217a77a1e1da49d935a17a7581618f4811bd1e94aa7a92d5"),
            ),
        },
    ),
)

WHISPER_CATALOG_BY_ID = {item.id: item for item in WHISPER_MODEL_CATALOG}


QWEN_ASR_MODEL_CATALOG: tuple[ModelDefinition, ...] = (
    ModelDefinition(
        "qwen3-asr-0.6b-int8-2026-03-25", "Qwen3-ASR 0.6B", "specialized", "专业场景",
        "多语言、中文方言、歌词和快速语流", "30 种语言及 22 种中文方言", ("*",),
        "Apple GPU 下载约 1.76 GB", "Qwen / MLX Qwen3-ASR",
        ("多语言", "方言", "Apple GPU", "MLX"),
        {
            "mlx": _variant(
                "mlx", "Qwen/Qwen3-ASR-0.6B",
                "5eb144179a02acc5e5ba31e748d22b0cf3e303b0",
                _f("chat_template.json", 1_161, "75a8cfca24f00de72d796fbfed6858fc9614ef3dabd8696684cc3bc03a9c58ff"),
                _f("config.json", 6_193, "76d3ae4601ce939830b2517f4a6cadb86cc51316c3900af6b020b051c21a478c"),
                _f("generation_config.json", 142, "1da527824d81e07118facff437e03f2e24a23311e3bdeb2368973fe77e5f275c"),
                _f("merges.txt", 1_671_853, "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5"),
                _f("model.safetensors", 1_876_091_704, "79d6cbd4c98c7bbffe9db2edac07f56cd6637d0d5944b27f6c2b8353840323ea"),
                _f("preprocessor_config.json", 330, "45e120a4eda2c20c5d7f2ea9354e63536bf35e27aa573fb7cdf78017b378770d"),
                _f("tokenizer_config.json", 12_487, "4942d005604266809309cabc9f4e9cb89ce855d59b14681fdc0e1cc62ea26c4c"),
                _f("vocab.json", 2_776_833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
            ),
        },
    ),
    ModelDefinition(
        "qwen3-asr-1.7b", "Qwen3-ASR 1.7B", "specialized", "专业场景",
        "Apple Silicon 上的高精度多语言与中文方言转写",
        "30 种语言及 22 种中文方言", ("*",), "下载约 4.38 GB", "Qwen / MLX Qwen3-ASR",
        ("高精度", "多语言", "方言", "Apple GPU", "MLX"),
        {
            "mlx": _variant(
                "mlx", "Qwen/Qwen3-ASR-1.7B",
                "7278e1e70fe206f11671096ffdd38061171dd6e5",
                _f("chat_template.json", 1_161, "75a8cfca24f00de72d796fbfed6858fc9614ef3dabd8696684cc3bc03a9c58ff"),
                _f("config.json", 6_194, "2e74a751548b8ad7d7526d29365ad8144c345d8b412b1152d25dc6698452712f"),
                _f("generation_config.json", 142, "1da527824d81e07118facff437e03f2e24a23311e3bdeb2368973fe77e5f275c"),
                _f("merges.txt", 1_671_853, "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5"),
                _f("model-00001-of-00002.safetensors", 4_220_320_824, "a4cd1f1a04d90b757dc7f7dd26254e69a013b19e80efe590a83c6a3bde8608d6"),
                _f("model-00002-of-00002.safetensors", 478_200_688, "6e0b9d9e09e2e0238e7ef3cc8a484ab387e91b90f1900bedf88bc92d7929ccfc"),
                _f("model.safetensors.index.json", 64_821, "f994739fe38e5210b9e3e8ce6c6307315e2ceac3cb630e7b7414d69dce520f60"),
                _f("preprocessor_config.json", 330, "45e120a4eda2c20c5d7f2ea9354e63536bf35e27aa573fb7cdf78017b378770d"),
                _f("tokenizer_config.json", 12_487, "4942d005604266809309cabc9f4e9cb89ce855d59b14681fdc0e1cc62ea26c4c"),
                _f("vocab.json", 2_776_833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
            ),
        },
    ),
)

QWEN_ASR_CATALOG_BY_ID = {item.id: item for item in QWEN_ASR_MODEL_CATALOG}
MODEL_CATALOG_BY_ID = {**WHISPER_CATALOG_BY_ID, **QWEN_ASR_CATALOG_BY_ID}
MODEL_CATEGORY_ORDER = ("lightweight", "balanced", "performance", "english", "parakeet", "cloud")
_MANIFEST_NAME = ".subtitle-factory-manifest.json"
_DOWNLOAD_LOCK = threading.Lock()


class ModelDownloadError(RuntimeError):
    def __init__(
        self,
        message: str,
        error_code: str,
        *,
        suggestion: str,
        recoverable: bool = True,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.suggestion = suggestion
        self.recoverable = recoverable
        self.available_actions = ["retry", "repair"] if recoverable else []


def model_definition(model_id: str) -> ModelDefinition | None:
    return MODEL_CATALOG_BY_ID.get(model_id)


def variant_for(model_id: str, runtime: str) -> ModelVariant:
    definition = model_definition(model_id)
    if not definition or runtime not in definition.variants:
        raise ModelDownloadError(
            "所选模型不支持该运行设备",
            "MODEL_RUNTIME_UNSUPPORTED",
            suggestion="请重新选择此模型支持的 CPU 或 Apple GPU 格式",
            recoverable=False,
        )
    return definition.variants[runtime]


def managed_model_dir(model_id: str, runtime: str) -> Path:
    if model_id in QWEN_ASR_CATALOG_BY_ID:
        root = QWEN_MLX_MODELS_DIR
    else:
        root = MLX_MODELS_DIR if runtime == "mlx" else WHISPER_MODELS_DIR
    return root / model_id


def _manifest_identity(variant: ModelVariant) -> dict:
    return {
        "schema": 1,
        "runtime": variant.runtime,
        "repository": variant.repository,
        "revision": variant.revision,
        "files": [
            {"name": item.name, "size": item.size, "sha256": item.sha256}
            for item in variant.files
        ],
    }


def _read_manifest(path: Path) -> dict | None:
    try:
        return json.loads((path / _MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _files_have_expected_sizes(path: Path, variant: ModelVariant) -> bool:
    return path.is_dir() and all(
        (path / item.name).is_file() and (path / item.name).stat().st_size == item.size
        for item in variant.files
    )


def managed_model_ready(model_id: str, runtime: str) -> bool:
    variant = variant_for(model_id, runtime)
    path = managed_model_dir(model_id, runtime)
    return (
        _files_have_expected_sizes(path, variant)
        and _read_manifest(path) == _manifest_identity(variant)
    )


def find_legacy_model(model_id: str, runtime: str) -> Path | None:
    """Locate an existing compatible cache without moving or deleting it."""
    if model_id in QWEN_ASR_CATALOG_BY_ID:
        root = QWEN_MLX_MODELS_DIR
    else:
        root = MLX_MODELS_DIR if runtime == "mlx" else WHISPER_MODELS_DIR
    if not root.is_dir():
        return None
    if model_id in QWEN_ASR_CATALOG_BY_ID:
        weight_names = (
            "model.safetensors",
            "model-00001-of-00002.safetensors",
        )
    else:
        weight_names = (
            ("weights.npz", "weights.safetensors")
            if runtime == "mlx"
            else ("model.bin",)
        )
    for weight_name in weight_names:
        for weight in root.rglob(weight_name):
            parent = weight.parent
            if model_id not in str(parent).lower():
                continue
            if (parent / "config.json").is_file():
                if runtime == "mlx" or (parent / "tokenizer.json").is_file():
                    return parent.resolve()
    return None


def resolve_local_model(model_id: str, runtime: str) -> Path | None:
    if managed_model_ready(model_id, runtime):
        return managed_model_dir(model_id, runtime).resolve()
    return find_legacy_model(model_id, runtime)


def runtime_model_status(model_id: str, runtime: str) -> dict:
    variant = variant_for(model_id, runtime)
    managed = managed_model_ready(model_id, runtime)
    legacy = None if managed else find_legacy_model(model_id, runtime)
    ready = managed or legacy is not None
    path = managed_model_dir(model_id, runtime)
    partial = path.exists() or path.with_name(f".{model_id}.{runtime}.downloading").exists()
    return {
        "model_ready": ready,
        "download_required": not ready,
        "download_bytes": variant.download_bytes,
        "repository": variant.repository,
        "revision": variant.revision,
        "source_url": variant.source_url,
        "source": "app_download" if managed else ("legacy_cache" if legacy else "huggingface"),
        "status": "ready" if ready else ("invalid" if partial else "not_downloaded"),
    }


def _sha256(path: Path, checkpoint: Callable[[], None]) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            checkpoint()
            chunk = source.read(4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _verify_file(path: Path, item: CatalogFile, checkpoint: Callable[[], None]) -> None:
    actual_size = path.stat().st_size if path.is_file() else 0
    if actual_size != item.size:
        raise ModelDownloadError(
            f"模型文件大小不匹配：{item.name}",
            "MODEL_INTEGRITY_FAILED",
            suggestion="来源可能已变更；请稍后重试或运行来源验证",
        )
    if _sha256(path, checkpoint) != item.sha256:
        raise ModelDownloadError(
            f"模型文件校验失败：{item.name}",
            "MODEL_INTEGRITY_FAILED",
            suggestion="请执行修复；原有可用模型仍然保留",
        )


def _download_file(
    variant: ModelVariant,
    item: CatalogFile,
    destination: Path,
    checkpoint: Callable[[], None],
    report: Callable[[int, bool], None],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    if destination.is_file() and destination.stat().st_size == item.size:
        try:
            _verify_file(destination, item, checkpoint)
            report(item.size, False)
            return
        except ModelDownloadError:
            destination.unlink(missing_ok=True)
    existing = partial.stat().st_size if partial.is_file() else 0
    if existing > item.size:
        partial.unlink()
        existing = 0
    url = (
        f"https://huggingface.co/{variant.repository}/resolve/{variant.revision}/"
        f"{urllib.parse.quote(item.name)}?download=true"
    )
    headers = {
        "Accept": "application/octet-stream",
        "User-Agent": product_user_agent("verified-model-downloader"),
    }
    if existing:
        headers["Range"] = f"bytes={existing}-"
    checkpoint()
    try:
        response = urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=60,
        )
    except urllib.error.HTTPError as exc:
        code = "MODEL_SOURCE_CHANGED" if exc.code in {404, 410} else "MODEL_NETWORK_UNREACHABLE"
        suggestion = (
            "固定提交或文件已不可访问；请运行来源验证并更新应用"
            if code == "MODEL_SOURCE_CHANGED" else "请检查网络或代理后重试"
        )
        raise ModelDownloadError(
            f"无法获取模型文件 {item.name}：HTTP {exc.code}",
            code, suggestion=suggestion,
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ModelDownloadError(
            f"无法连接模型来源：{exc}",
            "MODEL_NETWORK_UNREACHABLE",
            suggestion="请检查网络或代理后重试；已下载部分可断点续传",
        ) from exc
    try:
        status = getattr(response, "status", 200)
        resumed = bool(existing and status == 206)
        if existing and not resumed:
            existing = 0
        mode = "ab" if resumed else "wb"
        downloaded = existing
        report(downloaded, resumed)
        with partial.open(mode) as output:
            while True:
                checkpoint()
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                report(downloaded, resumed)
    except TaskCancelled:
        raise
    except OSError as exc:
        raise ModelDownloadError(
            f"写入模型暂存目录失败：{exc}",
            "MODEL_DISK_SPACE_INSUFFICIENT",
            suggestion="请释放磁盘空间后重试；已下载部分会保留",
        ) from exc
    finally:
        response.close()
    if not partial.is_file() or partial.stat().st_size != item.size:
        raise ModelDownloadError(
            f"模型文件下载不完整：{item.name}",
            "MODEL_NETWORK_UNREACHABLE",
            suggestion="请重试，下载器会从现有进度继续",
        )
    os.replace(partial, destination)
    try:
        _verify_file(destination, item, checkpoint)
    except ModelDownloadError:
        destination.unlink(missing_ok=True)
        raise


def _write_manifest(path: Path, variant: ModelVariant) -> None:
    payload = json.dumps(
        _manifest_identity(variant), ensure_ascii=False, indent=2, sort_keys=True,
    )
    temporary = path / f"{_MANIFEST_NAME}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path / _MANIFEST_NAME)


def prepare_catalog_model(
    task_id: str,
    model_id: str,
    runtime: str,
    *,
    repair: bool = False,
) -> dict:
    """Prepare a pinned CPU or MLX model without risking the current install."""
    variant = variant_for(model_id, runtime)
    final = managed_model_dir(model_id, runtime)
    if not repair:
        existing = resolve_local_model(model_id, runtime)
        if existing:
            status = runtime_model_status(model_id, runtime)
            task_manager.update_task(
                task_id, step="model_ready", progress=100,
                message="模型已就绪", details={"model_status": status},
            )
            return status

    with _DOWNLOAD_LOCK:
        if not repair:
            existing = resolve_local_model(model_id, runtime)
            if existing:
                return runtime_model_status(model_id, runtime)
        final.parent.mkdir(parents=True, exist_ok=True)
        staging = final.with_name(f".{model_id}.{runtime}.downloading")
        staging.mkdir(parents=True, exist_ok=True)
        downloaded_before = sum(
            min((staging / f"{item.name}.part").stat().st_size, item.size)
            if (staging / f"{item.name}.part").is_file()
            else (item.size if (staging / item.name).is_file() else 0)
            for item in variant.files
        )
        required_free = max(0, variant.download_bytes - downloaded_before) + 256 * 1024 * 1024
        try:
            free = shutil.disk_usage(final.parent).free
        except OSError as exc:
            raise ModelDownloadError(
                f"无法检查模型磁盘空间：{exc}",
                "MODEL_DISK_SPACE_INSUFFICIENT",
                suggestion="请检查模型目录是否可写",
            ) from exc
        if free < required_free:
            raise ModelDownloadError(
                "模型下载空间不足",
                "MODEL_DISK_SPACE_INSUFFICIENT",
                suggestion=(
                    f"至少需要额外 {required_free / 1024 ** 3:.1f} GB 可用空间；"
                    "不会删除现有模型"
                ),
            )
        completed = 0
        resumed_any = downloaded_before > 0
        total = variant.download_bytes
        task_manager.update_task(
            task_id, step="downloading_model", progress=1,
            message="正在准备经过校验的模型下载...",
            details={"model_download": {
                "status": "downloading", "model_id": model_id, "runtime": runtime,
                "downloaded_bytes": downloaded_before, "total_bytes": total,
                "resumed": resumed_any, "verification": "pending",
            }},
        )
        for item in variant.files:
            task_manager.checkpoint(task_id)
            prior_complete = completed

            def report(file_bytes: int, resumed: bool) -> None:
                current = min(total, prior_complete + file_bytes)
                percent = current * 100 / max(total, 1)
                task_manager.update_task(
                    task_id, step="downloading_model",
                    progress=min(88, 1 + percent * 0.87),
                    message=(
                        f"正在下载 {model_id} · {runtime.upper()}：{percent:.1f}% · "
                        f"{current / 1024 ** 2:.1f} / {total / 1024 ** 2:.1f} MiB"
                    ),
                    details={"model_download": {
                        "status": "downloading", "model_id": model_id, "runtime": runtime,
                        "current_file": item.name, "downloaded_bytes": current,
                        "total_bytes": total, "resumed": resumed_any or resumed,
                        "verification": "pending",
                    }},
                )

            _download_file(
                variant, item, staging / item.name,
                lambda: task_manager.checkpoint(task_id), report,
            )
            completed += item.size

        task_manager.update_task(
            task_id, step="verifying_model", progress=90,
            message="下载完成，正在逐文件校验大小与 SHA-256...",
            details={"model_download": {
                "status": "verifying", "model_id": model_id, "runtime": runtime,
                "downloaded_bytes": total, "total_bytes": total,
                "resumed": resumed_any, "verification": "running",
            }},
        )
        for index, item in enumerate(variant.files):
            _verify_file(staging / item.name, item, lambda: task_manager.checkpoint(task_id))
            task_manager.update_task(
                task_id, progress=90 + ((index + 1) / len(variant.files)) * 7,
            )
        _write_manifest(staging, variant)

        backup = final.with_name(f".{model_id}.{runtime}.backup-{uuid.uuid4().hex}")
        try:
            if final.exists():
                os.replace(final, backup)
            os.replace(staging, final)
        except OSError as exc:
            if backup.exists() and not final.exists():
                os.replace(backup, final)
            raise ModelDownloadError(
                f"无法原子安装模型：{exc}",
                "MODEL_INSTALL_FAILED",
                suggestion="请检查模型目录权限；原有模型已保留",
            ) from exc
        finally:
            if backup.exists() and final.exists():
                shutil.rmtree(backup, ignore_errors=True)

        status = runtime_model_status(model_id, runtime)
        task_manager.update_task(
            task_id, step="model_ready", progress=100, message="模型下载并校验完成",
            details={"model_download": {
                "status": "ready", "model_id": model_id, "runtime": runtime,
                "downloaded_bytes": total, "total_bytes": total,
                "resumed": resumed_any, "verification": "passed",
            }, "model_status": status},
        )
        return status


def prepare_whisper_model(
    task_id: str,
    model_id: str,
    runtime: str,
    *,
    repair: bool = False,
) -> dict:
    """Backward-compatible wrapper for callers that prepare Whisper models."""
    return prepare_catalog_model(task_id, model_id, runtime, repair=repair)


def remove_catalog_model(model_id: str) -> dict:
    """Remove only App-managed catalog files, never a discovered legacy cache."""
    definition = model_definition(model_id)
    if not definition:
        raise ModelDownloadError(
            "不支持移除此模型",
            "MODEL_REMOVE_UNSUPPORTED",
            suggestion="请重新选择模型",
            recoverable=False,
        )
    db = get_db()
    try:
        rows = db.execute(
            """SELECT details FROM tasks
               WHERE status IN ('pending','running','paused')
                 AND type IN ('transcribe','workflow','prepare_model')"""
        ).fetchall()
    finally:
        db.close()
    for row in rows:
        try:
            details = json.loads(row["details"] or "{}")
        except (TypeError, ValueError):
            details = {}
        resolution = details.get("model_resolution") or {}
        if details.get("model_id") == model_id or resolution.get("model_id") == model_id:
            raise ModelDownloadError(
                "有转写或模型任务正在运行，暂时不能移除模型",
                "MODEL_IN_USE",
                suggestion="任务完成或终止后再试",
            )

    targets: set[Path] = set()
    for runtime in definition.variants:
        final = managed_model_dir(model_id, runtime)
        targets.add(final)
        targets.add(final.with_name(f".{model_id}.{runtime}.downloading"))
    removed_bytes = 0
    for target in targets:
        if target.is_symlink() or target.is_file():
            removed_bytes += target.stat().st_size
            target.unlink()
        elif target.is_dir():
            removed_bytes += sum(
                path.stat().st_size for path in target.rglob("*") if path.is_file()
            )
            shutil.rmtree(target, ignore_errors=False)
    return {
        "model_id": model_id,
        "removed": True,
        "removed_bytes": removed_bytes,
        "message": "模型文件已移除，可随时重新下载",
    }
