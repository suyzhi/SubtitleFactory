"""Pinned Whisper and Qwen3-ASR model catalog definitions and lookup helpers."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any


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


def _f(name: str, size: int, sha256: str) -> CatalogFile:
    return CatalogFile(name, size, sha256)


_COMMON_TOKENIZER = _f(
    "tokenizer.json",
    2_203_239,
    "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
)
_COMMON_VOCABULARY = _f(
    "vocabulary.txt",
    459_861,
    "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
)
_V3_PREPROCESSOR = _f(
    "preprocessor_config.json",
    340,
    "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
)
_V3_TOKENIZER = _f(
    "tokenizer.json",
    2_480_617,
    "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca",
)
_V3_VOCABULARY = _f(
    "vocabulary.json",
    1_068_114,
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
        "tiny",
        "Whisper Tiny",
        "lightweight",
        "轻量快速",
        "预览、短音频和低配置设备",
        "多语言",
        ("*",),
        "约 75 MB",
        "OpenAI / Systran",
        ("多语言", "低内存", "CPU", "Apple GPU"),
        {
            "cpu": _variant(
                "cpu",
                "Systran/faster-whisper-tiny",
                "d90ca5fe260221311c53c58e660288d3deb8d356",
                _f("config.json", 2_249, "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8"),
                _f("model.bin", 75_538_270, "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"),
                _COMMON_TOKENIZER,
                _COMMON_VOCABULARY,
            ),
            "mlx": _variant(
                "mlx",
                "mlx-community/whisper-tiny-mlx",
                "6caf9c55601caafbe6508a8b0d216bdf4783c4e8",
                _f("config.json", 262, "aaff20ce8f69beddee3fe0cc1e08f4e92f58586cb9f12ba00a6f73cbfec1cb1c"),
                _f("weights.npz", 74_418_540, "0e03a5993d6eea43b07ee2dcc772b0e4cef5bb227257dacc24bf289387d49186"),
            ),
        },
    ),
    ModelDefinition(
        "base",
        "Whisper Base",
        "lightweight",
        "轻量快速",
        "快速草稿与日常短视频",
        "多语言",
        ("*",),
        "约 145 MB",
        "OpenAI / Systran",
        ("多语言", "快速", "CPU", "Apple GPU"),
        {
            "cpu": _variant(
                "cpu",
                "Systran/faster-whisper-base",
                "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
                _f("config.json", 2_309, "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a"),
                _f("model.bin", 145_217_532, "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9"),
                _COMMON_TOKENIZER,
                _COMMON_VOCABULARY,
            ),
            "mlx": _variant(
                "mlx",
                "mlx-community/whisper-base-mlx",
                "1e3e249fb8d01c655324bd6841b1deadffd6d04c",
                _f("config.json", 262, "737220a6d958b3ad48e78f840fa991556266983c84ea2ca40e413389c62e4c2f"),
                _f("weights.npz", 143_724_204, "2f57d5f3ef473054c638961f90716f4ee415e8108de81313eccb2c5fd62eff0b"),
            ),
        },
    ),
    ModelDefinition(
        "small",
        "Whisper Small",
        "balanced",
        "日常均衡",
        "日常字幕的默认选择",
        "多语言",
        ("*",),
        "约 484 MB",
        "OpenAI / Systran",
        ("推荐", "多语言", "均衡", "CPU", "Apple GPU"),
        {
            "cpu": _variant(
                "cpu",
                "Systran/faster-whisper-small",
                "536b0662742c02347bc0e980a01041f333bce120",
                _f("config.json", 2_370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
                _f("model.bin", 483_546_902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
                _COMMON_TOKENIZER,
                _COMMON_VOCABULARY,
            ),
            "mlx": _variant(
                "mlx",
                "mlx-community/whisper-small-mlx",
                "45f3915923c7a79a5a5b5a7d909d39aeb0e5630e",
                _f("config.json", 266, "e8f58e638208af66d5d5d67801259dc7a12d199e971967a9f9d33a8e3635668e"),
                _f("weights.npz", 481_307_592, "55b6674c9b339702d486e2b1573839a66f8ec8f821ed2886993ef717a86b09f5"),
            ),
        },
    ),
    ModelDefinition(
        "medium",
        "Whisper Medium",
        "balanced",
        "日常均衡",
        "更复杂口音和嘈杂录音",
        "多语言",
        ("*",),
        "约 1.53 GB",
        "OpenAI / Systran",
        ("多语言", "高质量", "CPU", "Apple GPU"),
        {
            "cpu": _variant(
                "cpu",
                "Systran/faster-whisper-medium",
                "08e178d48790749d25932bbc082711ddcfdfbc4f",
                _f("config.json", 2_257, "3622a2ddc41ec0e0fd4e68c13c6830f03b90c38d89aaad184de02c8c642cf807"),
                _f("model.bin", 1_527_906_378, "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae"),
                _COMMON_TOKENIZER,
                _COMMON_VOCABULARY,
            ),
            "mlx": _variant(
                "mlx",
                "mlx-community/whisper-medium-mlx",
                "7fc08c4eac4c316526498f147dfdee6f6303f975",
                _f("config.json", 268, "3ff0b3f17a5a3a614327ffd835a3c8f6c78f39cbd39e84dbff4b0ae267c4d2e4"),
                _f("weights.npz", 1_524_924_912, "10b597c2bcb1bcc38b2d3d24cd4f0885f461a7cd70e8444d6ad5a763ece549ea"),
            ),
        },
    ),
    ModelDefinition(
        "large-v3",
        "Whisper Large V3",
        "performance",
        "高性能 / 高精度",
        "最高精度与复杂多语言内容",
        "多语言",
        ("*",),
        "约 3.09 GB",
        "OpenAI / Systran",
        ("多语言", "最高精度", "CPU", "Apple GPU"),
        {
            "cpu": _variant(
                "cpu",
                "Systran/faster-whisper-large-v3",
                "edaa852ec7e145841d8ffdb056a99866b5f0a478",
                _f("config.json", 2_394, "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9"),
                _f("model.bin", 3_087_284_237, "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"),
                _V3_PREPROCESSOR,
                _V3_TOKENIZER,
                _V3_VOCABULARY,
            ),
            "mlx": _variant(
                "mlx",
                "mlx-community/whisper-large-v3-mlx",
                "49e6aa286ad60c14352c404340ded53710378a11",
                _f("config.json", 269, "34982ce6ae286095000f82ae9583b3431639e8b092bf60c961f203745e6500e3"),
                _f("weights.npz", 3_083_520_416, "05ff791ce3630fae47e7c51004e9666204d786246ec07cac6110af768099b40d"),
            ),
        },
    ),
    ModelDefinition(
        "large-v3-turbo",
        "Whisper Large V3 Turbo",
        "performance",
        "高性能 / 高精度",
        "接近 Large V3 精度的高速转写",
        "多语言",
        ("*",),
        "约 1.62 GB",
        "OpenAI / Dropbox Dash",
        ("多语言", "高速", "CPU", "Apple GPU"),
        {
            "cpu": _variant(
                "cpu",
                "dropbox-dash/faster-whisper-large-v3-turbo",
                "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
                _f("config.json", 2_263, "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
                _f("model.bin", 1_617_884_929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
                _V3_PREPROCESSOR,
                _f("tokenizer.json", 2_710_337, "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
                _V3_VOCABULARY,
            ),
            "mlx": _variant(
                "mlx",
                "mlx-community/whisper-large-v3-turbo",
                "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
                _f("config.json", 268, "b34fc29e4e11e0a25e812775dd67f4dd16fc2c8eb43d28ae25ff7d660ecb6379"),
                _f("weights.safetensors", 1_613_977_612, "951ed3fc1203e6a62467abb2144a96ce7eafca8fa77e3704fdb8635ff3e7f8a6"),
            ),
        },
    ),
    ModelDefinition(
        "distil-large-v3",
        "Distil-Whisper Large V3",
        "english",
        "英语专用",
        "英语内容的高质量高速转写",
        "仅英语；其他语言请使用 Whisper Small",
        ("en",),
        "约 1.51 GB",
        "Hugging Face / Systran",
        ("英语专用", "高速", "CPU", "Apple GPU"),
        {
            "cpu": _variant(
                "cpu",
                "Systran/faster-distil-whisper-large-v3",
                "c3058b475261292e64a0412df1d2681c06260fab",
                _f("config.json", 2_690, "90c55f775cc4e0bb17293d0bf12f96557a486f20dea886fabd8e6075a3588b21"),
                _f("model.bin", 1_512_927_867, "b79368e19b6623813609431a6e5ee309a71506701ebc49fd7820e692dec7c5f5"),
                _V3_PREPROCESSOR,
                _V3_TOKENIZER,
                _V3_VOCABULARY,
            ),
            "mlx": _variant(
                "mlx",
                "mlx-community/distil-whisper-large-v3",
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
        "qwen3-asr-0.6b-int8-2026-03-25",
        "Qwen3-ASR 0.6B",
        "specialized",
        "专业场景",
        "多语言、中文方言、歌词和快速语流",
        "30 种语言及 22 种中文方言",
        ("*",),
        "Apple GPU 下载约 1.76 GB",
        "Qwen / MLX Qwen3-ASR",
        ("多语言", "方言", "Apple GPU", "MLX"),
        {
            "mlx": _variant(
                "mlx",
                "Qwen/Qwen3-ASR-0.6B",
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
        "qwen3-asr-1.7b",
        "Qwen3-ASR 1.7B",
        "specialized",
        "专业场景",
        "Apple Silicon 上的高精度多语言与中文方言转写",
        "30 种语言及 22 种中文方言",
        ("*",),
        "下载约 4.38 GB",
        "Qwen / MLX Qwen3-ASR",
        ("高精度", "多语言", "方言", "Apple GPU", "MLX"),
        {
            "mlx": _variant(
                "mlx",
                "Qwen/Qwen3-ASR-1.7B",
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


def facade_attr(name: str, default: Any) -> Any:
    pkg = sys.modules.get(__package__ or "")
    return getattr(pkg, name, default) if pkg is not None else default
