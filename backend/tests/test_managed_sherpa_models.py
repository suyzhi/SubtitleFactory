import hashlib
import io
import os
import sys
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault(
    "SUBTITLE_FACTORY_DATA_DIR",
    tempfile.mkdtemp(prefix="subtitle-factory-managed-model-tests-"),
)

from app.services import managed_sherpa
from app.services.managed_sherpa import (
    ManagedModelError,
    _create_recognizer,
    _download_verified_file,
    _safe_extract_required,
    _timings_from_result,
    recommended_ready_model,
)
from app.services.sherpa_catalog import (
    MANAGED_SHERPA_BY_ID,
    MANAGED_SHERPA_MODELS,
    ManagedModelFile,
)
from app.services.transcriber import SUPPORTED_TRANSCRIPTION_MODELS
from app.utils.task_manager import TaskCancelled


class ManagedCatalogTests(unittest.TestCase):
    def test_catalog_has_eighteen_unique_pinned_models_and_twenty_nine_total(self):
        self.assertEqual(len(MANAGED_SHERPA_MODELS), 18)
        self.assertEqual(len(MANAGED_SHERPA_BY_ID), 18)
        self.assertEqual(
            len([item for item in SUPPORTED_TRANSCRIPTION_MODELS if item != "custom"]),
            29,
        )
        for definition in MANAGED_SHERPA_MODELS:
            self.assertEqual(definition.id, definition.id.lower())
            self.assertTrue(definition.package.startswith("sherpa-onnx-"))
            self.assertGreater(definition.asset_id, 0)
            self.assertTrue(definition.asset_updated_at.endswith("Z"))
            self.assertEqual(len(definition.archive_sha256), 64)
            self.assertGreater(definition.archive_size, 0)
            self.assertTrue(definition.files)
            self.assertTrue(all(item.size > 0 for item in definition.files))
            self.assertTrue(all(len(item.sha256) == 64 for item in definition.files))
            self.assertIn(definition.timestamp_mode, {"token", "segment"})
            self.assertTrue(definition.license)

    def test_every_managed_model_exposes_its_verified_apple_gpu_runtime(self):
        for definition in MANAGED_SHERPA_MODELS:
            expected = "mlx" if definition.adapter == "qwen3" else "coreml"
            self.assertIn(expected, definition.runtimes, definition.id)

    def test_professional_models_are_never_automatic(self):
        manual_only = {
            "qwen3-asr-0.6b-int8-2026-03-25",
            "telespeech-ctc-int8-zh-2024-06-04",
            "paraformer-zh-int8-2025-10-07",
            "sense-voice-zh-en-ja-ko-yue-int8-2025-09-09",
            "medasr-ctc-en-int8-2025-12-25",
        }
        self.assertTrue(all(not MANAGED_SHERPA_BY_ID[item].automatic for item in manual_only))

    def test_automatic_selection_uses_ready_specialist_then_multilingual(self):
        ready = {
            "paraformer-zh-2023-09-14",
            "wenetspeech-yue-u2pp-conformer-ctc-zh-en-cantonese-int8-2025-09-10",
            "dolphin-base-ctc-multi-lang-int8-2025-04-02",
            "qwen3-asr-0.6b-int8-2026-03-25",
        }
        with patch.object(
            managed_sherpa,
            "managed_model_ready",
            side_effect=lambda model_id: model_id in ready,
        ):
            self.assertEqual(
                recommended_ready_model("zh"),
                "paraformer-zh-2023-09-14",
            )
            self.assertEqual(
                recommended_ready_model("yue"),
                "wenetspeech-yue-u2pp-conformer-ctc-zh-en-cantonese-int8-2025-09-10",
            )
            self.assertEqual(
                recommended_ready_model("fr"),
                "dolphin-base-ctc-multi-lang-int8-2025-04-02",
            )
            self.assertIsNone(recommended_ready_model("auto"))


