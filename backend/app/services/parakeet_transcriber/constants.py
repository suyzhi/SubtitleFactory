"""Parakeet constants, pinned manifests, data models, and facade lookup helper."""

from __future__ import annotations

import logging
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("app.services.parakeet_transcriber")

PARAKEET_MODEL_ID = "parakeet-tdt-0.6b-v3-coreml"
PARAKEET_ONNX_MODEL_ID = "parakeet-tdt-0.6b-v3-int8"
PARAKEET_MODEL_DIR_NAME = f"sherpa-onnx-nemo-{PARAKEET_ONNX_MODEL_ID}"
PARAKEET_DISPLAY_NAME = "Parakeet TDT 0.6B v3 (sherpa-onnx int8)"
PARAKEET_COREML_DISPLAY_NAME = "Parakeet TDT 0.6B v3 (Core ML)"
PARAKEET_COREML_MODEL_ENV = "PARAKEET_COREML_MODEL_DIR"
PARAKEET_COREML_CLI_ENV = "PARAKEET_COREML_CLI"
PARAKEET_ARCHIVE_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    f"{PARAKEET_MODEL_DIR_NAME}.tar.bz2"
)
SILERO_VAD_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "silero_vad.onnx"
)

# Exact release-asset byte sizes. Besides making progress deterministic, this
# prevents a proxy/login HTML page or a truncated response from becoming cache.
PARAKEET_ARCHIVE_BYTES = 487_211_443
SILERO_VAD_BYTES = 643_854
PARAKEET_EXTRACTED_ESTIMATE_BYTES = 671_000_000
PARAKEET_ARCHIVE_SHA256 = "1495e91b59d7fc53ddca91f3431758ec99f8fa253fdb0e1e851be0f72b14b0f9"
SILERO_VAD_SHA256 = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"

# Core ML conversion of nvidia/parakeet-tdt-0.6b-v3 (CC-BY-4.0) published by
# FluidInference for FluidAudio. Pinned to one revision; only the entries the
# runner needs (~461 MiB of the 3.5 GB repository) are downloaded, each checked
# against its exact size and SHA-256.
PARAKEET_COREML_REPOSITORY = "FluidInference/parakeet-tdt-0.6b-v3-coreml"
PARAKEET_COREML_REVISION = "7dd20fe6b1797d35f5e3307e8b1732d9a178edfe"
PARAKEET_COREML_BASE_URL = (
    f"https://huggingface.co/{PARAKEET_COREML_REPOSITORY}/resolve/{PARAKEET_COREML_REVISION}/"
)
PARAKEET_COREML_SOURCE_URL = (
    f"https://huggingface.co/{PARAKEET_COREML_REPOSITORY}/tree/{PARAKEET_COREML_REVISION}"
)
PARAKEET_COREML_CLI_NAME = "parakeet-coreml"
PARAKEET_COREML_FILES: tuple[tuple[str, int, str], ...] = (
    ("Decoder.mlmodelc/analytics/coremldata.bin", 243, "4238c4e81ecd0dc94bd7dfbb60f7e2cc824107c1ffe0387b8607b72833dba350"),
    ("Decoder.mlmodelc/coremldata.bin", 554, "18647af085d87bd8f3121c8a9b4d4564c1ede038dab63d295b4e745cf2d7fb99"),
    ("Decoder.mlmodelc/metadata.json", 3427, "a39e93cd8371b8ded92635c7804fcd0590f0d1dd9415c6d19a0484be073077d9"),
    ("Decoder.mlmodelc/model.mil", 13110, "ef2a0a281695398a62fde86ac269c68f73d5b578d7ed3b31f2ba91a2d1ea1f35"),
    ("Decoder.mlmodelc/weights/weight.bin", 23604992, "48adf0f0d47c406c8253d4f7fef967436a39da14f5a65e66d5a4b407be355d41"),
    ("Encoder.mlmodelc/analytics/coremldata.bin", 243, "42e638870d73f26b332918a3496ce36793fbb413a81cbd3d16ba01328637a105"),
    ("Encoder.mlmodelc/coremldata.bin", 485, "d48034a167a82e88fc3df64f60af963ab3983538271175b8319e7d5720a0fb86"),
    ("Encoder.mlmodelc/metadata.json", 2921, "da24da9cca943fb29d7fa8e376d57fca7cb3aa08ca51b956b0b0e56813f087e9"),
    ("Encoder.mlmodelc/model.mil", 959769, "ed7b19156ca29fa7dfd6891deb9fda4b0e8893f68597c985d135736546a43808"),
    ("Encoder.mlmodelc/weights/weight.bin", 445187200, "e2020f323703477a5b21d7c2d282c403e371afb5962e79877e3033e73ba6f421"),
    ("JointDecision.mlmodelc/analytics/coremldata.bin", 243, "bc69ef031ed427e888b1f3889d13eb373655edd5ac9927de20b5dae281b636b7"),
    ("JointDecision.mlmodelc/coremldata.bin", 534, "f56ded0404498e666ffcd84dda0c393924fc3581345ad03e41429ff560cb97b6"),
    ("JointDecision.mlmodelc/metadata.json", 2936, "3044edab5e4ee331d37cef7100074653c944a0e58184ab618aab183a0e0707bc"),
    ("JointDecision.mlmodelc/model.mil", 9723, "2cb084d7e0dc86ad3ddaa53a9631cdd5d97f19839218845b0e65ca065a4d1a5e"),
    ("JointDecision.mlmodelc/weights/weight.bin", 12642764, "4e0e63d840032f7f07ddb1d64446051166281e5491bf22da8a945c41f6eedb3e"),
    ("Preprocessor.mlmodelc/analytics/coremldata.bin", 243, "c9beeb989c8d66f8be11df59bc6df277ec76cee404f6865b46243835ef562f6d"),
    ("Preprocessor.mlmodelc/coremldata.bin", 486, "dbde3f2300842c1fd51ef3ff948a0bcffe65ffd2dca10707f2509f32c1d65b1d"),
    ("Preprocessor.mlmodelc/metadata.json", 2841, "2a98699e22d279dd37fa1d238aeb1c6db1df0d6fad687775324157689d8f3acf"),
    ("Preprocessor.mlmodelc/model.mil", 28181, "4b8518a956450fec57f06c2a21bdffc26973f7f1fa6842fb38fe917f896b6b93"),
    ("Preprocessor.mlmodelc/weights/weight.bin", 491072, "129b76e3aeafa8afa3ea76d995b964b145fe83700d579f6ff42c4c38fa0968ea"),
    ("config.json", 475, "97f19ecccd0fdc730d76fb918090fa8bc64bce8ea4ad43715d5e9c2c7350db66"),
    ("parakeet_v3_vocab.json", 151122, "7ec60e05f1b24480736ec0eed40900f4626bce1fa9a60fd700ec7e2a59198735"),
    ("parakeet_vocab.json", 151122, "7ec60e05f1b24480736ec0eed40900f4626bce1fa9a60fd700ec7e2a59198735"),
)
PARAKEET_COREML_DOWNLOAD_BYTES = sum(size for _, size, _ in PARAKEET_COREML_FILES)

