import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("SUBTITLE_FACTORY_DATA_DIR", tempfile.mkdtemp(prefix="subtitle-factory-playlist-tests-"))

from app.api import batches, projects
from app.models import database, migrations
from app.services import playlist_batches
from app.utils.task_manager import task_manager

REAL_DISPATCH_BATCH = playlist_batches._dispatch_batch


def playlist_fixture(ids=("aaaaaaaaaaa", "bbbbbbbbbbb")):
    return {
        "playlist": {
            "id": "PL-test", "title": "Test playlist",
            "url": "https://www.youtube.com/playlist?list=PL-test",
            "channel": "Test channel", "thumbnail_url": "https://example.test/playlist.jpg",
            "item_count": len(ids), "available_count": len(ids), "unavailable_count": 0,
            "total_duration": len(ids) * 60,
        },
        "items": [{
            "source_id": video_id, "video_id": video_id, "position": index,
            "title": f"Video {index}", "url": f"https://www.youtube.com/watch?v={video_id}",
            "duration": 60.0, "thumbnail_url": f"https://example.test/{video_id}.jpg",
            "availability": "active",
        } for index, video_id in enumerate(ids, 1)],
        "warnings": [],
    }


class PlaylistBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            patch.object(database, "DB_PATH", self.root / "factory.db"),
            patch.object(playlist_batches, "PROJECTS_DIR", self.root / "projects"),
            patch.object(projects, "DATA_DIR", self.root),
            patch.object(projects, "PROJECTS_DIR", self.root / "projects"),
            patch.object(projects, "DOWNLOADS_DIR", self.root / "downloads"),
            patch.object(projects, "AUDIO_DIR", self.root / "audio"),
            patch.object(projects, "SUBTITLES_DIR", self.root / "subtitles"),
            patch.object(projects, "EXPORTS_DIR", self.root / "exports"),
            patch.object(playlist_batches, "_dispatch_batch"),
        ]
        for item in self.patches:
            item.start()
        database.init_db()
        app = FastAPI()
        app.include_router(projects.router)
        app.include_router(batches.router)
        self.client = TestClient(app)
        self.configuration = {
            "model": "small", "runtime": "cpu", "language": "en", "target_language": "zh",
            "clean_target_length": 42,
            "stages": {"transcribe": True, "clean": False, "translate": False},
        }

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_v8_schema_is_migrated_and_local_batch_defaults_remain(self):
        db = database.get_db()
        busy_timeout = db.execute("PRAGMA busy_timeout").fetchone()[0]
        version = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        batch_columns = {row["name"] for row in db.execute("PRAGMA table_info(batches)")}
        stage_table = db.execute("SELECT name FROM sqlite_master WHERE name='batch_item_stages'").fetchone()
        db.close()
        self.assertEqual(version, migrations.CURRENT_SCHEMA_VERSION)
        self.assertEqual(version, 8)
        self.assertEqual(busy_timeout, 30000)
        self.assertIn("source_external_id", batch_columns)
        self.assertIsNotNone(stage_table)

    def test_task_memory_updates_do_not_deadlock_behind_stage_writer(self):
        task_id = task_manager.create_task(None, "test")
        writer = database.get_db()
        writer.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        try:
            task_manager.update_task(task_id, progress=25, message="still responsive")
        finally:
            writer.rollback()
            writer.close()
        # CI macOS runners can occasionally spend just over one second in
        # SQLite connection setup.  Two seconds still distinguishes the fixed
        # best-effort persistence path from the former 30-second lock wait.
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(task_manager.get_task(task_id)["progress"], 25)

    def test_successful_retry_resolves_old_failed_task_center_entries(self):
        created = playlist_batches.create_or_sync_playlist(
            playlist_fixture(("aaaaaaaaaaa",)), self.configuration,
        )
        db = database.get_db()
        item = db.execute(
            "SELECT id,project_id FROM batch_items WHERE batch_id=?",
            (created["batch_id"],),
        ).fetchone()
        db.execute(
            """INSERT INTO tasks(id,project_id,type,status,error,error_code,details,logs,
                                 available_actions,created_at,updated_at)
               VALUES (?,?,?,'failed','temporary failure','DOWNLOAD_FAILED',?,'[]','[\"retry\"]','now','now')""",
            ("old-attempt", item["project_id"], "download", json.dumps({"batch_item_id": item["id"]})),
        )
        db.execute(
            """INSERT INTO tasks(id,project_id,type,status,error,error_code,details,logs,
                                 available_actions,created_at,updated_at)
               VALUES (?,?,?,'failed','unrelated failure','DOWNLOAD_FAILED','{}','[]','[\"retry\"]','now','now')""",
            ("unrelated-attempt", item["project_id"], "download"),
        )
        db.execute(
            """INSERT INTO transcription_runs(id,project_id,task_id,model,status,started_at)
               VALUES ('stale-run',?,'stale-task','small','running','now')""",
            (item["project_id"],),
        )
        db.execute(
            """INSERT INTO transcription_segments(id,run_id,project_id,idx,start,end,text)
               VALUES ('stale-segment','stale-run',?,1,0,1,'draft')""",
            (item["project_id"],),
        )
        db.commit()
        db.close()

        resolved = playlist_batches._resolve_previous_stage_attempts(
            item["project_id"], item["id"], "download", "current-attempt",
        )

        db = database.get_db()
        old = db.execute("SELECT * FROM tasks WHERE id='old-attempt'").fetchone()
        unrelated = db.execute("SELECT * FROM tasks WHERE id='unrelated-attempt'").fetchone()
        db.close()
        self.assertEqual(resolved, 1)
        self.assertEqual(old["status"], "success")
        self.assertIsNone(old["error"])
        self.assertEqual(old["message"], "后续重试已成功完成")
        self.assertEqual(unrelated["status"], "failed")
        removed = playlist_batches._resolve_previous_stage_attempts(
            item["project_id"], item["id"], "transcribe", "current-transcribe",
        )
        db = database.get_db()
        self.assertIsNone(db.execute("SELECT 1 FROM transcription_runs WHERE id='stale-run'").fetchone())
        self.assertIsNone(db.execute("SELECT 1 FROM transcription_segments WHERE id='stale-segment'").fetchone())
        db.close()
        self.assertEqual(removed, 1)

    def test_create_hides_children_from_default_projects_and_preserves_detail(self):
        fixture = playlist_fixture()
        with patch.object(batches, "preview_playlist", return_value=fixture):
            response = self.client.post("/api/batches/playlist", json={
                "url": fixture["playlist"]["url"], "configuration": self.configuration,
            })
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["action"], "created")
        self.assertEqual(payload["added_count"], 2)

        self.assertEqual(self.client.get("/api/projects").json()["projects"], [])
        included = self.client.get("/api/projects?include_playlist_items=true").json()["projects"]
        self.assertEqual(len(included), 2)
        detail = self.client.get(f"/api/projects/{included[0]['id']}")
        self.assertEqual(detail.status_code, 200)

        batch = self.client.get(f"/api/batches/{payload['batch_id']}").json()
        self.assertEqual([item["position"] for item in batch["items"]], [1, 2])
        stages = batch["items"][0]["stages"]
        self.assertEqual(stages["download"]["status"], "waiting")
        self.assertEqual(stages["transcribe"]["status"], "waiting")
        self.assertEqual(stages["clean"]["status"], "skipped")

    def test_sync_only_adds_new_videos_and_marks_removed_items(self):
        first = playlist_fixture()
        created = playlist_batches.create_or_sync_playlist(first, self.configuration)
        second = playlist_fixture(("bbbbbbbbbbb", "ccccccccccc"))
        synced = playlist_batches.create_or_sync_playlist(second, self.configuration)
        self.assertEqual(synced["action"], "synced")
        self.assertEqual(synced["batch_id"], created["batch_id"])
        self.assertEqual(synced["added_count"], 1)

        detail = playlist_batches.get_batch_detail(created["batch_id"])
        by_id = {item["source_id"]: item for item in detail["items"]}
        self.assertEqual(by_id["aaaaaaaaaaa"]["source_state"], "removed")
        self.assertEqual(by_id["bbbbbbbbbbb"]["project_id"], detail["items"][1]["project_id"])
        self.assertEqual(by_id["ccccccccccc"]["source_state"], "active")
        self.assertEqual(len(by_id), 3)
        self.assertEqual(self.client.get("/api/projects").json()["projects"], [])

    def test_delete_playlist_purges_children_and_managed_files_only(self):
        created = playlist_batches.create_or_sync_playlist(playlist_fixture(), self.configuration)
        batch_id = created["batch_id"]
        db = database.get_db()
        rows = db.execute(
            """SELECT p.* FROM projects p JOIN batch_items i ON i.project_id=p.id
               WHERE i.batch_id=? ORDER BY i.position""",
            (batch_id,),
        ).fetchall()
        unrelated_id = "unrelated-project"
        unrelated_dir = self.root / "downloads" / unrelated_id
        unrelated_dir.mkdir(parents=True)
        unrelated_file = unrelated_dir / "keep.mp4"
        unrelated_file.write_bytes(b"keep")
        db.execute(
            """INSERT INTO projects(id,title,source_type,video_path,created_at,updated_at)
               VALUES (?,?, 'local',?,?,?)""",
            (unrelated_id, "Keep me", str(unrelated_file), "now", "now"),
        )
        for row in rows:
            video_dir = self.root / "downloads" / row["id"]
            audio_dir = self.root / "audio" / row["id"]
            video_dir.mkdir(parents=True)
            audio_dir.mkdir(parents=True)
            video = video_dir / "video.mp4"
            audio = audio_dir / "audio.wav"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            db.execute(
                "UPDATE projects SET video_path=?,audio_path=? WHERE id=?",
                (str(video), str(audio), row["id"]),
            )
        db.commit()
        db.close()

        required = self.client.delete(f"/api/batches/{batch_id}")
        self.assertEqual(required.status_code, 400)
        deleted = self.client.delete(
            f"/api/batches/{batch_id}?confirm=true&terminate=true"
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["deleted_projects"], 2)

        db = database.get_db()
        self.assertIsNone(db.execute("SELECT 1 FROM batches WHERE id=?", (batch_id,)).fetchone())
        for row in rows:
            self.assertIsNone(db.execute("SELECT 1 FROM projects WHERE id=?", (row["id"],)).fetchone())
            self.assertFalse((self.root / "downloads" / row["id"]).exists())
            self.assertFalse((self.root / "audio" / row["id"]).exists())
        self.assertIsNotNone(db.execute("SELECT 1 FROM projects WHERE id=?", (unrelated_id,)).fetchone())
        db.close()
        self.assertTrue(unrelated_file.exists())

    def test_bulk_clean_enables_transcription_dependency_without_repeating_success(self):
        created = playlist_batches.create_or_sync_playlist(playlist_fixture(("aaaaaaaaaaa",)), self.configuration)
        batch_id = created["batch_id"]
        db = database.get_db()
        item_id = db.execute("SELECT id FROM batch_items WHERE batch_id=?", (batch_id,)).fetchone()[0]
        db.execute("UPDATE batch_item_stages SET status='success' WHERE item_id=? AND stage IN ('download','extract_audio')", (item_id,))
        db.commit(); db.close()

        with patch.object(playlist_batches, "assigned_provider", return_value={"api_key": "test-key"}):
            playlist_batches.enable_batch_stage(batch_id, "clean", {"clean_target_length": 48, "ai_authorized": True})
        detail = playlist_batches.get_batch_detail(batch_id)["items"][0]["stages"]
        self.assertEqual(detail["download"]["status"], "success")
        self.assertEqual(detail["transcribe"]["status"], "waiting")
        self.assertEqual(detail["clean"]["status"], "waiting")
        self.assertEqual(detail["translate"]["status"], "skipped")

    def test_preview_deduplicates_and_retains_unavailable_entries(self):
        class FakeYDL:
            def __init__(self, _options): pass
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def extract_info(self, _url, download=False):
                self.download = download
                return {"_type": "playlist", "id": "PL-test", "title": "Playlist", "channel": "Channel", "entries": [
                    {"id": "aaaaaaaaaaa", "title": "First", "duration": 60},
                    {"id": "aaaaaaaaaaa", "title": "Duplicate", "duration": 60},
                    None,
                ]}
        with patch.object(playlist_batches.yt_dlp, "YoutubeDL", FakeYDL):
            result = playlist_batches.preview_playlist("https://www.youtube.com/playlist?list=PL-test")
        self.assertEqual(result["playlist"]["item_count"], 2)
        self.assertEqual(result["playlist"]["unavailable_count"], 1)
        self.assertEqual(result["items"][0]["position"], 1)
        self.assertEqual(result["items"][1]["availability"], "unavailable")

    def test_dispatch_submits_only_the_available_io_capacity(self):
        created = playlist_batches.create_or_sync_playlist(
            playlist_fixture(("aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc")), self.configuration,
        )
        submitted = []
        with patch.object(playlist_batches, "_queue_stage", side_effect=lambda item_id, stage: submitted.append((item_id, stage)) or "task"):
            REAL_DISPATCH_BATCH(created["batch_id"])
        self.assertEqual(len(submitted), 2)
        self.assertEqual([stage for _, stage in submitted], ["download", "download"])

    def test_ai_stages_require_explicit_authorization(self):
        configuration = {**self.configuration, "stages": {"transcribe": True, "clean": True, "translate": False}}
        with self.assertRaisesRegex(playlist_batches.PlaylistBatchError, "明确确认"):
            playlist_batches.create_or_sync_playlist(playlist_fixture(("aaaaaaaaaaa",)), configuration)


if __name__ == "__main__":
    unittest.main()
