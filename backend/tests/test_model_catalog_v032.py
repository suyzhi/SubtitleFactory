import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
if "SUBTITLE_FACTORY_DATA_DIR" not in os.environ:
    os.environ["SUBTITLE_FACTORY_DATA_DIR"] = tempfile.mkdtemp(
        prefix="subtitle-factory-v032-model-tests-",
    )

from fastapi.testclient import TestClient

from app.main import app
from app.security import API_TOKEN
from app.services import model_catalog
from app.services.model_catalog import (
    CatalogFile,
    ModelDownloadError,
    ModelVariant,
    QWEN_ASR_CATALOG_BY_ID,
    WHISPER_CATALOG_BY_ID,
)
from app.services.parakeet_transcriber import (
    PARAKEET_ARCHIVE_SHA256,
    SILERO_VAD_SHA256,
)
from app.services.transcriber import resolve_transcription_model
from app.utils.task_manager import TaskCancelled, task_manager


EXPECTED_REPOSITORIES = {
    "tiny": (
        ("Systran/faster-whisper-tiny", "d90ca5fe260221311c53c58e660288d3deb8d356"),
        ("mlx-community/whisper-tiny-mlx", "6caf9c55601caafbe6508a8b0d216bdf4783c4e8"),
    ),
    "base": (
        ("Systran/faster-whisper-base", "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"),
        ("mlx-community/whisper-base-mlx", "1e3e249fb8d01c655324bd6841b1deadffd6d04c"),
    ),
    "small": (
        ("Systran/faster-whisper-small", "536b0662742c02347bc0e980a01041f333bce120"),
        ("mlx-community/whisper-small-mlx", "45f3915923c7a79a5a5b5a7d909d39aeb0e5630e"),
    ),
    "medium": (
        ("Systran/faster-whisper-medium", "08e178d48790749d25932bbc082711ddcfdfbc4f"),
        ("mlx-community/whisper-medium-mlx", "7fc08c4eac4c316526498f147dfdee6f6303f975"),
    ),
    "large-v3": (
        ("Systran/faster-whisper-large-v3", "edaa852ec7e145841d8ffdb056a99866b5f0a478"),
        ("mlx-community/whisper-large-v3-mlx", "49e6aa286ad60c14352c404340ded53710378a11"),
    ),
    "large-v3-turbo": (
        ("dropbox-dash/faster-whisper-large-v3-turbo", "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"),
        ("mlx-community/whisper-large-v3-turbo", "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"),
    ),
    "distil-large-v3": (
        ("Systran/faster-distil-whisper-large-v3", "c3058b475261292e64a0412df1d2681c06260fab"),
        ("mlx-community/distil-whisper-large-v3", "e1c3c155644be59f8b477c0186719442f7e3fbb0"),
    ),
}


