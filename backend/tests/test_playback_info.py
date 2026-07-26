import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import av

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.playback_info import get_playback_info


def make_video(path: Path, fps: int) -> None:
    with av.open(str(path), "w") as output:
        stream = output.add_stream("mpeg4", rate=fps)
        stream.width = 64
        stream.height = 48
        stream.pix_fmt = "yuv420p"
        for index in range(fps):
            frame = av.VideoFrame(64, 48, "yuv420p")
            frame.pts = index
            frame.time_base = Fraction(1, fps)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


class PlaybackInfoTests(unittest.TestCase):
    def test_common_frame_rates(self):
        with tempfile.TemporaryDirectory() as folder:
            for fps in (24, 25, 30, 60):
                with self.subTest(fps=fps):
                    path = Path(folder) / f"{fps}.mp4"
                    make_video(path, fps)
                    info = get_playback_info(path)
                    self.assertAlmostEqual(info["frame_rate"], fps, places=3)
                    self.assertAlmostEqual(info["frame_duration"], 1 / fps, places=6)
                    self.assertTrue(info["frame_rate_reliable"])

    def test_missing_rate_falls_back_to_30_without_writing(self):
        path = Path(__file__)
        stream = SimpleNamespace(
            average_rate=None,
            guessed_rate=None,
            base_rate=None,
            duration=120,
            time_base=Fraction(1, 60),
        )
        class Container:
            streams = SimpleNamespace(video=[stream])
            duration = None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        container = Container()
        with patch("app.services.playback_info.av.open", return_value=container):
            info = get_playback_info(path)
        self.assertEqual(info["frame_rate"], 30)
        self.assertEqual(info["frame_rate_source"], "fallback_30fps")
        self.assertFalse(info["frame_rate_reliable"])

    def test_invalid_video_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "missing.mp4"
            with self.assertRaisesRegex(ValueError, "不存在"):
                get_playback_info(missing)


if __name__ == "__main__":
    unittest.main()