class ManagedDownloadTests(unittest.TestCase):
    def test_range_resume_is_verified_before_promotion(self):
        content = b"verified managed model"

        class Response:
            status = 206

            def __init__(self):
                self.remaining = content[8:]

            def read(self, _size):
                chunk, self.remaining = self.remaining, b""
                return chunk

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "model.onnx"
            destination.with_name("model.onnx.part").write_bytes(content[:8])
            reports = []
            with patch.object(
                managed_sherpa.urllib.request,
                "urlopen",
                return_value=Response(),
            ):
                _download_verified_file(
                    url="https://example.invalid/model.onnx",
                    destination=destination,
                    expected_size=len(content),
                    expected_sha256=hashlib.sha256(content).hexdigest(),
                    checkpoint=lambda: None,
                    report=lambda done, total, resumed: reports.append(
                        (done, total, resumed)
                    ),
                )
            self.assertEqual(destination.read_bytes(), content)
            self.assertTrue(any(item[2] for item in reports))

    def test_cancellation_preserves_partial_for_future_resume(self):
        class Response:
            status = 200

            def read(self, _size):
                return b"partial"

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "model.onnx"
            checkpoints = iter((None, TaskCancelled()))

            def cancel_after_request():
                value = next(checkpoints)
                if isinstance(value, Exception):
                    raise value

            with patch.object(
                managed_sherpa.urllib.request,
                "urlopen",
                return_value=Response(),
            ):
                with self.assertRaises(TaskCancelled):
                    _download_verified_file(
                        url="https://example.invalid/model.onnx",
                        destination=destination,
                        expected_size=20,
                        expected_sha256="0" * 64,
                        checkpoint=cancel_after_request,
                        report=lambda *_args: None,
                    )
            self.assertTrue(destination.with_name("model.onnx.part").exists())
            self.assertFalse(destination.exists())

    def test_safe_extract_rejects_path_traversal(self):
        definition = MANAGED_SHERPA_MODELS[0]
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "bad.tar.bz2"
            with tarfile.open(archive, "w:bz2") as bundle:
                member = tarfile.TarInfo(f"{definition.package}/../../outside")
                member.size = 1
                bundle.addfile(member, io.BytesIO(b"x"))
            with self.assertRaisesRegex(ManagedModelError, "越界路径"):
                _safe_extract_required(
                    archive,
                    Path(folder) / "out",
                    definition,
                    lambda: None,
                    lambda *_args: None,
                )

    def test_safe_extract_installs_only_declared_files(self):
        content = b"runtime"
        definition = replace(
            MANAGED_SHERPA_MODELS[0],
            package="fixture",
            files=(
                ManagedModelFile(
                    "model.onnx",
                    len(content),
                    hashlib.sha256(content).hexdigest(),
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "good.tar.bz2"
            with tarfile.open(archive, "w:bz2") as bundle:
                required = tarfile.TarInfo("fixture/model.onnx")
                required.size = len(content)
                bundle.addfile(required, io.BytesIO(content))
                ignored = tarfile.TarInfo("fixture/example.wav")
                ignored.size = 3
                bundle.addfile(ignored, io.BytesIO(b"wav"))
            destination = Path(folder) / "out"
            _safe_extract_required(
                archive,
                destination,
                definition,
                lambda: None,
                lambda *_args: None,
            )
            self.assertEqual((destination / "model.onnx").read_bytes(), content)
            self.assertFalse((destination / "example.wav").exists())


class ManagedAdapterTests(unittest.TestCase):
    def test_moonshine_and_zipformer_use_the_validated_file_layout(self):
        calls = {}

        class FakeRecognizer:
            @classmethod
            def from_moonshine_v2(cls, **kwargs):
                calls["moonshine"] = kwargs
                return object()

            @classmethod
            def from_transducer(cls, **kwargs):
                calls["zipformer"] = kwargs
                return object()

        fake_module = SimpleNamespace(OfflineRecognizer=FakeRecognizer)
        with patch.dict(sys.modules, {"sherpa_onnx": fake_module}):
            _create_recognizer(
                MANAGED_SHERPA_BY_ID[
                    "moonshine-base-zh-quantized-2026-02-27"
                ],
                "cpu",
                "zh",
            )
            _create_recognizer(
                MANAGED_SHERPA_BY_ID["zipformer-korean-2024-06-24"],
                "cpu",
                "ko",
            )
        self.assertTrue(calls["moonshine"]["encoder"].endswith("encoder_model.ort"))
        self.assertTrue(
            calls["moonshine"]["decoder"].endswith("decoder_model_merged.ort")
        )
        self.assertTrue(
            calls["zipformer"]["encoder"].endswith(
                "encoder-epoch-99-avg-1.int8.onnx"
            )
        )
        self.assertTrue(
            calls["zipformer"]["decoder"].endswith(
                "decoder-epoch-99-avg-1.onnx"
            )
        )
        self.assertEqual(calls["zipformer"]["model_type"], "")

    def test_timestamp_conversion_accepts_ctc_terminal_timestamp(self):
        result = SimpleNamespace(
            tokens=["hello", "world"],
            timestamps=[0.1, 0.5, 0.9],
        )
        timings = _timings_from_result(result, 10.0, 11.0, "token")
        self.assertEqual([item["text"] for item in timings], ["hello", "world"])
        self.assertEqual(timings[0]["start"], 10.1)
        self.assertEqual(timings[1]["end"], 11.0)
        self.assertEqual(
            _timings_from_result(result, 10.0, 11.0, "segment"),
            (),
        )


if __name__ == "__main__":
    unittest.main()
