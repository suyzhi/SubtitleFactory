import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import av
import numpy as np


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault(
    "SUBTITLE_FACTORY_DATA_DIR",
    tempfile.mkdtemp(prefix="subtitle-factory-local-thumbnail-tests-"),
)

from app.api import projects
from app.models import database
from app.services.video_thumbnail import generate_video_thumbnail


class FakeUpload:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self._content = content

    async def read(self, _size: int) -> bytes:
        content, self._content = self._content, b""
        return content


def create_test_video(path: Path, width: int = 640, height: int = 360):
    with av.open(str(path), mode="w") as output:
        stream = output.add_stream("mpeg4", rate=10)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        for index in range(12):
            pixels = np.zeros((height, width, 3), dtype=np.uint8)
            pixels[:, :, 0] = 30 + index * 10
            pixels[:, :, 1] = 100
            pixels[:, :, 2] = 180
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts = index
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


class PyAVThumbnailTests(unittest.TestCase):
    def test_generates_small_jpeg_without_external_image_libraries(self):
        with tempfile.TemporaryDirectory() as folder:
            folder_path = Path(folder)
            video_path = folder_path / "input.mp4"
            project_dir = folder_path / "project"
            create_test_video(video_path)

            result = generate_video_thumbnail(str(video_path), str(project_dir))

            self.assertIsNotNone(result)
            thumbnail_path = Path(result)
            self.assertEqual(thumbnail_path.name, "thumbnail.jpg")
            self.assertEqual(thumbnail_path.read_bytes()[:3], b"\xff\xd8\xff")
            with av.open(str(thumbnail_path)) as image:
                frame = next(image.decode(video=0))
            self.assertLessEqual(frame.width, 480)
            self.assertLessEqual(frame.height, 270)
            self.assertEqual((frame.width, frame.height), (480, 270))


class LocalImportThumbnailTests(unittest.TestCase):
    @staticmethod
    def _insert_project(project_id: str, *, old_thumbnail_path: str = ""):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        conn = database.get_db()
        conn.execute(
            """INSERT INTO projects
               (id, title, source_type, source_url, thumbnail_url, thumbnail_path,
                language, target_language, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id, "Old YouTube video", "youtube",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://cdn.example.test/old.jpg", old_thumbnail_path,
                "auto", "zh", now, now,
            ),
        )
        conn.commit()
        conn.close()

    def test_local_import_persists_new_frame_and_clears_remote_cover(self):
        with tempfile.TemporaryDirectory() as folder:
            folder_path = Path(folder)
            db_path = folder_path / "projects.db"
            projects_dir = folder_path / "projects"

            with (
                patch.object(database, "DB_PATH", db_path),
                patch.object(projects, "PROJECTS_DIR", projects_dir),
            ):
                database.init_db()
                self._insert_project("project-id")

                def fake_thumbnail(video_path, project_dir):
                    self.assertTrue(Path(video_path).is_file())
                    thumbnail = Path(project_dir) / "thumbnail.jpg"
                    thumbnail.write_bytes(b"jpeg")
                    return str(thumbnail.resolve())

                with patch.object(
                    projects, "generate_video_thumbnail", side_effect=fake_thumbnail
                ):
                    result = asyncio.run(projects.import_local_video(
                        "project-id", FakeUpload("local.mp4", b"uploaded-video")
                    ))

                conn = database.get_db()
                row = conn.execute(
                    """SELECT source_type, video_path, thumbnail_url, thumbnail_path
                       FROM projects WHERE id = ?""",
                    ("project-id",),
                ).fetchone()
                conn.close()

                self.assertEqual(result["thumbnail_url"], "/api/projects/project-id/thumbnail")
                self.assertEqual(row["source_type"], "local")
                self.assertIsNone(row["thumbnail_url"])
                self.assertEqual(Path(row["thumbnail_path"]).name, "thumbnail.jpg")
                self.assertEqual(
                    projects.get_project("project-id")["thumbnail_url"],
                    "/api/projects/project-id/thumbnail",
                )

    def test_thumbnail_failure_keeps_import_successful_and_clears_old_cover(self):
        with tempfile.TemporaryDirectory() as folder:
            folder_path = Path(folder)
            db_path = folder_path / "projects.db"
            projects_dir = folder_path / "projects"
            old_thumbnail = folder_path / "old-thumbnail.jpg"
            old_thumbnail.write_bytes(b"old")

            with (
                patch.object(database, "DB_PATH", db_path),
                patch.object(projects, "PROJECTS_DIR", projects_dir),
            ):
                database.init_db()
                self._insert_project("project-id", old_thumbnail_path=str(old_thumbnail))

                with (
                    patch.object(
                        projects, "generate_video_thumbnail",
                        side_effect=RuntimeError("decode failed"),
                    ),
                    patch.object(projects.logger, "warning"),
                ):
                    result = asyncio.run(projects.import_local_video(
                        "project-id", FakeUpload("broken.mp4", b"not-a-real-video")
                    ))

                conn = database.get_db()
                row = conn.execute(
                    "SELECT thumbnail_url, thumbnail_path FROM projects WHERE id = ?",
                    ("project-id",),
                ).fetchone()
                conn.close()

                self.assertEqual(result["message"], "本地视频导入成功")
                self.assertIsNone(result["thumbnail_url"])
                self.assertIsNone(row["thumbnail_url"])
                self.assertIsNone(row["thumbnail_path"])
                self.assertIsNone(projects.get_project("project-id")["thumbnail_url"])


if __name__ == "__main__":
    unittest.main()