class CatalogContractTests(unittest.TestCase):
    def test_api_exposes_exactly_twenty_nine_grouped_release_models(self):
        response = TestClient(app).get(
            "/api/transcription/models",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        release_models = [
            item for item in payload["models"]
            if item.get("category_id") in payload["category_order"]
        ]
        self.assertEqual(len(release_models), 29)
        self.assertEqual(payload["recommended_model"], "small")
        self.assertEqual(
            payload["category_order"],
            [
                "lightweight", "balanced", "performance", "multilingual",
                "chinese", "dialects", "english", "east_asian", "european",
                "specialized", "parakeet", "cloud",
            ],
        )
        for item in release_models:
            self.assertTrue(item["purpose"])
            self.assertTrue(item["language_description"])
            self.assertTrue(item["publisher"])
            for field in (
                "family", "scenarios", "strengths", "limitations",
                "speed_tier", "accuracy_tier", "memory_tier",
                "timestamp_mode", "punctuation_mode", "installed_bytes",
                "license",
            ):
                self.assertIn(field, item)
            for runtime in item["runtimes"]:
                self.assertIn("model_ready", runtime)
                self.assertIn("download_required", runtime)

    def test_every_whisper_variant_is_pinned_with_required_sha256(self):
        self.assertEqual(set(WHISPER_CATALOG_BY_ID), set(EXPECTED_REPOSITORIES))
        for model_id, expected in EXPECTED_REPOSITORIES.items():
            definition = WHISPER_CATALOG_BY_ID[model_id]
            for runtime, pair in zip(("cpu", "mlx"), expected):
                variant = definition.variants[runtime]
                self.assertEqual((variant.repository, variant.revision), pair)
                self.assertTrue(variant.files)
                self.assertTrue(all(item.size > 0 for item in variant.files))
                self.assertTrue(all(len(item.sha256) == 64 for item in variant.files))

    def test_qwen_apple_gpu_variants_pin_official_revisions_and_files(self):
        expected = {
            "qwen3-asr-0.6b-int8-2026-03-25": (
                "Qwen/Qwen3-ASR-0.6B", "5eb144179a02acc5e5ba31e748d22b0cf3e303b0", 8,
            ),
            "qwen3-asr-1.7b": (
                "Qwen/Qwen3-ASR-1.7B", "7278e1e70fe206f11671096ffdd38061171dd6e5", 10,
            ),
        }
        self.assertEqual(set(QWEN_ASR_CATALOG_BY_ID), set(expected))
        for model_id, (repository, revision, file_count) in expected.items():
            variant = QWEN_ASR_CATALOG_BY_ID[model_id].variants["mlx"]
            self.assertEqual((variant.repository, variant.revision), (repository, revision))
            self.assertEqual(len(variant.files), file_count)
            self.assertTrue(all(item.size > 0 and len(item.sha256) == 64 for item in variant.files))

    def test_language_restrictions_fall_back_visibly_to_small(self):
        distil = resolve_transcription_model("distil-large-v3", "zh")
        parakeet = resolve_transcription_model("parakeet-tdt-0.6b-v3-int8", "zh")
        self.assertEqual(distil.model_id, "small")
        self.assertIn("仅支持英语", distil.fallback_reason)
        self.assertEqual(parakeet.model_id, "small")
        self.assertIn("不支持", parakeet.fallback_reason)

    def test_parakeet_release_hashes_are_real_sha256_values(self):
        self.assertEqual(
            PARAKEET_ARCHIVE_SHA256,
            "5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf",
        )
        self.assertEqual(
            SILERO_VAD_SHA256,
            "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
        )


class VerifiedDownloaderTests(unittest.TestCase):
    def setUp(self):
        self.content = b"verified model bytes"
        self.file = CatalogFile(
            "model.bin", len(self.content), hashlib.sha256(self.content).hexdigest(),
        )
        self.variant = ModelVariant(
            "cpu", "example/model", "a" * 40, (self.file,),
        )

    def test_http_range_resume_finishes_and_verifies(self):
        class Response:
            status = 206

            def read(self, _size):
                value, self.remaining = self.remaining, b""
                return value

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / self.file.name
            destination.with_name(f"{destination.name}.part").write_bytes(self.content[:7])
            response = Response()
            response.remaining = self.content[7:]
            progress = []
            with patch.object(model_catalog.urllib.request, "urlopen", return_value=response):
                model_catalog._download_file(
                    self.variant, self.file, destination, lambda: None,
                    lambda count, resumed: progress.append((count, resumed)),
                )
            self.assertEqual(destination.read_bytes(), self.content)
            self.assertTrue(any(resumed for _, resumed in progress))

    def test_hash_failure_never_promotes_bad_file(self):
        class Response:
            status = 200

            def __init__(self):
                self.remaining = b"x" * len(self_content)

            def read(self, _size):
                value, self.remaining = self.remaining, b""
                return value

            def close(self):
                pass

        self_content = self.content
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / self.file.name
            with patch.object(model_catalog.urllib.request, "urlopen", return_value=Response()):
                with self.assertRaisesRegex(ModelDownloadError, "校验失败"):
                    model_catalog._download_file(
                        self.variant, self.file, destination, lambda: None,
                        lambda _count, _resumed: None,
                    )
            self.assertFalse(destination.exists())

    def test_cancel_keeps_partial_for_next_resume(self):
        class Response:
            status = 200

            def __init__(self):
                self.calls = 0

            def read(self, _size):
                self.calls += 1
                return self_content[:5] if self.calls == 1 else b""

            def close(self):
                pass

        self_content = self.content
        checks = 0

        def checkpoint():
            nonlocal checks
            checks += 1
            if checks >= 3:
                raise TaskCancelled()

        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / self.file.name
            with patch.object(model_catalog.urllib.request, "urlopen", return_value=Response()):
                with self.assertRaises(TaskCancelled):
                    model_catalog._download_file(
                        self.variant, self.file, destination, checkpoint,
                        lambda _count, _resumed: None,
                    )
            self.assertTrue(destination.with_name(f"{destination.name}.part").exists())

    def test_failed_repair_preserves_current_live_model(self):
        with tempfile.TemporaryDirectory() as folder:
            final = Path(folder) / "tiny"
            final.mkdir()
            (final / "model.bin").write_bytes(b"old working model")
            task_id = task_manager.create_task(None, "prepare_model")
            with patch.object(model_catalog, "variant_for", return_value=self.variant), patch.object(
                model_catalog, "managed_model_dir", return_value=final,
            ), patch.object(
                model_catalog, "_download_file",
                side_effect=ModelDownloadError(
                    "network failed", "MODEL_NETWORK_UNREACHABLE",
                    suggestion="retry",
                ),
            ), patch.object(
                model_catalog.shutil, "disk_usage",
                return_value=SimpleNamespace(free=10_000_000_000),
            ):
                with self.assertRaises(ModelDownloadError):
                    model_catalog.prepare_whisper_model(
                        task_id, "tiny", "cpu", repair=True,
                    )
            self.assertEqual((final / "model.bin").read_bytes(), b"old working model")

    def test_disk_preflight_uses_stable_error_code(self):
        with tempfile.TemporaryDirectory() as folder:
            final = Path(folder) / "tiny"
            task_id = task_manager.create_task(None, "prepare_model")
            with patch.object(model_catalog, "variant_for", return_value=self.variant), patch.object(
                model_catalog, "managed_model_dir", return_value=final,
            ), patch.object(
                model_catalog.shutil, "disk_usage",
                return_value=SimpleNamespace(free=0),
            ):
                with self.assertRaises(ModelDownloadError) as raised:
                    model_catalog.prepare_whisper_model(
                        task_id, "tiny", "cpu", repair=True,
                    )
            self.assertEqual(raised.exception.error_code, "MODEL_DISK_SPACE_INSUFFICIENT")


if __name__ == "__main__":
    unittest.main()