# Sizes are deliberately lower bounds: they catch incomplete extraction while
# allowing an upstream-compatible re-export to remain usable.
PARAKEET_REQUIRED_FILES = {
    "encoder.int8.onnx": 600_000_000,
    "decoder.int8.onnx": 10_000_000,
    "joiner.int8.onnx": 5_000_000,
    "tokens.txt": 50_000,
}

PARAKEET_SUPPORTED_LANGUAGES = frozenset(
    {
        "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de",
        "el", "hu", "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk",
        "sl", "es", "sv", "ru", "uk",
    }
)

_DOWNLOAD_LOCK = threading.Lock()
_PUNCTUATION_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)
_TEXT_PROGRESS = re.compile(r"(?<!\d)(100|\d{1,2})\s*%")
_SENTENCE_ENDINGS = frozenset(".!?。！？")
_COREML_REQUIRED_MODEL_ENTRIES = (
    "Encoder.mlmodelc",
    "Decoder.mlmodelc",
    "Preprocessor.mlmodelc",
    "JointDecision.mlmodelc",
    "parakeet_v3_vocab.json",
)


class ParakeetModelError(RuntimeError):
    def __init__(self, message: str, error_code: str, suggestion: str):
        super().__init__(message)
        self.error_code = error_code
        self.recoverable = True
        self.available_actions = ["retry", "repair"]
        self.suggestion = suggestion


@dataclass(frozen=True)
class CoreMLRuntime:
    model_dir: Path
    cli_path: Path
    source: str = "external_detected"


@dataclass(frozen=True)
class ParakeetAssets:
    model_dir: Path
    encoder: Path
    decoder: Path
    joiner: Path
    tokens: Path
    vad: Path


@dataclass(frozen=True)
class ParakeetSegment:
    start: float
    end: float
    text: str
    timings: tuple[dict, ...] = ()


@dataclass(frozen=True)
class ParakeetSession:
    segments: Iterator[ParakeetSegment]
    audio_duration: float
    detected_language: str
    device: str = "cpu"
    compute_type: str = "int8 ONNX"
    model_label: str = PARAKEET_DISPLAY_NAME
    progress_start: float = 25.0


def facade_attr(name: str, default: Any) -> Any:
    pkg = sys.modules.get(__package__ or "")
    return getattr(pkg, name, default) if pkg is not None else default
