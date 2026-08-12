#!/usr/bin/env python3
"""Fail a release if any pinned transcription-model source has drifted."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.model_catalog import (  # noqa: E402
    QWEN_ASR_MODEL_CATALOG,
    WHISPER_MODEL_CATALOG,
)
from app.services.parakeet_transcriber import (  # noqa: E402
    PARAKEET_ARCHIVE_BYTES,
    PARAKEET_ARCHIVE_SHA256,
    PARAKEET_ARCHIVE_URL,
    SILERO_VAD_BYTES,
    SILERO_VAD_SHA256,
    SILERO_VAD_URL,
)
from app.services.sherpa_catalog import MANAGED_SHERPA_MODELS  # noqa: E402
from app.version import product_user_agent  # noqa: E402


# Git blob IDs protect regular repository files. LFS/Xet files expose their
# SHA-256 directly and are compared with CatalogFile.sha256 below.
EXPECTED_SOURCE_OIDS = {
    ("tiny", "cpu"): {
        "config.json": "3baa18e2b321a2f489614607852a729fcd516480",
        "tokenizer.json": "7818adb6de9fa3064d3ff81226fdd675be1f6344",
        "vocabulary.txt": "c9074644d9d1205686f16d411564729461324b75",
    },
    ("tiny", "mlx"): {"config.json": "a5f93cd58e229a777d10e77c753693018cd25264"},
    ("base", "cpu"): {
        "config.json": "867cf1a0fece1394e01d55e287ba2f09a577c046",
        "tokenizer.json": "7818adb6de9fa3064d3ff81226fdd675be1f6344",
        "vocabulary.txt": "c9074644d9d1205686f16d411564729461324b75",
    },
    ("base", "mlx"): {"config.json": "3860125a7f82a556e43e28eda7147a51c802ba5a"},
    ("small", "cpu"): {
        "config.json": "e5047537059bd8f182d9ca64c470201585015187",
        "tokenizer.json": "7818adb6de9fa3064d3ff81226fdd675be1f6344",
        "vocabulary.txt": "c9074644d9d1205686f16d411564729461324b75",
    },
    ("small", "mlx"): {"config.json": "df4c65c442a30e50034ee63422b31bde60c3d407"},
    ("medium", "cpu"): {
        "config.json": "242aa06a480a7b5509375c645097e87af5136774",
        "tokenizer.json": "7818adb6de9fa3064d3ff81226fdd675be1f6344",
        "vocabulary.txt": "c9074644d9d1205686f16d411564729461324b75",
    },
    ("medium", "mlx"): {"config.json": "96da6a75123b7642a14599c6afb516b0c1562237"},
    ("large-v3", "cpu"): {
        "config.json": "75336feae814999bae6ccccdecf177639ffc6f9d",
        "preprocessor_config.json": "931c77a740890c46365c7ae0c9d350ba3cca908f",
        "tokenizer.json": "3a5e2ba63acdcac9a19ba56cf9bd27f185bfff61",
        "vocabulary.json": "0adcd01e7c237205d593b707e66dd5d7bc785d2d",
    },
    ("large-v3", "mlx"): {"config.json": "2c626df3c540bdc0287b11725441d10e1af73680"},
    ("large-v3-turbo", "cpu"): {
        "config.json": "0351d1d6870005e865747b781b5d7c23ea0459cd",
        "preprocessor_config.json": "931c77a740890c46365c7ae0c9d350ba3cca908f",
        "tokenizer.json": "17456db595adc78a973f97d69d8cb50bc87c0b1c",
        "vocabulary.json": "0adcd01e7c237205d593b707e66dd5d7bc785d2d",
    },
    ("large-v3-turbo", "mlx"): {"config.json": "6ac9a52a28f70a2e5681c250a470eca6e9c8cc3e"},
    ("distil-large-v3", "cpu"): {
        "config.json": "499468d516090aee836412556ca759be4e9e73f7",
        "preprocessor_config.json": "931c77a740890c46365c7ae0c9d350ba3cca908f",
        "tokenizer.json": "3a5e2ba63acdcac9a19ba56cf9bd27f185bfff61",
        "vocabulary.json": "0adcd01e7c237205d593b707e66dd5d7bc785d2d",
    },
    ("distil-large-v3", "mlx"): {"config.json": "9f26c8800f97a3fec6a86124d25db3e0a4c79a57"},
    ("qwen3-asr-0.6b-int8-2026-03-25", "mlx"): {
        "chat_template.json": "c44736493efd71ec96218cc626904698cdb13235",
        "config.json": "8113d596dec2d1a2ef4552edfe005ee84d16d9ec",
        "generation_config.json": "7382a4d347c0a865b76bb1b8277f66a5ac312854",
        "merges.txt": "31349551d90c7606f325fe0f11bbb8bd5fa0d7c7",
        "preprocessor_config.json": "8f7f07346466d5d494ec0d4969d1c3d0190eed72",
        "tokenizer_config.json": "b93109843922a40c6654c5449d3bf95372267c66",
        "vocab.json": "4783fe10ac3adce15ac8f358ef5462739852c569",
    },
    ("qwen3-asr-1.7b", "mlx"): {
        "chat_template.json": "c44736493efd71ec96218cc626904698cdb13235",
        "config.json": "2bc16c9d4ca08963715cfb94d879799b9adbd0e9",
        "generation_config.json": "7382a4d347c0a865b76bb1b8277f66a5ac312854",
        "merges.txt": "31349551d90c7606f325fe0f11bbb8bd5fa0d7c7",
        "model.safetensors.index.json": "1048a4eb4f21fef9aea06d8568a784b2b5595689",
        "preprocessor_config.json": "8f7f07346466d5d494ec0d4969d1c3d0190eed72",
        "tokenizer_config.json": "b93109843922a40c6654c5449d3bf95372267c66",
        "vocab.json": "4783fe10ac3adce15ac8f358ef5462739852c569",
    },
}


def get_json(url: str) -> dict | list:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json, application/json",
            "User-Agent": product_user_agent("release-source-verifier"),
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def verify_hugging_face() -> int:
    checked = 0
    for definition in (*WHISPER_MODEL_CATALOG, *QWEN_ASR_MODEL_CATALOG):
        for runtime, variant in definition.variants.items():
            url = (
                f"https://huggingface.co/api/models/{variant.repository}/tree/"
                f"{variant.revision}?recursive=true&expand=false"
            )
            entries = get_json(url)
            if not isinstance(entries, list):
                raise RuntimeError(f"无效的 Hugging Face 元数据：{variant.repository}")
            by_name = {
                item["path"]: item for item in entries
                if item.get("type") == "file"
            }
            regular_oids = EXPECTED_SOURCE_OIDS[(definition.id, runtime)]
            for expected in variant.files:
                actual = by_name.get(expected.name)
                if not actual:
                    raise RuntimeError(
                        f"来源漂移：{variant.repository}@{variant.revision} 缺少 {expected.name}"
                    )
                if actual.get("size") != expected.size:
                    raise RuntimeError(
                        f"来源漂移：{variant.repository}/{expected.name} 大小已变化"
                    )
                lfs = actual.get("lfs") or {}
                if lfs:
                    if lfs.get("oid") != expected.sha256:
                        raise RuntimeError(
                            f"来源漂移：{variant.repository}/{expected.name} SHA-256 已变化"
                        )
                elif actual.get("oid") != regular_oids.get(expected.name):
                    raise RuntimeError(
                        f"来源漂移：{variant.repository}/{expected.name} Git blob 已变化"
                    )
                if len(expected.sha256) != 64:
                    raise RuntimeError(f"发布清单缺少 SHA-256：{definition.id}/{expected.name}")
                checked += 1
    return checked


def verify_github() -> int:
    release = get_json(
        "https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/asr-models"
    )
    if not isinstance(release, dict):
        raise RuntimeError("无效的 GitHub 发布元数据")
    assets = {asset["name"]: asset for asset in release.get("assets", [])}
    expected = (
        (
            Path(urllib.parse.urlparse(PARAKEET_ARCHIVE_URL).path).name,
            PARAKEET_ARCHIVE_BYTES,
            PARAKEET_ARCHIVE_SHA256,
        ),
        (
            Path(urllib.parse.urlparse(SILERO_VAD_URL).path).name,
            SILERO_VAD_BYTES,
            SILERO_VAD_SHA256,
        ),
    )
    for name, size, sha256 in expected:
        asset = assets.get(name)
        if not asset:
            raise RuntimeError(f"来源漂移：GitHub 发布页缺少 {name}")
        if asset.get("size") != size:
            raise RuntimeError(f"来源漂移：{name} 大小已变化")
        if asset.get("digest") != f"sha256:{sha256}":
            raise RuntimeError(f"来源漂移：{name} SHA-256 已变化")
    for definition in MANAGED_SHERPA_MODELS:
        asset = assets.get(definition.archive_name)
        if not asset:
            raise RuntimeError(
                f"来源漂移：GitHub 发布页缺少 {definition.archive_name}"
            )
        if asset.get("id") != definition.asset_id:
            raise RuntimeError(
                f"来源漂移：{definition.archive_name} 资源 ID 已变化"
            )
        if asset.get("size") != definition.archive_size:
            raise RuntimeError(
                f"来源漂移：{definition.archive_name} 大小已变化"
            )
        if asset.get("updated_at") != definition.asset_updated_at:
            raise RuntimeError(
                f"来源漂移：{definition.archive_name} 更新时间已变化"
            )
        # GitHub does not backfill ``digest`` for many older release assets.
        # Runtime downloads still enforce the pinned full archive SHA-256.
        if len(definition.archive_sha256) != 64:
            raise RuntimeError(
                f"发布清单缺少压缩包 SHA-256：{definition.archive_name}"
            )
        for item in definition.files:
            if len(item.sha256) != 64 or item.size <= 0:
                raise RuntimeError(
                    f"发布清单缺少逐文件身份：{definition.id}/{item.name}"
                )
    return len(expected) + len(MANAGED_SHERPA_MODELS)


def main() -> int:
    try:
        hf_count = verify_hugging_face()
        github_count = verify_github()
    except (RuntimeError, urllib.error.URLError, TimeoutError) as exc:
        print(f"模型来源验证失败：{exc}", file=sys.stderr)
        return 1
    print(f"模型来源验证通过：Hugging Face {hf_count} 个文件，GitHub {github_count} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
