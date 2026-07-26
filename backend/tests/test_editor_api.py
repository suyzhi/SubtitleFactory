import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault(
    "SUBTITLE_FACTORY_DATA_DIR",
    tempfile.mkdtemp(prefix="subtitle-factory-editor-tests-"),
)

from app.api import editor, projects
from app.models import database, migrations


class EditorAPITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "editor.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()
        app = FastAPI()
        app.include_router(projects.router)
        app.include_router(editor.router)
        self.client = TestClient(app)
        self.project_id = "editor-project"
        db = database.get_db()
        db.execute(
            """INSERT INTO projects
               (id,title,source_type,language,target_language,created_at,updated_at)
               VALUES (?, 'Editor', 'local', 'en', 'zh', 'now', 'now')""",
            (self.project_id,),
        )
        for index, (start, end, text, locked) in enumerate([
            (0.0, 1.0, "Hello world", 0),
            (1.2, 2.0, "Second line", 0),
            (2.2, 3.0, "Locked world", 1),
        ], 1):
            db.execute(
                """INSERT INTO segments
                   (id,project_id,idx,start,end,raw_text,clean_text,translated_text,
                    speaker,locked,is_draft,source_stage)
                   VALUES (?,?,?,?,?,?,?,?,?,?,0,'final')""",
                (str(uuid.uuid4()), self.project_id, index, start, end, text, text, "", "", locked),
            )
        db.commit()
        db.close()

    def tearDown(self):
        self.client.close()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def operate(self, **payload):
        payload.setdefault("expected_revision", self.revision())
        return self.client.post(
            f"/api/projects/{self.project_id}/segment-operations", json=payload
        )

    def revision(self):
        return self.client.get(f"/api/projects/{self.project_id}").json()["edit_revision"]

    def segments(self):
        return self.client.get(f"/api/projects/{self.project_id}/segments").json()["segments"]

    def test_bulk_replace_is_atomic_skips_locked_and_detects_conflicts(self):
        response = self.operate(
            operation="replace", search="world", replacement="there",
            fields=["clean_text"], match_case=True,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["affected_count"], 1)
        self.assertEqual(self.segments()[0]["clean_text"], "Hello there")
        self.assertEqual(self.segments()[2]["clean_text"], "Locked world")

        conflict = self.client.post(
            f"/api/projects/{self.project_id}/segment-operations",
            json={
                "expected_revision": 0, "operation": "replace",
                "search": "Second", "replacement": "Another",
            },
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"]["code"], "EDIT_REVISION_CONFLICT")

    def test_split_merge_and_persistent_undo_redo(self):
        split = self.operate(
            operation="split", split_index=1, split_at=0.5, text_offset=5,
        )
        self.assertEqual(split.status_code, 200, split.text)
        self.assertEqual(len(split.json()["segments"]), 4)
        self.assertEqual(split.json()["segments"][0]["clean_text"], "Hello")

        merge = self.operate(operation="merge", indices=[1, 2])
        self.assertEqual(merge.status_code, 200, merge.text)
        self.assertEqual(len(merge.json()["segments"]), 3)

        undone = self.client.post(
            f"/api/projects/{self.project_id}/editor/undo",
            json={"expected_revision": self.revision()},
        )
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(len(undone.json()["segments"]), 4)

        redone = self.client.post(
            f"/api/projects/{self.project_id}/editor/redo",
            json={"expected_revision": self.revision()},
        )
        self.assertEqual(redone.status_code, 200, redone.text)
        self.assertEqual(len(redone.json()["segments"]), 3)

    def test_invalid_timing_rolls_back_and_draft_commit_is_one_operation(self):
        invalid = self.operate(
            operation="update_many",
            items=[{"index": 2, "start": 0.5}],
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["detail"]["code"], "SEGMENT_OVERLAP")
        self.assertEqual(self.revision(), 0)

        saved = self.client.put(
            f"/api/projects/{self.project_id}/draft",
            json={
                "base_revision": 0,
                "items": [
                    {"index": 1, "clean_text": "Draft one"},
                    {"index": 2, "clean_text": "Draft two"},
                ],
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.segments()[0]["clean_text"], "Hello world")
        committed = self.client.post(f"/api/projects/{self.project_id}/draft/commit")
        self.assertEqual(committed.status_code, 200, committed.text)
        self.assertEqual(committed.json()["affected_count"], 2)
        self.assertEqual([item["clean_text"] for item in self.segments()[:2]], ["Draft one", "Draft two"])
        self.assertIsNone(
            self.client.get(f"/api/projects/{self.project_id}/draft").json()["draft"]
        )


class VersionedMigrationTests(unittest.TestCase):
    def test_migrations_are_idempotent_and_create_safety_backups(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "legacy.db"
            with patch.object(database, "DB_PATH", db_path):
                database.init_db()
                db = database.get_db()
                db.execute("DELETE FROM schema_migrations")
                db.commit()
                db.close()
                database.init_db()
                database.init_db()
                db = database.get_db()
                version = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
                operations = db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='edit_operations'"
                ).fetchone()
                db.close()
            self.assertEqual(version, migrations.CURRENT_SCHEMA_VERSION)
            self.assertIsNotNone(operations)
            self.assertTrue(list((Path(folder) / "backups").glob("schema-v1-*.db")))


if __name__ == "__main__":
    unittest.main()
