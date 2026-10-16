import os
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
if "SUBTITLE_FACTORY_DATA_DIR" not in os.environ:
    os.environ["SUBTITLE_FACTORY_DATA_DIR"] = tempfile.mkdtemp(
        prefix="subtitle-factory-editor-tests-",
    )

from app.api import editor, projects, quality
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
        app.include_router(quality.router)
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

    def add_segment(self, index, start, end, text, locked=0):
        db = database.get_db()
        db.execute(
            """INSERT INTO segments
               (id,project_id,idx,start,end,raw_text,clean_text,translated_text,
                speaker,locked,is_draft,source_stage)
               VALUES (?,?,?,?,?,?,?,'','',?,0,'final')""",
            (str(uuid.uuid4()), self.project_id, index, start, end, text, text, locked),
        )
        db.commit()
        db.close()

    def search_rows(self):
        db = database.get_db()
        try:
            return db.execute(
                "SELECT COUNT(*) FROM segment_search WHERE project_id=?", (self.project_id,)
            ).fetchone()[0]
        finally:
            db.close()

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

    def test_bulk_replace_inserts_replacement_text_literally(self):
        # ASS line breaks, Windows paths and regex-looking group references
        # must not be interpreted as re.sub escapes.
        replacement = r"C:\temp\Users\N\1\g<0>"
        response = self.operate(
            operation="replace", search="world", replacement=replacement,
            fields=["clean_text"], match_case=False,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["affected_count"], 1)
        self.assertEqual(self.segments()[0]["clean_text"], "Hello " + replacement)

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

    def test_stale_draft_requires_explicit_rebase_and_commit_clears_atomically(self):
        saved = self.client.put(
            f"/api/projects/{self.project_id}/draft",
            json={
                "base_revision": 0,
                "items": [{"index": 2, "clean_text": "Recovered draft"}],
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)

        official = self.operate(
            operation="update_many",
            items=[{"index": 1, "clean_text": "Official change"}],
        )
        self.assertEqual(official.status_code, 200, official.text)

        # Editing recovery data must remain durable even after the official
        # track moves on. It is stored as stale, never silently applied.
        updated_draft = self.client.put(
            f"/api/projects/{self.project_id}/draft",
            json={
                "base_revision": 0,
                "items": [{"index": 2, "clean_text": "Latest recovered draft"}],
            },
        )
        self.assertEqual(updated_draft.status_code, 200, updated_draft.text)

        stale = self.client.post(f"/api/projects/{self.project_id}/draft/commit")
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "EDIT_REVISION_CONFLICT")
        self.assertIsNotNone(
            self.client.get(f"/api/projects/{self.project_id}/draft").json()["draft"]
        )

        unconfirmed = self.client.post(
            f"/api/projects/{self.project_id}/draft/rebase", json={"confirm": False},
        )
        self.assertEqual(unconfirmed.status_code, 400, unconfirmed.text)
        rebased = self.client.post(
            f"/api/projects/{self.project_id}/draft/rebase", json={"confirm": True},
        )
        self.assertEqual(rebased.status_code, 200, rebased.text)
        self.assertEqual(
            [item["clean_text"] for item in self.segments()[:2]],
            ["Official change", "Latest recovered draft"],
        )
        self.assertIsNone(
            self.client.get(f"/api/projects/{self.project_id}/draft").json()["draft"]
        )

        undone = self.client.post(
            f"/api/projects/{self.project_id}/editor/undo",
            json={"expected_revision": self.revision()},
        )
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(self.segments()[1]["clean_text"], "Second line")

    def test_draft_commit_preserves_locked_or_corrupt_recovery_data(self):
        locked = self.client.put(
            f"/api/projects/{self.project_id}/draft",
            json={
                "base_revision": 0,
                "items": [{"index": 3, "clean_text": "Must not overwrite"}],
            },
        )
        self.assertEqual(locked.status_code, 200, locked.text)
        rejected = self.client.post(f"/api/projects/{self.project_id}/draft/commit")
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(rejected.json()["detail"]["code"], "DRAFT_LOCKED_CONFLICT")
        self.assertEqual(self.segments()[2]["clean_text"], "Locked world")
        self.assertIsNotNone(
            self.client.get(f"/api/projects/{self.project_id}/draft").json()["draft"]
        )

        explicitly_unlocked = self.client.put(
            f"/api/projects/{self.project_id}/draft",
            json={
                "base_revision": 0,
                "items": [{
                    "index": 3,
                    "clean_text": "Explicitly unlocked draft",
                    "locked": False,
                }],
            },
        )
        self.assertEqual(explicitly_unlocked.status_code, 200, explicitly_unlocked.text)
        committed = self.client.post(f"/api/projects/{self.project_id}/draft/commit")
        self.assertEqual(committed.status_code, 200, committed.text)
        self.assertEqual(self.segments()[2]["clean_text"], "Explicitly unlocked draft")
        self.assertFalse(self.segments()[2]["locked"])

        replacement = self.client.put(
            f"/api/projects/{self.project_id}/draft",
            json={
                "base_revision": self.revision(),
                "items": [{"index": 2, "clean_text": "Recovery remains intact"}],
            },
        )
        self.assertEqual(replacement.status_code, 200, replacement.text)

        db = database.get_db()
        db.execute(
            "UPDATE segment_drafts SET draft_json='{}' WHERE project_id=?",
            (self.project_id,),
        )
        db.commit()
        db.close()
        corrupt = self.client.post(f"/api/projects/{self.project_id}/draft/commit")
        self.assertEqual(corrupt.status_code, 422, corrupt.text)
        self.assertEqual(corrupt.json()["detail"]["code"], "DRAFT_INVALID")
        corrupt_draft = self.client.get(
            f"/api/projects/{self.project_id}/draft"
        ).json()["draft"]
        self.assertTrue(corrupt_draft["invalid"])
        self.assertEqual(corrupt_draft["items"], [])

    def test_delete_one_segment_renumbers_and_is_undoable(self):
        self.assertEqual(self.search_rows(), 3)
        deleted = self.operate(operation="delete", indices=[1])
        self.assertEqual(deleted.status_code, 200, deleted.text)
        body = deleted.json()
        self.assertEqual(body["operation"], "delete")
        self.assertEqual(body["affected_count"], 1)
        self.assertEqual(body["revision"], 1)
        self.assertEqual(
            [(item["index"], item["clean_text"]) for item in self.segments()],
            [(1, "Second line"), (2, "Locked world")],
        )
        self.assertTrue(self.segments()[1]["locked"])
        self.assertEqual(self.search_rows(), 2)

        undone = self.client.post(
            f"/api/projects/{self.project_id}/editor/undo",
            json={"expected_revision": self.revision()},
        )
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(
            [(item["index"], item["clean_text"]) for item in self.segments()],
            [(1, "Hello world"), (2, "Second line"), (3, "Locked world")],
        )
        self.assertEqual(self.search_rows(), 3)

        redone = self.client.post(
            f"/api/projects/{self.project_id}/editor/redo",
            json={"expected_revision": self.revision()},
        )
        self.assertEqual(redone.status_code, 200, redone.text)
        self.assertEqual(
            [item["clean_text"] for item in redone.json()["segments"]],
            ["Second line", "Locked world"],
        )

    def test_delete_several_non_adjacent_segments_in_one_operation(self):
        self.add_segment(4, 3.2, 4.0, "Fourth line")
        self.add_segment(5, 4.2, 5.0, "Fifth line")
        deleted = self.operate(operation="delete", indices=[4, 1, 4])
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["affected_count"], 2)
        self.assertEqual(
            [(item["index"], item["clean_text"], item["locked"]) for item in self.segments()],
            [(1, "Second line", False), (2, "Locked world", True), (3, "Fifth line", False)],
        )

        undone = self.client.post(
            f"/api/projects/{self.project_id}/editor/undo",
            json={"expected_revision": self.revision()},
        )
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(len(self.segments()), 5)

    def test_delete_refuses_locked_segments_unless_explicitly_included(self):
        refused = self.operate(operation="delete", indices=[2, 3])
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["detail"]["code"], "SEGMENT_LOCKED")
        # Atomic: the unlocked row in the same request is kept as well.
        self.assertEqual(len(self.segments()), 3)
        self.assertEqual(self.revision(), 0)

        allowed = self.operate(operation="delete", indices=[2, 3], include_locked=True)
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(
            [(item["index"], item["clean_text"]) for item in self.segments()],
            [(1, "Hello world")],
        )

    def test_delete_validates_selection_and_revision(self):
        empty = self.operate(operation="delete", indices=[])
        self.assertEqual(empty.status_code, 422, empty.text)
        self.assertEqual(empty.json()["detail"]["code"], "SEGMENTS_REQUIRED")

        missing = self.operate(operation="delete", indices=[2, 9])
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["detail"]["code"], "SEGMENT_NOT_FOUND")
        self.assertEqual(len(self.segments()), 3)

        stale = self.operate(operation="delete", indices=[1], expected_revision=5)
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "EDIT_REVISION_CONFLICT")
        self.assertEqual(len(self.segments()), 3)
        self.assertEqual(self.revision(), 0)

    def test_quality_fix_previews_and_deletes_empty_subtitle(self):
        db = database.get_db()
        db.execute(
            "UPDATE segments SET raw_text='',clean_text='' WHERE project_id=? AND idx=2",
            (self.project_id,),
        )
        db.commit()
        db.close()

        def empty_text_issue():
            scanned = self.client.post(
                f"/api/projects/{self.project_id}/quality/scan", json={"rules": {}},
            )
            self.assertEqual(scanned.status_code, 200, scanned.text)
            matches = [item for item in scanned.json()["issues"] if item["rule_id"] == "empty_text"]
            self.assertEqual([item["segment_index"] for item in matches], [2])
            return matches[0]["id"]

        issue_id = empty_text_issue()
        fix_url = f"/api/projects/{self.project_id}/quality/issues/{issue_id}/fix"
        preview = self.client.post(fix_url, json={"expected_revision": 0, "apply": False})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertFalse(preview.json()["applied"])
        self.assertEqual(preview.json()["operation"]["operation"], "delete")
        self.assertEqual(preview.json()["operation"]["indices"], [2])
        self.assertEqual(len(self.segments()), 3)
        self.assertEqual(self.revision(), 0)

        applied = self.client.post(fix_url, json={"expected_revision": 0, "apply": True})
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertTrue(applied.json()["applied"])
        self.assertEqual(
            [(item["index"], item["clean_text"]) for item in applied.json()["editor"]["segments"]],
            [(1, "Hello world"), (2, "Locked world")],
        )
        remaining = self.client.get(
            f"/api/projects/{self.project_id}/quality/issues", params={"status": "all"},
        ).json()["issues"]
        self.assertNotIn("empty_text", {item["rule_id"] for item in remaining})

        undone = self.client.post(
            f"/api/projects/{self.project_id}/editor/undo",
            json={"expected_revision": self.revision()},
        )
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertEqual(self.segments()[1]["clean_text"], "")

        # A stale issue must never delete a subtitle that has been filled in.
        issue_id = empty_text_issue()
        filled = self.operate(
            operation="update_many", items=[{"index": 2, "clean_text": "Filled in"}],
        )
        self.assertEqual(filled.status_code, 200, filled.text)
        stale = self.client.post(
            f"/api/projects/{self.project_id}/quality/issues/{issue_id}/fix",
            json={"expected_revision": self.revision(), "apply": True},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "QUALITY_ISSUE_STALE")
        self.assertEqual(len(self.segments()), 3)


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

    def test_concurrent_initialization_is_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "startup.db"
            with patch.object(database, "DB_PATH", db_path):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(lambda _: database.init_db(), range(16)))
                db = database.get_db()
                provider_count = db.execute(
                    "SELECT COUNT(*) FROM ai_provider_configs"
                ).fetchone()[0]
                version = db.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
                db.close()
            self.assertLessEqual(provider_count, 1)
            self.assertEqual(version, migrations.CURRENT_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
