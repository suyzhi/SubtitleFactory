import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault(
    "SUBTITLE_FACTORY_DATA_DIR",
    tempfile.mkdtemp(prefix="subtitle-factory-runtime-tests-"),
)

from app.services import downloader
from app.services import parakeet_transcriber as parakeet
from app.services import runtime_diagnostics as runtime
from app.services.transcriber import resolve_transcription_model
from app.api.projects import _runtime_options, _select_runtime
from fastapi import HTTPException


def _executable(folder: Path, name: str, output: str = "tool version 1") -> Path:
    path = folder / name
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class DownloadRuntimeTests(unittest.TestCase):
    def test_ffmpeg_resolution_prefers_bundled_then_user_then_environment_then_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundled = _executable(root, "bundled-ffmpeg", "bundled")
            user = _executable(root, "user-ffmpeg", "user")
            environment = _executable(root, "environment-ffmpeg", "environment")
            system = _executable(root, "system-ffmpeg", "system")
            with patch.object(
                runtime, "_bundled_candidates", return_value=[(bundled, "bundled")]
            ), patch.object(
                runtime, "environment_path_overrides_enabled", return_value=True
            ), patch.dict(
                os.environ, {"FFMPEG_PATH": str(environment)}
            ), patch.object(
                runtime.shutil, "which", side_effect=lambda name: str(system) if name == "ffmpeg" else None
            ):
                selected = runtime.resolve_ffmpeg_path(user)
            self.assertIsNotNone(selected)
            self.assertEqual(selected.path, bundled.resolve())
            self.assertEqual(selected.source, "bundled")

    def test_invalid_bundled_runtime_falls_back_to_user_setting(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            invalid = root / "not-executable"
            invalid.write_text("invalid", encoding="utf-8")
            user = _executable(root, "user-ffmpeg", "user")
            with patch.object(
                runtime, "_bundled_candidates", return_value=[(invalid, "bundled")]
            ), patch.object(runtime.shutil, "which", return_value=None):
                selected = runtime.resolve_ffmpeg_path(user)
            self.assertEqual(selected.path, user.resolve())
            self.assertEqual(selected.source, "user")

    def test_normal_release_does_not_inherit_advanced_environment_path(self):
        with tempfile.TemporaryDirectory() as folder:
            environment = _executable(Path(folder), "environment-ffmpeg")
            with patch.object(runtime, "_bundled_candidates", return_value=[]), patch.object(
                runtime, "environment_path_overrides_enabled", return_value=False
            ), patch.dict(os.environ, {"FFMPEG_PATH": str(environment)}), patch.object(
                runtime.shutil, "which", return_value=None
            ):
                self.assertIsNone(runtime.resolve_ffmpeg_path())

    def test_architecture_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = _executable(Path(folder), "ffmpeg")
            with patch.object(runtime, "_detect_architectures", return_value=("x86_64",)), patch.object(
                runtime.platform, "machine", return_value="arm64"
            ):
                result = runtime.validate_runtime_executable(
                    executable, name="ffmpeg", source="bundled"
                )
            self.assertFalse(result.available)
            self.assertIn("架构不兼容", result.error)

    def test_youtube_position_parameters_are_removed_and_share_links_canonicalized(self):
        self.assertEqual(
            downloader.normalize_youtube_url(
                "https://www.youtube.com/watch?v=purSYm87Cas&t=110s&start=10"
            ),
            "https://www.youtube.com/watch?v=purSYm87Cas",
        )
        self.assertEqual(
            downloader.normalize_youtube_url("https://youtu.be/purSYm87Cas?t=110"),
            "https://www.youtube.com/watch?v=purSYm87Cas",
        )
        self.assertEqual(
            downloader.normalize_youtube_url("https://example.test/watch?t=110"),
            "https://example.test/watch?t=110",
        )

    def test_download_fails_with_stable_code_before_network_when_ffmpeg_missing(self):
        with patch.object(downloader, "resolve_ffmpeg_path", return_value=None), patch.object(
            downloader.yt_dlp, "YoutubeDL",
            side_effect=AssertionError("network must not start without merge runtime"),
        ):
            with self.assertRaises(downloader.DownloadServiceError) as raised:
                downloader.download_video("task", "https://youtu.be/purSYm87Cas", "project")
        self.assertEqual(raised.exception.error_code, "DOWNLOAD_RUNTIME_MISSING")

    def test_download_error_classification_is_stable(self):
        unavailable = downloader._classify_download_error(Exception("ERROR: Video unavailable"))
        merged = downloader._classify_download_error(Exception("ffmpeg merger exited with code 1"))
        self.assertEqual(unavailable.error_code, "VIDEO_UNAVAILABLE")
        self.assertEqual(merged.error_code, "MERGE_FAILED")


class ModelIndependenceTests(unittest.TestCase):
    def test_runtime_options_expose_stable_ids_and_visible_device_metadata(self):
        options = _runtime_options("small")
        self.assertEqual([item["id"] for item in options], ["cpu", "mlx"])
        self.assertEqual(options[0]["name"], "CPU")
        self.assertEqual(options[1]["name"], "Apple GPU")
        self.assertIn("engine", options[1])
        self.assertIn("available", options[1])

    def test_runtime_must_be_explicit_or_remembered(self):
        with self.assertRaises(HTTPException) as raised:
            _select_runtime("small", None, {"transcription_runtime_by_model": {}})
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["code"], "RUNTIME_SELECTION_REQUIRED")
        self.assertEqual(
            _select_runtime("small", None, {"transcription_runtime_by_model": {"small": "cpu"}}),
            "cpu",
        )

    def test_auto_is_safe_whisper_small_without_memo(self):
        with patch.object(
            parakeet, "discover_coreml_runtime",
            side_effect=AssertionError("auto must not depend on Memo"),
        ):
            resolution = resolve_transcription_model("auto", default_model="small")
        self.assertEqual(resolution.model_id, "small")
        self.assertEqual(resolution.load_target, "small")
        self.assertFalse(resolution.fell_back)

    def test_invalid_custom_model_falls_back_without_persisting_path(self):
        secret_path = "/Users/example/private/missing-model"
        resolution = resolve_transcription_model(
            "custom", custom_model_path=secret_path
        )
        self.assertEqual(resolution.model_id, "small")
        self.assertTrue(resolution.fell_back)
        self.assertNotIn(secret_path, str(resolution.to_details()))

    def test_valid_custom_model_is_selected_but_details_are_redacted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("model.bin", "config.json", "tokenizer.json"):
                (root / name).write_bytes(b"model")
            resolution = resolve_transcription_model(
                "custom", custom_model_path=root
            )
            self.assertEqual(resolution.model_id, "custom")
            self.assertEqual(resolution.source, "custom_path")
            self.assertEqual(resolution.load_target, str(root.resolve()))
            self.assertNotIn(str(root), str(resolution.to_details()))

    def test_unavailable_coreml_and_unsupported_parakeet_language_fall_back(self):
        with patch(
            "app.services.transcriber.get_parakeet_model_status",
            return_value={"ready": False, "source": "unavailable"},
        ):
            missing = resolve_transcription_model(parakeet.PARAKEET_MODEL_ID, "en")
        unsupported = resolve_transcription_model(parakeet.PARAKEET_ONNX_MODEL_ID, "zh")
        self.assertEqual(missing.model_id, "small")
        self.assertEqual(unsupported.model_id, "small")
        self.assertIn("不支持", unsupported.fallback_reason)

    def test_prepare_model_forwards_repair_to_atomic_asset_manager(self):
        expected = {"model_id": parakeet.PARAKEET_ONNX_MODEL_ID, "ready": True}
        with patch.object(parakeet, "ensure_parakeet_assets") as ensure, patch.object(
            parakeet, "get_parakeet_model_status", return_value=expected
        ):
            result = parakeet.prepare_parakeet_model(
                "repair-task", parakeet.PARAKEET_ONNX_MODEL_ID, repair=True
            )
        self.assertEqual(result, expected)
        ensure.assert_called_once_with("repair-task", None, repair=True)


if __name__ == "__main__":
    unittest.main()
