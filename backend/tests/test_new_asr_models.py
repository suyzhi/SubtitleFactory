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
if "SUBTITLE_FACTORY_DATA_DIR" not in os.environ:
    os.environ["SUBTITLE_FACTORY_DATA_DIR"] = tempfile.mkdtemp(
        prefix="subtitle-factory-new-asr-tests-",
    )

from app.services import qwen_mlx
from app.utils.task_manager import task_manager


def _wav(path: Path, seconds: float = 1.0) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\0\0" * int(16_000 * seconds))


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
