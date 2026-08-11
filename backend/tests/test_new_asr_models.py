import json
import os
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
os.environ.setdefault(
    "SUBTITLE_FACTORY_DATA_DIR",
    tempfile.mkdtemp(prefix="subtitle-factory-new-asr-tests-"),
)

from app.services import cloud_asr, qwen_mlx
from app.utils.task_manager import task_manager


def _wav(path: Path, seconds: float = 1.0) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\0\0" * int(16_000 * seconds))


class FunAsrTests(unittest.TestCase):
    def test_endpoint_accepts_only_official_beijing_or_dashscope_hosts(self):
        self.assertEqual(
            cloud_asr._endpoint("https://dashscope.aliyuncs.com/compatible-mode/v1"),
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        )
        self.assertEqual(
            cloud_asr._endpoint("https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"),
            "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        )
        self.assertEqual(
            cloud_asr._endpoint("https://workspace.cn-beijing.maas.aliyuncs.com/api/v1"),
            "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        )
        with self.assertRaises(cloud_asr.CloudAsrError):
            cloud_asr._endpoint("https://example.invalid/compatible-mode/v1")

    def test_status_requires_key_endpoint_and_named_transcription_consent(self):
        provider = {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "has_api_key": True,
            "enabled": True,
        }
        with patch.object(cloud_asr, "get_provider", return_value=provider), patch.object(
            cloud_asr, "_authorization_granted", return_value=True,
        ):
            status = cloud_asr.fun_asr_status()
        self.assertTrue(status["ready"])
        self.assertTrue(status["uploads_audio"])
        self.assertFalse(status["download_required"])

    def test_sse_result_becomes_incremental_word_timed_segment(self):
        captured = {}
        event = {
            "output": {
                "sentence": {
                    "sentence_id": 1,
                    "sentence_end": True,
                    "begin_time": 160,
                    "end_time": 1680,
                    "text": "欢迎使用字幕工厂。",
                    "words": [{
                        "begin_time": 160, "end_time": 520,
                        "text": "欢迎", "punctuation": "，",
                    }],
                },
            },
        }

        class Response:
            status_code = 200
            text = ""

            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def iter_lines(self): return iter([f"data:{json.dumps(event, ensure_ascii=False)}"])

        class Client:
            def __init__(self, *args, **kwargs): captured["timeout"] = kwargs.get("timeout")
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def stream(self, method, url, **kwargs):
                captured.update({"method": method, "url": url, **kwargs})
                return Response()

        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "audio.wav"
            _wav(audio)
            task_id = task_manager.create_task(None, "transcribe")
            provider = {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "secret-key",
                "enabled": True,
            }
            with patch.object(cloud_asr, "_authorization_granted", return_value=True), patch.object(
                cloud_asr, "get_provider", return_value=provider,
            ), patch.object(cloud_asr.httpx, "Client", Client):
                session = cloud_asr.create_fun_asr_session(task_id, str(audio), "zh")
                segments = list(session.segments)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].text, "欢迎使用字幕工厂。")
        self.assertAlmostEqual(segments[0].start, 0.16)
        self.assertEqual(segments[0].words[0]["text"], "欢迎，")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["headers"]["X-DashScope-SSE"], "enable")
        audio_data = captured["json"]["input"]["messages"][0]["content"][0]["audio"]
        self.assertTrue(audio_data.startswith("data:audio/wav;base64,"))


class QwenMlxTests(unittest.TestCase):
    def test_qwen_session_uses_mlx_model_once_per_vad_span(self):
        calls = []

        def transcribe(audio, **kwargs):
            calls.append((audio, kwargs))
            kwargs["on_progress"]({"progress": 1})
            return SimpleNamespace(text=" Apple GPU 转写成功。 ", language="Chinese")

        fake_module = SimpleNamespace(transcribe=transcribe)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio_path = root / "audio.wav"
            model_path = root / "model"
            model_path.mkdir()
            _wav(audio_path)
            task_id = task_manager.create_task(None, "transcribe")
            with patch.dict(sys.modules, {"mlx_qwen3_asr": fake_module}), patch.object(
                qwen_mlx, "resolve_local_model", return_value=model_path,
            ), patch.object(
                qwen_mlx, "_ensure_vad", return_value=root / "silero_vad.onnx",
            ), patch.object(
                qwen_mlx,
                "iter_vad_audio_segments",
                return_value=iter([(0.25, 1.0, np.zeros(12_000, dtype=np.float32))]),
            ):
                session = qwen_mlx.create_qwen_mlx_session(
                    task_id, str(audio_path), "zh", "qwen3-asr-1.7b",
                )
                segments = list(session.segments)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["model"], str(model_path))
        self.assertEqual(calls[0][1]["language"], "zh")
        self.assertEqual(segments[0].text, "Apple GPU 转写成功。")
        self.assertEqual(session.device, "Apple GPU (Metal)")


if __name__ == "__main__":
    unittest.main()
