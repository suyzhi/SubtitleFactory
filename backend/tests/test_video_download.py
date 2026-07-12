import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault(
    "SUBTITLE_FACTORY_DATA_DIR",
    tempfile.mkdtemp(prefix="subtitle-factory-video-tests-"),
)

from app.api import projects
from app.models import database
from app.services import downloader


class DownloadQualityTests(unittest.TestCase):
    def test_options_select_unrestricted_best_streams_and_mp4_remux(self):
        options = downloader._download_options(
            "task-id",
            "/tmp/%(title)s.%(ext)s",
            thumbnail_template="/tmp/thumbnail.%(ext)s",
        )

        self.assertEqual(options["format"], "bestvideo+bestaudio/best")
        self.assertNotIn("height", options["format"])
        self.assertNotIn("res", options["format"])
        self.assertEqual(options["merge_output_format"], "mp4")
        self.assertEqual(options["final_ext"], "mp4")
        self.assertEqual(options["postprocessors"], [{
            "key": "FFmpegVideoRemuxer",
            "preferedformat": "mp4",
        }])
        self.assertTrue(options["writethumbnail"])
        self.assertEqual(options["outtmpl"]["thumbnail"], "/tmp/thumbnail.%(ext)s")

    def test_quality_limit_and_container_settings_change_yt_dlp_options(self):
        limited = downloader._download_options(
            "task-id", "/tmp/%(title)s.%(ext)s",
            quality="1080p", container="mkv",
        )
        self.assertIn("height<=1080", limited["format"])
        self.assertEqual(limited["merge_output_format"], "mkv")
        self.assertEqual(limited["final_ext"], "mkv")
        self.assertEqual(limited["postprocessors"][0]["preferedformat"], "mkv")

        webm = downloader._download_options(
            "task-id", "/tmp/%(title)s.%(ext)s",
            quality="720p", container="webm",
        )
        self.assertIn("bestvideo[ext=webm][height<=720]", webm["format"])
        self.assertIn("bestaudio[ext=webm]", webm["format"])
        self.assertEqual(webm["merge_output_format"], "webm")

    def test_progress_never_moves_backwards_between_video_and_audio_streams(self):
        options = downloader._download_options(
            "task-id", "/tmp/%(title)s.%(ext)s",
        )
        hook = options["progress_hooks"][0]

        with (
            patch.object(downloader.task_manager, "wait_if_paused"),
            patch.object(downloader.task_manager, "update_task") as update_task,
        ):
            hook({"status": "downloading", "downloaded_bytes": 90, "total_bytes": 100})
            # yt-dlp starts the second requested stream with a fresh byte count.
            hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100})
            hook({"status": "downloading", "downloaded_bytes": 100, "total_bytes": 100})

        progress = [call.kwargs["progress"] for call in update_task.call_args_list]
        self.assertEqual(progress, sorted(progress))
        self.assertGreaterEqual(progress[1], progress[0])

    def test_download_returns_postprocessed_video_and_records_thumbnail(self):
        captured_options = []

        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options
                captured_options.append(options)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download):
                self.assert_download = download
                project_dir = Path(self.options["outtmpl"]["default"]).parent
                final_video = project_dir / "highest-quality.mp4"
                temporary_stream = project_dir / "highest-quality.f401.mp4"
                thumbnail = project_dir / "thumbnail.webp"
                final_video.write_bytes(b"merged-video-and-audio")
                temporary_stream.write_bytes(b"temporary-video-only-stream")
                thumbnail.write_bytes(b"thumbnail")
                return {
                    "id": "video-id",
                    "title": "Highest quality",
                    "filepath": str(final_video),
                    "thumbnail": "https://cdn.example.test/cover.webp",
                    "thumbnails": [{"filepath": str(thumbnail)}],
                    "requested_downloads": [{"filepath": str(temporary_stream)}],
                }

            def prepare_filename(self, info):
                return str(Path(info["filepath"]).with_suffix(".webm"))

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(downloader, "DOWNLOADS_DIR", Path(folder)),
                patch.object(downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL),
                patch.object(
                    downloader,
                    "resolve_ffmpeg_path",
                    return_value=SimpleNamespace(path=Path("/app/bin/ffmpeg"), source="bundled"),
                ),
                patch.object(downloader.task_manager, "update_task") as update_task,
            ):
                result = downloader.download_video(
                    "task-id", "https://example.test/watch", "project-id"
                )

            self.assertEqual(Path(result).name, "highest-quality.mp4")
            self.assertEqual(Path(result).read_bytes(), b"merged-video-and-audio")
            details = update_task.call_args_list[-1].kwargs["details"]
            self.assertEqual(details["video_path"], result)
            self.assertEqual(
                details["thumbnail_url"], "https://cdn.example.test/cover.webp"
            )
            self.assertEqual(Path(details["thumbnail_path"]).name, "thumbnail.webp")
            self.assertEqual(captured_options[0]["format"], "bestvideo+bestaudio/best")
            self.assertEqual(captured_options[0]["ffmpeg_location"], "/app/bin/ffmpeg")


