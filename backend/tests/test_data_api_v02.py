import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
if "SUBTITLE_FACTORY_DATA_DIR" not in os.environ:
    os.environ["SUBTITLE_FACTORY_DATA_DIR"] = tempfile.mkdtemp(
        prefix="subtitle-factory-data-api-tests-",
    )

from app.api import projects, settings
from app.models import database, migrations


class DatabaseMigrationTests(unittest.TestCase):
    def test_legacy_database_migrates_cleanly_with_pre_migration_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "legacy.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """CREATE TABLE projects (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, source_type TEXT NOT NULL,
                    source_url TEXT, video_path TEXT, audio_path TEXT,
                    language TEXT, target_language TEXT, created_at TEXT, updated_at TEXT
                )"""
            )
            conn.execute(
                "INSERT INTO projects VALUES ('legacy','Legacy','local',NULL,NULL,NULL,'auto','zh','old','old')"
            )
            conn.commit()
            conn.close()

            with patch.object(database, "DB_PATH", db_path):
                database.init_db()
                conn = database.get_db()
                tables = {
                    row["name"]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                version = conn.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
                provider_cols = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(ai_provider_configs)")
                }
                conn.close()

            self.assertEqual(version, migrations.CURRENT_SCHEMA_VERSION)
            self.assertIn("app_settings", tables)
            self.assertIn("ai_provider_configs", tables)
            self.assertIn("thinking_enabled", provider_cols)
            self.assertTrue(list((Path(folder) / "backups").glob("schema-v*.db")))

    def test_failed_migration_rolls_back_without_advancing_schema_version(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "rollback.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, title TEXT)")
            conn.execute("INSERT INTO projects VALUES ('p1', 'keep')")
            conn.commit()

            def broken_step(_conn):
                _conn.execute("INSERT INTO projects VALUES ('p2', 'transient')")
                raise RuntimeError("boom")

            with (
                patch.object(migrations, "MIGRATIONS", ((1, broken_step),)),
                self.assertRaisesRegex(RuntimeError, "boom"),
            ):
                migrations.run_migrations(conn, db_path)

            applied = [
                row[0] for row in conn.execute("SELECT version FROM schema_migrations")
            ]
            ids = [row[0] for row in conn.execute("SELECT id FROM projects")]
            conn.close()
            self.assertEqual(applied, [])
            self.assertEqual(ids, ["p1"])


class ProjectCRUDAPITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_patch = patch.object(database, "DB_PATH", root / "projects.db")
        self.db_patch.start()
        self.path_patches = [
            patch.object(projects, "DATA_DIR", root),
            patch.object(projects, "PROJECTS_DIR", root / "projects"),
            patch.object(projects, "DOWNLOADS_DIR", root / "downloads"),
            patch.object(projects, "AUDIO_DIR", root / "audio"),
            patch.object(projects, "SUBTITLES_DIR", root / "subtitles"),
            patch.object(projects, "EXPORTS_DIR", root / "exports"),
        ]
        for item in self.path_patches:
            item.start()
        for name in ("projects", "downloads", "audio", "subtitles", "exports"):
            (root / name).mkdir()
        database.init_db()
        app = FastAPI()
        app.include_router(projects.router)
        self.client = TestClient(app)
        self.root = root

    def tearDown(self):
        self.client.close()
        for item in reversed(self.path_patches):
            item.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def insert_project(self, project_id: str):
        db = database.get_db()
        db.execute(
            """INSERT INTO projects
               (id,title,source_type,language,target_language,created_at,updated_at)
               VALUES (?,?,'local','auto','zh','now','now')""",
            (project_id, project_id),
        )
        db.commit()
        db.close()

    def test_new_youtube_project_canonicalizes_url(self):
        created = self.client.post(
            "/api/projects",
            json={
                "source_type": "youtube",
                "source_url": "https://youtu.be/dQw4w9WgXcQ?t=110",
                "title": "YouTube project",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        project = self.client.get(
            f"/api/projects/{created.json()['project_id']}"
        ).json()
        self.assertEqual(project["youtube_video_id"], "dQw4w9WgXcQ")
        self.assertEqual(
            project["source_url"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )

    def test_delete_rejects_project_with_active_tasks(self):
        self.insert_project("busy")
        with patch.object(
            projects.task_manager, "active_task_ids", return_value=["task-1"]
        ):
            conflict = self.client.delete("/api/projects/busy?permanent=true")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"]["code"], "ACTIVE_TASKS")

    def test_permanent_delete_removes_managed_files_and_database_rows(self):
        self.insert_project("purge")
        project_dir = self.root / "projects" / "purge"
        download_dir = self.root / "downloads" / "purge"
        project_dir.mkdir()
        download_dir.mkdir()
        video = download_dir / "video.mp4"
        video.write_bytes(b"video")
        export = self.root / "exports" / "purge_subtitles.srt"
        export.write_text("subtitle", encoding="utf-8")
        db = database.get_db()
        db.execute("UPDATE projects SET video_path=? WHERE id='purge'", (str(video),))
        db.execute(
            """INSERT INTO tasks
               (id,project_id,type,status,created_at,updated_at)
               VALUES ('done','purge','workflow','success','now','now')"""
        )
        db.commit()
        db.close()

        deleted = self.client.delete("/api/projects/purge?permanent=true")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertFalse(project_dir.exists())
        self.assertFalse(download_dir.exists())
        self.assertFalse(export.exists())
        db = database.get_db()
        self.assertEqual(
            db.execute("SELECT COUNT(*) FROM projects WHERE id='purge'").fetchone()[0], 0
        )
        self.assertEqual(
            db.execute("SELECT COUNT(*) FROM tasks WHERE project_id='purge'").fetchone()[0],
            0,
        )
        db.close()

    def test_managed_path_guard_never_removes_a_storage_root(self):
        self.assertFalse(projects._path_is_managed(self.root))
        self.assertFalse(projects._path_is_managed(self.root / "projects"))
        self.assertFalse(
            projects._path_is_managed(self.root / "projects" / "escape" / "..")
        )
        self.assertTrue(
            projects._path_is_managed(self.root / "projects" / "other-project")
        )

    def test_model_catalog_lists_28_local_models_without_cloud_or_custom(self):
        response = self.client.get("/api/transcription/models")
        self.assertEqual(response.status_code, 200)
        models = response.json()["models"]
        ids = {item["id"] for item in models}
        self.assertEqual(len(models), 28)
        self.assertIn("small", ids)
        self.assertIn("parakeet-tdt-0.6b-v3-coreml", ids)
        self.assertNotIn("fun-asr-realtime", ids)
        self.assertNotIn("custom", ids)


class AIProviderSettingsAPITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            database, "DB_PATH", Path(self.temp_dir.name) / "settings.db"
        )
        self.db_patch.start()
        database.init_db()
        app = FastAPI()
        app.include_router(settings.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_provider_cards_save_and_assignments_persist(self):
        initial = self.client.get("/api/settings/ai/providers")
        self.assertEqual(initial.status_code, 200)
        payload = initial.json()
        self.assertEqual(
            payload["assignments"],
            {"clean_provider_id": "deepseek", "translate_provider_id": "deepseek"},
        )

        saved = self.client.put(
            "/api/settings/ai/providers/openai",
            json={
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4.1-mini",
                "api_key": "sk-test-123",
                "enabled": True,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["provider"]["has_api_key"])
        self.assertEqual(saved.json()["provider"]["api_key"], "")

        assigned = self.client.put(
            "/api/settings/ai/assignments",
            json={"clean_provider_id": "openai", "translate_provider_id": "deepseek"},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(
            assigned.json()["assignments"],
            {"clean_provider_id": "openai", "translate_provider_id": "deepseek"},
        )


if __name__ == "__main__":
    unittest.main()
