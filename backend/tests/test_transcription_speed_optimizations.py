"""Unit tests for transcription speed optimizations across local and cloud engines."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
if "SUBTITLE_FACTORY_DATA_DIR" not in os.environ:
    os.environ["SUBTITLE_FACTORY_DATA_DIR"] = tempfile.mkdtemp(
        prefix="subtitle-factory-speed-opt-tests-",
    )

from app.services import cloud_asr, managed_sherpa, qwen_mlx, transcriber
from app.services import parakeet_transcriber as parakeet
from app.services.model_pool import (
    model_identity,
    optimal_cpu_threads,
    sherpa_model_pool,
)
from app.services.parakeet_transcriber import coreml as parakeet_coreml
from app.utils.task_manager import task_manager


def _write_pcm16_wav(
    path: Path,
    seconds: float = 1.0,
    *,
    sample_rate: int = 16_000,
    channels: int = 1,
) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\0\0" * int(sample_rate * seconds) * channels)


class ModelPoolOptimizationTests(unittest.TestCase):
    def test_optimal_cpu_threads_returns_bounded_positive_int(self):
        threads = optimal_cpu_threads()
        self.assertIsInstance(threads, int)
        self.assertGreaterEqual(threads, 1)
        self.assertLessEqual(threads, 8)

    def test_model_identity_tracks_explicit_files_and_invalidates_on_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            enc = root / "encoder.int8.onnx"
            tok = root / "tokens.txt"
            enc.write_bytes(b"v1")
            tok.write_text("a 0\n", encoding="utf-8")
            first = model_identity(str(root), "cpu", "sherpa-onnx", files=(enc, tok))
            second = model_identity(str(root), "cpu", "sherpa-onnx", files=(enc, tok))
            self.assertEqual(first, second)
            enc.write_bytes(b"v2-updated")
            third = model_identity(str(root), "cpu", "sherpa-onnx", files=(enc, tok))
            self.assertNotEqual(first, third)


class VadPrefetchTests(unittest.TestCase):
    def test_prefetch_vad_segments_preserves_order_and_propagates_errors(self):
        items = [(float(i), float(i + 1), np.zeros(160, dtype=np.float32)) for i in range(6)]
        received = list(managed_sherpa.prefetch_vad_segments(iter(items), maxsize=2))
        self.assertEqual([s for s, _, _ in received], [0.0, 1.0, 2.0, 3.0, 4.0, 5.0])

        def failing_source():
            yield (0.0, 1.0, np.zeros(160, dtype=np.float32))
            raise RuntimeError("vad boom")

        gen = managed_sherpa.prefetch_vad_segments(failing_source(), maxsize=2)
        self.assertEqual(next(gen)[0], 0.0)
        with self.assertRaisesRegex(RuntimeError, "vad boom"):
            next(gen)

    def test_prefetch_vad_segments_stops_cleanly_on_early_close(self):
        produced = 0

        def infinite_source():
            nonlocal produced
            while True:
                produced += 1
                yield (float(produced), float(produced + 1), np.zeros(16, dtype=np.float32))

        gen = managed_sherpa.prefetch_vad_segments(infinite_source(), maxsize=2)
        first = next(gen)
        self.assertEqual(first[0], 1.0)
        gen.close()
        before = produced
        time.sleep(0.05)
        self.assertLessEqual(produced - before, 2)


class QwenBatchedDecodeTests(unittest.TestCase):
    def test_decode_qwen_batch_casts_float32_audio_features_to_float16_and_handles_variable_lengths(self):
        import mlx.core as mx

        recorded: dict[str, object] = {}

        class FakeCache:
            def __init__(self, max_seq_len: int):
                self.max_seq_len = max_seq_len
                self.offset = 0

        class FakeDecoder:
            def __call__(
                self,
                input_ids=None,
                inputs_embeds=None,
                position_ids=None,
                attention_mask=None,
                cache=None,
            ):
                if inputs_embeds is not None:
                    recorded["prefill_dtype"] = inputs_embeds.dtype
                    recorded["prefill_shape"] = tuple(inputs_embeds.shape)
                    recorded["prefill_mask_has_nan"] = bool(mx.any(mx.isnan(attention_mask)).item())
                    # Verify every row has at least one unmasked key (0.0) so softmax cannot NaN.
                    row_max = mx.max(attention_mask, axis=-1)
                    recorded["prefill_row_max_zero"] = bool(mx.all(row_max == 0.0).item())
                    batch_size = inputs_embeds.shape[0]
                    cache.offset = inputs_embeds.shape[1]
                    return mx.zeros((batch_size, inputs_embeds.shape[1], 4), dtype=mx.float16)
                cache.offset += 1
                recorded.setdefault("step_count", 0)
                recorded["step_count"] = int(recorded["step_count"]) + 1
                return mx.zeros((input_ids.shape[0], 1, 4), dtype=mx.float16)

        class FakeModel:
            audio_token_id = 99

            def __init__(self):
                self.model = FakeDecoder()
                self._step = 0
                # Sequence 0 emits [10, EOS]; Sequence 1 emits [20, 21, EOS].
                self._script = {
                    0: [10, 151643, 151643],
                    1: [20, 21, 151643],
                }

            def audio_tower(self, mel, _feature_lens):
                # Deliberately return float32 like mlx_qwen3_asr's SinusoidalPositionEmbedding bug.
                n_tokens = max(2, int(mel.shape[-1]) // 4)
                return mx.zeros((1, n_tokens, 4), dtype=mx.float32), None

            def _embed_tokens(self, ids, validate_input_ids=False):
                return mx.zeros((ids.shape[0], ids.shape[1], 4), dtype=mx.float16)

            def _inject_audio_features(self, embeds, audio_features, _mask):
                # Mirrors upstream promotion when audio_features isn't float16.
                return embeds + mx.zeros(embeds.shape, dtype=audio_features.dtype)

            def create_cache(self, max_seq_len: int):
                return FakeCache(max_seq_len)

            def lm_head(self, hidden):
                batch_size = hidden.shape[0]
                vocab = 151650
                logits = np.full((batch_size, 1, vocab), -10.0, dtype=np.float16)
                for row in range(batch_size):
                    idx = min(self._step, len(self._script[row]) - 1)
                    logits[row, 0, self._script[row][idx]] = 10.0
                self._step += 1
                return mx.array(logits, dtype=mx.float16)

        class FakeTokenizer:
            def build_prompt_tokens(self, n_audio_tokens: int, language=None, context: str = ""):
                return [1, 2] + [99] * n_audio_tokens + [3]

            def decode(self, tokens: list[int]) -> str:
                mapping = {10: "第一句", 20: "第二", 21: "句"}
                return "".join(mapping.get(t, "") for t in tokens)

        components = {
            "mx": mx,
            "np": np,
            "compute_features": lambda samples: (
                mx.zeros((1, 4, max(8, len(samples) // 200)), dtype=mx.float32),
                mx.array([max(8, len(samples) // 200)]),
            ),
            "resolve_max_new_tokens": lambda _limit, audio_duration_sec: 16,
            "detect_repetition": lambda _tokens: False,
            "parse_asr_output": lambda raw, user_language=None: (user_language or "zh", raw),
        }
        task_id = task_manager.create_task(None, "transcribe")
        batch = [
            np.zeros(1600, dtype=np.float32),
            np.zeros(4800, dtype=np.float32),
        ]
        texts = qwen_mlx._decode_qwen_batch(
            task_id, FakeModel(), FakeTokenizer(), batch, "Chinese", components
        )
        self.assertEqual(texts, ["第一句", "第二句"])
        self.assertEqual(recorded["prefill_dtype"], mx.float16)
        self.assertFalse(recorded["prefill_mask_has_nan"])
        self.assertTrue(recorded["prefill_row_max_zero"])


class ParakeetAndSherpaPoolTests(unittest.TestCase):
    def tearDown(self):
        sherpa_model_pool.clear()

    def test_parakeet_onnx_reuses_pooled_recognizer_and_invalidates_on_file_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / "audio.wav"
            _write_pcm16_wav(audio, seconds=1.0)
            model_dir = root / "model"
            model_dir.mkdir()
            assets = parakeet.ParakeetAssets(
                model_dir=model_dir,
                encoder=model_dir / "encoder.int8.onnx",
                decoder=model_dir / "decoder.int8.onnx",
                joiner=model_dir / "joiner.int8.onnx",
                tokens=model_dir / "tokens.txt",
                vad=root / "silero_vad.onnx",
            )
            for path in (assets.encoder, assets.decoder, assets.joiner, assets.tokens, assets.vad):
                path.write_bytes(b"v1")

            load_count = 0

            class FakeStream:
                def __init__(self):
                    self.result = SimpleNamespace(text="")

                def accept_waveform(self, _sr, _samples):
                    pass

            class FakeRecognizer:
                def create_stream(self):
                    return FakeStream()

                def decode_stream(self, stream):
                    stream.result.text = "Cached Parakeet."

            class FakeOfflineRecognizer:
                @classmethod
                def from_transducer(cls, **_kwargs):
                    nonlocal load_count
                    load_count += 1
                    return FakeRecognizer()

            class FakeVadConfig:
                def __init__(self):
                    self.silero_vad = SimpleNamespace(
                        model="", threshold=0.0, min_silence_duration=0.0,
                        min_speech_duration=0.0, max_speech_duration=0.0, window_size=512,
                    )
                    self.sample_rate = 0

            class FakeVad:
                def __init__(self, _cfg, buffer_size_in_seconds):
                    self._items = []

                def accept_waveform(self, _samples):
                    pass

                def flush(self):
                    self._items.append(
                        SimpleNamespace(start=0, samples=np.ones(4000, dtype=np.float32))
                    )

                def empty(self):
                    return not self._items

                @property
                def front(self):
                    return self._items[0]

                def pop(self):
                    self._items.pop(0)

            fake_sherpa = SimpleNamespace(
                OfflineRecognizer=FakeOfflineRecognizer,
                VadModelConfig=FakeVadConfig,
                VoiceActivityDetector=FakeVad,
            )

            with patch.object(parakeet, "ensure_parakeet_assets", return_value=assets), patch.object(
                parakeet, "_import_sherpa_onnx", return_value=fake_sherpa
            ):
                t1 = task_manager.create_task(None, "transcribe")
                s1 = parakeet.create_parakeet_session(
                    t1, str(audio), "auto", parakeet.PARAKEET_ONNX_MODEL_ID
                )
                self.assertEqual(len(list(s1.segments)), 1)
                self.assertFalse(task_manager.get_task(t1)["details"]["model_cache_hit"])

                t2 = task_manager.create_task(None, "transcribe")
                s2 = parakeet.create_parakeet_session(
                    t2, str(audio), "auto", parakeet.PARAKEET_ONNX_MODEL_ID
                )
                self.assertEqual(len(list(s2.segments)), 1)
                self.assertTrue(task_manager.get_task(t2)["details"]["model_cache_hit"])
                self.assertEqual(load_count, 1)

                # Changing model weights invalidates the cache immediately.
                assets.encoder.write_bytes(b"v2-new-weights")
                t3 = task_manager.create_task(None, "transcribe")
                s3 = parakeet.create_parakeet_session(
                    t3, str(audio), "auto", parakeet.PARAKEET_ONNX_MODEL_ID
                )
                self.assertEqual(len(list(s3.segments)), 1)
                self.assertFalse(task_manager.get_task(t3)["details"]["model_cache_hit"])
                self.assertEqual(load_count, 2)

    def test_coreml_cli_validation_cache_hits_and_invalidates_on_change(self):
        with tempfile.TemporaryDirectory() as folder:
            cli = Path(folder) / "parakeet-coreml"
            cli.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
            cli.chmod(0o755)
            calls = 0

            def fake_validate(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                return SimpleNamespace(available=True)

            parakeet_coreml._VALID_CLI_CACHE.clear()
            with patch.object(parakeet_coreml, "validate_runtime_executable", side_effect=fake_validate):
                self.assertTrue(parakeet_coreml._valid_coreml_cli(cli))
                self.assertTrue(parakeet_coreml._valid_coreml_cli(cli))
                self.assertEqual(calls, 1)
                cli.write_text("#!/bin/sh\necho updated\n", encoding="utf-8")
                self.assertTrue(parakeet_coreml._valid_coreml_cli(cli))
                self.assertEqual(calls, 2)

    def test_coreml_runner_throttles_frequent_progress_updates_and_preserves_endpoints(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = parakeet.CoreMLRuntime(root / "model", root / "parakeet")
            audio = root / "input.wav"
            audio.write_bytes(b"audio")
            progress_updates: list[int] = []

            class FakeProcess:
                def __init__(self, stdout_text: str):
                    self.stdout = io.StringIO(stdout_text)

                def poll(self):
                    return 0

                def wait(self, timeout=None):
                    return 0

                def terminate(self):
                    pass

            def fake_popen(command, **_kwargs):
                output_dir = Path(command[command.index("--output-dir") + 1])
                filename = command[command.index("--output-filename") + 1]
                output_path = output_dir / f"{filename}.json"
                output_path.write_text(
                    json.dumps({"duration": 1, "text": "Hi", "tokenTimings": []}),
                    encoding="utf-8",
                )
                lines = [json.dumps({"status": "progress", "progress": p}) for p in range(0, 101)]
                lines.append(json.dumps({"status": "success", "result": str(output_path)}))
                return FakeProcess("\n".join(lines) + "\n")

            fake_tm = SimpleNamespace(
                checkpoint=lambda _tid: None,
                update_task=lambda _tid, **kw: progress_updates.append(
                    kw["details"]["coreml"]["progress"]
                ),
            )
            with patch.object(parakeet, "task_manager", fake_tm):
                payload = parakeet._run_coreml_cli("throttle-test", str(audio), runtime, fake_popen)
            self.assertEqual(payload["text"], "Hi")
            self.assertEqual(progress_updates[0], 0)
            self.assertEqual(progress_updates[-1], 100)
            self.assertLess(len(progress_updates), 20)


class MlxWhisperAndWavFastPathTests(unittest.TestCase):
    def test_load_pcm16_wav_float32_accepts_16k_mono_and_rejects_others(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            good = root / "good.wav"
            stereo = root / "stereo.wav"
            wrong_rate = root / "rate.wav"
            _write_pcm16_wav(good, seconds=1.5, sample_rate=16_000, channels=1)
            _write_pcm16_wav(stereo, seconds=1.0, sample_rate=16_000, channels=2)
            _write_pcm16_wav(wrong_rate, seconds=1.0, sample_rate=44_100, channels=1)

            loaded = transcriber._load_pcm16_wav_float32(str(good))
            self.assertIsNotNone(loaded)
            samples, duration = loaded
            self.assertEqual(len(samples), 24_000)
            self.assertAlmostEqual(duration, 1.5)
            self.assertIsNone(transcriber._load_pcm16_wav_float32(str(stereo)))
            self.assertIsNone(transcriber._load_pcm16_wav_float32(str(wrong_rate)))

    def test_mlx_whisper_uses_vad_windows_shifts_timestamps_and_reuses_language(self):
        calls: list[dict] = []

        def fake_transcribe(audio_input, **kwargs):
            calls.append({"len": len(audio_input), **kwargs})
            return {
                "language": "zh",
                "segments": [
                    {
                        "start": 0.5,
                        "end": 2.0,
                        "text": f"段{len(calls)}",
                        "words": [{"word": "词", "start": 0.5, "end": 1.0}],
                    }
                ],
            }

        fake_mlx_whisper = SimpleNamespace(transcribe=fake_transcribe)
        samples = np.zeros(16_000 * 60, dtype=np.float32)
        detected_box = ["auto"]
        task_id = task_manager.create_task(None, "transcribe")
        with patch.object(transcriber, "_clear_mlx_metal_cache") as cleared:
            segments = list(
                transcriber._iter_mlx_whisper_segments(
                    task_id,
                    fake_mlx_whisper,
                    "/fake/model",
                    "/fake/audio.wav",
                    None,
                    (samples, 60.0),
                    [(2.0, 12.0), (40.0, 52.0)],
                    detected_box,
                )
            )
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0]["language"])
        self.assertEqual(calls[1]["language"], "zh")
        self.assertFalse(calls[0]["condition_on_previous_text"])
        self.assertEqual(detected_box[0], "zh")
        self.assertAlmostEqual(segments[0].start, 2.5)
        self.assertAlmostEqual(segments[0].words[0]["start"], 2.5)
        self.assertAlmostEqual(segments[1].start, 40.5)
        self.assertAlmostEqual(segments[1].words[0]["end"], 41.0)
        cleared.assert_called_once()


class CloudAsrConcurrencyTests(unittest.TestCase):
    def test_multichunk_cloud_asr_preserves_strict_chronological_order(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "long.wav"
            # 320s -> 3 chunks (0s, 150s, 300s)
            _write_pcm16_wav(audio, seconds=320.0)
            task_id = task_manager.create_task(None, "transcribe")
            provider = {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "secret-key",
                "enabled": True,
            }
            active = 0
            peak_active = 0
            lock = threading.Lock()

            def fake_request(_tid, _client, _endpoint, _key, offset, _wav_bytes):
                nonlocal active, peak_active
                with lock:
                    active += 1
                    peak_active = max(peak_active, active)
                try:
                    # Make earlier chunks finish slower than later chunks.
                    delay = 0.08 if offset == 0.0 else (0.04 if offset == 150.0 else 0.01)
                    time.sleep(delay)
                    yield cloud_asr.CloudAsrSegment(
                        start=offset + 1.0,
                        end=offset + 2.0,
                        text=f"chunk@{int(offset)}",
                    )
                finally:
                    with lock:
                        active -= 1

            with patch.object(cloud_asr, "_authorization_granted", return_value=True), patch.object(
                cloud_asr, "get_provider", return_value=provider
            ), patch.object(cloud_asr, "_request_chunk", side_effect=fake_request):
                session = cloud_asr.create_fun_asr_session(task_id, str(audio), "zh")
                segments = list(session.segments)

            self.assertEqual([s.text for s in segments], ["chunk@0", "chunk@150", "chunk@300"])
            self.assertGreaterEqual(peak_active, 2)


if __name__ == "__main__":
    unittest.main()
