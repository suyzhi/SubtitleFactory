import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services import playback_info
from app.services.media_tools import require_ffmpeg
from app.services.playback_info import get_playback_info


def make_video(path: Path, fps: int) -> None:
    ffmpeg = require_ffmpeg()
    subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=64x48:d=1:r={fps}",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )


class PlaybackInfoTests(unittest.TestCase):
    def test_cache_reuses_metadata_but_invalidates_replaced_media(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "media.mp4"
            make_video(path, 24)
            with patch.object(playback_info, "probe_media", wraps=playback_info.probe_media) as probed:
                first = get_playback_info(path)
                first["frame_rate"] = 999
                self.assertEqual(get_playback_info(path)["frame_rate"], 24)
                self.assertEqual(probed.call_count, 1)
            replacement = Path(folder) / "replacement.mp4"
            make_video(replacement, 60)
            replacement.replace(path)
            self.assertEqual(get_playback_info(path)["frame_rate"], 60)

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
        fake_probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "avg_frame_rate": "0/0",
                    "duration": "2.0",
                }
            ],
            "format": {"duration": "2.0"},
        }
        playback_info._cached_playback_info.cache_clear()
        with patch.object(playback_info, "probe_media", return_value=fake_probe):
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