class ProjectThumbnailPersistenceTests(unittest.TestCase):
    @staticmethod
    def _create_legacy_projects_table(db_path: Path):
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '未命名项目',
                source_type TEXT NOT NULL DEFAULT 'youtube',
                source_url TEXT,
                video_path TEXT,
                audio_path TEXT,
                language TEXT DEFAULT 'auto',
                target_language TEXT DEFAULT 'zh',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def test_init_db_migrates_legacy_projects_with_thumbnail_columns(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "legacy.db"
            self._create_legacy_projects_table(db_path)

            with patch.object(database, "DB_PATH", db_path):
                database.init_db()
                conn = database.get_db()
                columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(projects)")
                }
                conn.close()

            self.assertIn("thumbnail_url", columns)
            self.assertIn("thumbnail_path", columns)

    def test_legacy_youtube_urls_get_a_thumbnail_fallback(self):
        video_id = "dQw4w9WgXcQ"
        expected = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        for source_url in (
            f"https://www.youtube.com/watch?v={video_id}&feature=shared",
            f"https://youtu.be/{video_id}?si=share-token",
            f"https://www.youtube.com/shorts/{video_id}",
            f"https://www.youtube.com/embed/{video_id}",
        ):
            with self.subTest(source_url=source_url):
                self.assertEqual(database._youtube_thumbnail_url(source_url), expected)

        self.assertIsNone(
            database._youtube_thumbnail_url(
                f"https://youtube.com.evil.test/watch?v={video_id}"
            )
        )

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT 'legacy-id' AS id, 'Legacy video' AS title,
                      'youtube' AS source_type, ? AS source_url,
                      NULL AS video_path, NULL AS audio_path,
                      NULL AS thumbnail_url, NULL AS thumbnail_path,
                      'auto' AS language, 'zh' AS target_language,
                      'now' AS created_at, 'now' AS updated_at""",
            (f"https://www.youtube.com/watch?v={video_id}",),
        ).fetchone()
        conn.close()
        self.assertEqual(database.project_to_dict(row)["thumbnail_url"], expected)

    def test_download_persists_cover_and_project_list_exposes_local_url(self):
        with tempfile.TemporaryDirectory() as folder:
            folder_path = Path(folder)
            db_path = folder_path / "projects.db"
            video_path = folder_path / "video.mp4"
            thumbnail_path = folder_path / "thumbnail.jpg"
            video_path.write_bytes(b"video")
            thumbnail_path.write_bytes(b"image")
            now = time.strftime("%Y-%m-%d %H:%M:%S")

            with patch.object(database, "DB_PATH", db_path):
                database.init_db()
                conn = database.get_db()
                conn.execute(
                    """INSERT INTO projects
                       (id, title, source_type, source_url, language, target_language,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "project-id", "Old title", "youtube", "https://old.example.test",
                        "auto", "zh", now, now,
                    ),
                )
                conn.commit()
                conn.close()

                task = {
                    "details": {
                        "title": "Downloaded title",
                        "thumbnail_url": "https://cdn.example.test/cover.jpg",
                        "thumbnail_path": str(thumbnail_path),
                    }
                }
                with (
                    patch.object(projects, "download_video", return_value=str(video_path)),
                    patch.object(projects.task_manager, "get_task", return_value=task),
                    patch.object(projects.task_manager, "update_task"),
                ):
                    projects._do_download(
                        "task-id", "project-id", "https://example.test/watch"
                    )

                listing = projects.list_projects()["projects"]
                detail = projects.get_project("project-id")
                conn = database.get_db()
                persisted = conn.execute(
                    "SELECT thumbnail_url, thumbnail_path FROM projects WHERE id = ?",
                    ("project-id",),
                ).fetchone()
                conn.close()

                self.assertEqual(
                    persisted["thumbnail_url"], "https://cdn.example.test/cover.jpg"
                )
                self.assertEqual(persisted["thumbnail_path"], str(thumbnail_path))
                self.assertEqual(
                    listing[0]["thumbnail_url"],
                    "/api/projects/project-id/thumbnail",
                )
                self.assertEqual(
                    detail["thumbnail_url"],
                    "/api/projects/project-id/thumbnail",
                )

                thumbnail_path.unlink()
                fallback = projects.get_project("project-id")
                self.assertEqual(
                    fallback["thumbnail_url"],
                    "https://cdn.example.test/cover.jpg",
                )


if __name__ == "__main__":
    unittest.main()
