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
os.environ.setdefault(
    "SUBTITLE_FACTORY_DATA_DIR",
    tempfile.mkdtemp(prefix="subtitle-factory-data-api-tests-"),
)

from app.api import projects, settings
from app.models import database


class V02DatabaseMigrationTests(unittest.TestCase):
    def test_legacy_database_gets_deleted_at_and_local_app_settings(self):
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
                columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(projects)")
                }
                app_settings_table = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
                ).fetchone()
                row = conn.execute("SELECT deleted_at FROM projects").fetchone()
                conn.close()
            self.assertIn("deleted_at", columns)
            self.assertIsNotNone(app_settings_table)
            self.assertIsNone(row["deleted_at"])


class ProjectTrashAPITests(unittest.TestCase):
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

    def insert_project(self, project_id: str, deleted_at=None):
        db = database.get_db()
        db.execute(
            """INSERT INTO projects
               (id,title,source_type,language,target_language,created_at,updated_at,deleted_at)
               VALUES (?,?,'local','auto','zh','now','now',?)""",
            (project_id, project_id, deleted_at),
        )
        db.commit()
        db.close()

    def test_default_list_filters_trash_and_restore_round_trip(self):
        self.insert_project("active")
        self.insert_project("deleted", "2026-01-01 00:00:00")
        self.assertEqual(
            [item["id"] for item in self.client.get("/api/projects").json()["projects"]],
            ["active"],
        )
        self.assertEqual(
            [item["id"] for item in self.client.get("/api/projects?deleted=true").json()["projects"]],
            ["deleted"],
        )
        trashed = self.client.post("/api/projects/active/trash")
        self.assertEqual(trashed.status_code, 200)
        self.assertIsNotNone(trashed.json()["project"]["deleted_at"])
        restored = self.client.post("/api/projects/active/restore")
        self.assertEqual(restored.status_code, 200)
        self.assertIsNone(restored.json()["project"]["deleted_at"])

    def test_active_task_requires_termination_confirmation(self):
        self.insert_project("busy")
        with patch.object(projects.task_manager, "active_task_ids", return_value=["task-1"]), patch.object(
            projects.task_manager, "cancel_project_tasks", return_value=["task-1"]
        ) as cancel:
            conflict = self.client.post("/api/projects/busy/trash")
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.json()["detail"]["code"], "ACTIVE_TASKS")
            accepted = self.client.post("/api/projects/busy/trash?terminate=true")
            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(accepted.json()["terminated_task_ids"], ["task-1"])
            cancel.assert_called_once_with("busy")

    def test_permanent_delete_removes_managed_files_and_database_rows(self):
        self.insert_project("purge", "2026-01-01 00:00:00")
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
               VALUES ('done','purge','export','success','now','now')"""
        )
        db.commit()
        db.close()
        deleted = self.client.delete("/api/projects/purge?permanent=true")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertFalse(download_dir.exists())
        self.assertFalse(export.exists())
        db = database.get_db()
        self.assertEqual(db.execute("SELECT COUNT(*) FROM projects WHERE id='purge'").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks WHERE project_id='purge'").fetchone()[0], 0)
        db.close()

    def test_rename_and_confirmed_empty_trash(self):
        self.insert_project("rename")
        renamed = self.client.patch("/api/projects/rename", json={"title": "  New title  "})
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["title"], "New title")
        self.client.post("/api/projects/rename/trash")
        self.assertEqual(self.client.delete("/api/projects/trash").status_code, 400)
        emptied = self.client.delete("/api/projects/trash?confirm=true")
        self.assertEqual(emptied.status_code, 200)
        self.assertEqual(emptied.json()["deleted_count"], 1)

    def test_model_validation_matches_model_list_contract(self):
        response = self.client.get("/api/transcription/models/small/validate")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        for key in (
            "id", "name", "ready", "download_required", "runtime_error",
            "languages", "source", "status",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["id"], "small")


class AppSettingsAPITests(unittest.TestCase):
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

    def test_defaults_are_safe_and_partial_updates_persist(self):
        initial = self.client.get("/api/settings/app").json()
        self.assertEqual(initial["settings"]["default_model"], "small")
        self.assertIsNone(initial["settings"]["coreml_model_path"])
        updated = self.client.put(
            "/api/settings/app",
            json={"source_language": "vi", "translation_target_language": "uk"},
        )
        self.assertEqual(updated.status_code, 200)
        reloaded = self.client.get("/api/settings/app").json()["settings"]
        self.assertEqual(reloaded["source_language"], "vi")
        self.assertEqual(reloaded["translation_target_language"], "uk")

    def test_invalid_model_path_falls_back_and_secret_fields_are_rejected(self):
        response = self.client.put(
            "/api/settings/app",
            json={
                "default_model": "parakeet-tdt-0.6b-v3-coreml",
                "coreml_model_path": "/definitely/missing/model",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["settings"]["default_model"], "small")
        self.assertIsNone(payload["settings"]["coreml_model_path"])
        self.assertTrue(payload["warnings"])
        self.assertEqual(
            self.client.put("/api/settings/app", json={"api_key": "secret"}).status_code,
            422,
        )

    def test_path_validation_and_home_path_redaction(self):
        executable = Path(self.temp_dir.name) / "tool"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        valid = self.client.post(
            "/api/settings/app/validate-path",
            json={"kind": "cli", "path": str(executable)},
        )
        self.assertEqual(valid.status_code, 200)
        self.assertTrue(valid.json()["ok"])
        redacted = self.client.post(
            "/api/settings/app/validate-path",
            json={"kind": "model", "path": str(Path.home() / "private-model")},
        ).json()
        self.assertTrue(redacted["resolved_path"].startswith("~/"))


if __name__ == "__main__":
    unittest.main()
