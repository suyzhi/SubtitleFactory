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
if "SUBTITLE_FACTORY_DATA_DIR" not in os.environ:
    os.environ["SUBTITLE_FACTORY_DATA_DIR"] = tempfile.mkdtemp(
        prefix="subtitle-factory-video-tests-",
    )

from app.api import projects
from app.models import database
from app.services import downloader


class DownloadQualityTests(unittest.TestCase):
    def test_error_classifier_covers_stable_download_failure_model(self):
        cases = [
            ("Join this channel to get access to members-only content", "MEMBERSHIP_REQUIRED", False),
            ("ERROR: This is a private video", "PRIVATE_VIDEO", False),
            ("Sign in to confirm your age", "AGE_RESTRICTED", False),
            ("not available in your country", "GEO_RESTRICTED", False),
            ("This video has been removed because of copyright", "VIDEO_REMOVED", False),
            ("This video is DRM protected", "DRM_PROTECTED", False),
            ("HTTP Error 429: Too Many Requests", "RATE_LIMITED", False),
            ("could not copy Chrome cookie database", "COOKIE_ACCESS_FAILED", False),
            ("YouTube requires a PO Token", "PO_TOKEN_REQUIRED", False),
            ("HTTP Error 403: Forbidden", "MEDIA_ACCESS_DENIED", False),
            ("HTTP Error 503: Service Unavailable", "NETWORK_TEMPORARY", True),
            ("requested format is not available", "FORMAT_UNAVAILABLE", False),
            ("ffmpeg merger exited with code 1", "MERGE_FAILED", False),
            ("totally unknown extractor failure", "DOWNLOAD_FAILED", False),
        ]
        for message, expected_code, automatic_retry in cases:
            with self.subTest(message=message):
                failure = downloader._classify_download_error(Exception(message))
                self.assertEqual(failure.error_code, expected_code)
                self.assertEqual(failure.automatic_retry, automatic_retry)

        self.assertEqual(
            downloader._classify_download_error(OSError(28, "No space left")).error_code,
            "DISK_FULL",
        )
        self.assertEqual(
            downloader._classify_download_error(PermissionError(13, "Permission denied")).error_code,
            "OUTPUT_PERMISSION_DENIED",
        )

    def test_members_only_metadata_retries_once_with_chrome_and_then_succeeds(self):
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
                if "cookiesfrombrowser" not in self.options:
                    raise downloader.yt_dlp.utils.DownloadError(
                        "Join this channel to get access to members-only content"
                    )
                return {
                    "id": "LWq_LwsKLTI",
                    "title": "会员视频",
                    "duration": 2421,
                    "availability": "subscriber_only",
                }

        with (
            patch.object(downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL),
            patch.object(downloader.task_manager, "checkpoint"),
            patch.object(downloader.task_manager, "update_task"),
        ):
            info, details = downloader.extract_youtube_info(
                "https://www.youtube.com/watch?v=LWq_LwsKLTI",
                task_id="membership-test",
            )

        self.assertEqual(info["id"], "LWq_LwsKLTI")
        self.assertTrue(details["authenticated_attempted"])
        self.assertEqual([item["mode"] for item in details["attempts"]], ["anonymous", "chrome"])
        self.assertEqual(len(captured_options), 2)
        self.assertNotIn("cookiesfrombrowser", captured_options[0])
        self.assertEqual(captured_options[1]["cookiesfrombrowser"], ("chrome",))

    def test_public_metadata_never_reads_chrome_cookies(self):
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
                return {"id": "dQw4w9WgXcQ", "title": "Public", "duration": 213}

        with (
            patch.object(downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL),
            patch.object(downloader.task_manager, "checkpoint"),
        ):
            _, details = downloader.extract_youtube_info(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                task_id="public-test",
            )

        self.assertFalse(details["authenticated_attempted"])
        self.assertEqual(len(captured_options), 1)
        self.assertNotIn("cookiesfrombrowser", captured_options[0])

    def test_audio_only_download_uses_task_local_staging_without_video_merge(self):
        captured = {}

        class FakeYoutubeDL:
            def __init__(self, options):
                captured.update(options)
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download):
                source = Path(self.options["outtmpl"].replace("%(ext)s", "webm"))
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"audio")
                return {
                    "id": "dQw4w9WgXcQ",
                    "title": "Audio only",
                    "filepath": str(source),
                    "duration": 60,
                    "requested_downloads": [{"filepath": str(source)}],
                }

            def prepare_filename(self, info):
                return info["filepath"]

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(downloader, "DOWNLOADS_DIR", Path(folder)),
                patch.object(downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL),
                patch.object(downloader.task_manager, "update_task"),
            ):
                path = downloader.download_audio_source(
                    "task-id", "https://youtu.be/dQw4w9WgXcQ", "project-id",
                )
                self.assertTrue(Path(path).is_file())
                self.assertEqual(Path(path).parent.name, ".audio-task-id")

        self.assertEqual(captured["format"], "bestaudio/best")
        self.assertNotIn("merge_output_format", captured)
        self.assertNotIn("postprocessors", captured)

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
        self.assertEqual(options["retries"], 3)
        self.assertEqual(options["fragment_retries"], 5)
        self.assertEqual(options["extractor_retries"], 3)
        self.assertEqual(options["file_access_retries"], 3)
        self.assertEqual(options["socket_timeout"], 30)
        self.assertTrue(options["continuedl"])
        self.assertEqual(options["retry_sleep_functions"]["http"](n=8), 20)

    def test_media_stream_403_retries_once_with_chrome_cookies(self):
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
                if "cookiesfrombrowser" not in self.options:
                    raise downloader.yt_dlp.utils.DownloadError(
                        "ERROR: unable to download video data: HTTP Error 403: Forbidden"
                    )
                project_dir = Path(self.options["outtmpl"]["default"]).parent
                final_video = project_dir / "authenticated.mp4"
                final_video.write_bytes(b"authenticated-video")
                return {
                    "id": "video-id",
                    "title": "Authenticated video",
                    "filepath": str(final_video),
                }

            def prepare_filename(self, info):
                return info["filepath"]

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL),
                patch.object(
                    downloader,
                    "resolve_ffmpeg_path",
                    return_value=SimpleNamespace(path=Path("/app/bin/ffmpeg"), source="bundled"),
                ),
                patch.object(downloader.task_manager, "update_task"),
                patch.object(downloader.task_manager, "checkpoint"),
                patch.object(downloader, "_probe_media", return_value={
                    "duration": 60, "container": "mp4", "format_name": "mp4",
                    "video_codec": "av1", "audio_codec": "opus", "file_size": 19,
                }),
            ):
                result = downloader.download_video(
                    "task-id",
                    "https://www.youtube.com/watch?v=video-id",
                    "project-id",
                    download_dir=folder,
                )

        self.assertTrue(Path(result).name.startswith("video-video-id-"))
        self.assertFalse((Path(folder) / "project-id" / ".download-task-id").exists())
        self.assertEqual(len(captured_options), 2)
        self.assertNotIn("cookiesfrombrowser", captured_options[0])
        self.assertEqual(captured_options[1]["cookiesfrombrowser"], ("chrome",))

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
                patch.object(downloader, "_probe_media", return_value={
                    "duration": 60, "container": "mp4", "format_name": "mp4",
                    "video_codec": "av1", "audio_codec": "opus",
                    "file_size": len(b"merged-video-and-audio"),
                }),
            ):
                result = downloader.download_video(
                    "task-id", "https://example.test/watch", "project-id"
                )

            self.assertTrue(Path(result).name.startswith("video-video-id-"))
            self.assertEqual(Path(result).read_bytes(), b"merged-video-and-audio")
            details = next(
                call.kwargs["details"]
                for call in reversed(update_task.call_args_list)
                if "video_path" in call.kwargs.get("details", {})
            )
            self.assertEqual(details["video_path"], result)
            self.assertEqual(
                details["thumbnail_url"], "https://cdn.example.test/cover.webp"
            )
            self.assertTrue(Path(details["thumbnail_path"]).name.startswith("thumbnail-"))
            self.assertFalse((Path(folder) / "project-id" / ".download-task-id").exists())
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

    def test_database_commit_failure_keeps_old_media_and_removes_new_candidates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db_path = root / "projects.db"
            project_dir = root / "downloads" / "project-id"
            project_dir.mkdir(parents=True)
            old_video = project_dir / "old.mp4"
            old_thumbnail = project_dir / "old.webp"
            new_video = project_dir / "video-new.mp4"
            new_thumbnail = project_dir / "thumbnail-new.webp"
            for path, content in (
                (old_video, b"old-video"),
                (old_thumbnail, b"old-cover"),
                (new_video, b"new-video"),
                (new_thumbnail, b"new-cover"),
            ):
                path.write_bytes(content)
            now = time.strftime("%Y-%m-%d %H:%M:%S")

            with patch.object(database, "DB_PATH", db_path):
                database.init_db()
                conn = database.get_db()
                conn.execute(
                    """INSERT INTO projects
                       (id,title,source_type,source_url,video_path,thumbnail_path,
                        language,target_language,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "project-id", "Old", "youtube", "https://youtube.test/watch",
                        str(old_video), str(old_thumbnail), "auto", "zh", now, now,
                    ),
                )
                conn.commit()
                conn.close()

                real = database.get_db()

                class CommitFailingConnection:
                    def __getattr__(self, name):
                        return getattr(real, name)

                    def commit(self):
                        raise sqlite3.OperationalError("simulated commit failure")

                task = {
                    "details": {
                        "title": "New",
                        "thumbnail_path": str(new_thumbnail),
                        "thumbnail_url": "https://cdn.example.test/new.webp",
                    },
                }
                with (
                    patch.object(projects, "download_video", return_value=str(new_video)),
                    patch.object(projects, "get_app_settings", return_value={
                        "download_directory": str(root / "downloads"),
                    }),
                    patch.object(projects, "get_db", return_value=CommitFailingConnection()),
                    patch.object(projects.task_manager, "get_task", return_value=task),
                    patch.object(projects.task_manager, "checkpoint"),
                ):
                    with self.assertRaisesRegex(sqlite3.OperationalError, "commit failure"):
                        projects._do_download(
                            "task-id", "project-id", "https://youtube.test/watch",
                        )

                self.assertTrue(old_video.is_file())
                self.assertTrue(old_thumbnail.is_file())
                self.assertFalse(new_video.exists())
                self.assertFalse(new_thumbnail.exists())
                conn = database.get_db()
                row = conn.execute(
                    "SELECT video_path,thumbnail_path FROM projects WHERE id='project-id'"
                ).fetchone()
                conn.close()
                self.assertEqual(row["video_path"], str(old_video))
                self.assertEqual(row["thumbnail_path"], str(old_thumbnail))


if __name__ == "__main__":
    unittest.main()
