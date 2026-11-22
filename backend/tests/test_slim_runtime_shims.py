"""Tests for slimmed runtime shims (MLX timing C lib, PyAV-free audio, sherpa VAD, QuickJS)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services import downloader
from app.services import faster_whisper_shim as fw_shim
from app.services import mlx_timing_shim as mlx_shim
from app.services.media_tools import require_ffmpeg


class MlxTimingShimTests(unittest.TestCase):
    def test_dtw_native_and_numpy_fallback_match_bit_for_bit(self):
        rng = np.random.default_rng(123)
        for shape in [(1, 1), (4, 9), (20, 80)]:
            matrix = rng.standard_normal(shape).astype(np.float32)
            native = mlx_shim.dtw_cpu(matrix)
            with patch.object(mlx_shim, "_load_native_lib", return_value=None):
                fallback = mlx_shim.dtw_cpu(matrix)
            self.assertEqual(native.shape[0], 2)
            self.assertTrue(np.array_equal(native, fallback))

    def test_median_filter_native_and_numpy_fallback_match(self):
        rng = np.random.default_rng(456)
        for shape in [(15,), (4, 8, 60)]:
            for width in (1, 3, 7):
                arr = rng.standard_normal(shape).astype(np.float32)
                native = mlx_shim.median_filter(arr, width)
                with patch.object(mlx_shim, "_load_native_lib", return_value=None):
                    fallback = mlx_shim.median_filter(arr, width)
                self.assertEqual(native.shape, arr.shape)
                self.assertTrue(np.array_equal(native, fallback))

    def test_median_filter_short_array_returns_unchanged(self):
        arr = np.array([[1.0, 2.0]], dtype=np.float32)
        self.assertTrue(np.array_equal(mlx_shim.median_filter(arr, 7), arr))


class FasterWhisperShimTests(unittest.TestCase):
    def test_decode_audio_wav_fast_path_and_ffmpeg_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wav_path = root / "sample.wav"
            t = np.linspace(0, 0.5, 8000, endpoint=False)
            pcm = (np.sin(2 * np.pi * 440 * t) * 16000).astype("<i2")
            with wave.open(str(wav_path), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(16000)
                writer.writeframes(pcm.tobytes())

            decoded_wav = fw_shim.decode_audio(str(wav_path), sampling_rate=16000)
            self.assertEqual(decoded_wav.dtype, np.float32)
            self.assertEqual(decoded_wav.shape, (8000,))

            # Non-WAV container goes through bundled FFmpeg without PyAV.
            m4a_path = root / "sample.mkv"
            ffmpeg = require_ffmpeg()
            subprocess.run(
                [
                    str(ffmpeg),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(wav_path),
                    "-c:a",
                    "pcm_s16le",
                    str(m4a_path),
                ],
                check=True,
            )
            decoded_mkv = fw_shim.decode_audio(str(m4a_path), sampling_rate=16000)
            self.assertEqual(decoded_mkv.dtype, np.float32)
            self.assertAlmostEqual(decoded_mkv.shape[0], 8000, delta=64)

    def test_sherpa_vad_and_coreml_probe(self):
        self.assertTrue(fw_shim.sherpa_coreml_available())
        silence = np.zeros(32000, dtype=np.float32)
        self.assertEqual(fw_shim.sherpa_get_speech_timestamps(silence), [])


class QuickJsPreferenceTests(unittest.TestCase):
    def test_download_options_prefers_quickjs_over_deno(self):
        with (
            patch.object(
                downloader,
                "resolve_quickjs_path",
                return_value=SimpleNamespace(path=Path("/app/bin/qjs"), source="bundled"),
            ),
            patch.object(
                downloader,
                "_resolve_deno_path",
                return_value=Path("/usr/local/bin/deno"),
            ),
        ):
            options = downloader._download_options("task-qjs", "/tmp/%(title)s.%(ext)s")
        self.assertEqual(options["js_runtimes"], {"quickjs": {"path": "/app/bin/qjs"}})


if __name__ == "__main__":
    unittest.main()
