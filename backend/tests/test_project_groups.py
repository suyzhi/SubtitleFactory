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
    tempfile.mkdtemp(prefix="subtitle-factory-project-group-tests-"),
)

from app.api import projects
from app.models import database
from app.models.schemas import ProjectResponse


class ProjectGroupMigrationTests(unittest.TestCase):
    def test_init_db_adds_nullable_group_name_to_legacy_projects(self):
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "legacy.db"
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
            conn.execute(
                """INSERT INTO projects
                   (id, title, source_type, language, target_language, created_at, updated_at)
                   VALUES ('legacy-id', 'Legacy', 'youtube', 'auto', 'zh', 'old', 'old')"""
            )
            conn.commit()
            conn.close()

            with patch.object(database, "DB_PATH", db_path):
                database.init_db()
                conn = database.get_db()
                columns = {
                    row["name"]: row
                    for row in conn.execute("PRAGMA table_info(projects)")
                }
                row = conn.execute(
                    "SELECT group_name FROM projects WHERE id = 'legacy-id'"
                ).fetchone()
                conn.close()

            self.assertIn("group_name", columns)
            self.assertFalse(columns["group_name"]["notnull"])
            self.assertIsNone(row["group_name"])


class ProjectGroupAPITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "projects.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()

        app = FastAPI()
        app.include_router(projects.router)
        self.client = TestClient(app)
        self._insert_project()

    def tearDown(self):
        self.client.close()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def _insert_project(project_id: str = "project-id"):
        conn = database.get_db()
        conn.execute(
            """INSERT INTO projects
               (id, title, source_type, language, target_language, created_at, updated_at)
               VALUES (?, 'Video', 'youtube', 'auto', 'zh', '2000-01-01 00:00:00',
                       '2000-01-01 00:00:00')""",
            (project_id,),
        )
        conn.commit()
        conn.close()

    def test_patch_trims_persists_and_exposes_group_name(self):
        response = self.client.patch(
            "/api/projects/project-id/group",
            json={"group_name": "  学习资料  "},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["group_name"], "学习资料")
        self.assertEqual(ProjectResponse.model_validate(payload).group_name, "学习资料")

        conn = database.get_db()
        row = conn.execute(
            "SELECT group_name, updated_at FROM projects WHERE id = ?",
            ("project-id",),
        ).fetchone()
        conn.close()
        self.assertEqual(row["group_name"], "学习资料")
        self.assertNotEqual(row["updated_at"], "2000-01-01 00:00:00")

        self.assertEqual(
            self.client.get("/api/projects").json()["projects"][0]["group_name"],
            "学习资料",
        )
        self.assertEqual(
            self.client.get("/api/projects/project-id").json()["group_name"],
            "学习资料",
        )

    def test_empty_values_clear_group(self):
        values = [None, "", "  \n\t  "]
        for value in values:
            with self.subTest(value=value):
                conn = database.get_db()
                conn.execute(
                    "UPDATE projects SET group_name = '待清空' WHERE id = 'project-id'"
                )
                conn.commit()
                conn.close()

                response = self.client.patch(
                    "/api/projects/project-id/group",
                    json={"group_name": value},
                )
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json()["group_name"])

                conn = database.get_db()
                stored = conn.execute(
                    "SELECT group_name FROM projects WHERE id = 'project-id'"
                ).fetchone()["group_name"]
                conn.close()
                self.assertIsNone(stored)

    def test_group_name_validation_runs_after_trimming(self):
        accepted = self.client.patch(
            "/api/projects/project-id/group",
            json={"group_name": f"  {'a' * 40}  "},
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["group_name"], "a" * 40)

        too_long = self.client.patch(
            "/api/projects/project-id/group",
            json={"group_name": "a" * 41},
        )
        missing = self.client.patch(
            "/api/projects/project-id/group",
            json={},
        )
        wrong_type = self.client.patch(
            "/api/projects/project-id/group",
            json={"group_name": ["not", "a", "string"]},
        )
        self.assertEqual(too_long.status_code, 422)
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(wrong_type.status_code, 422)

        conn = database.get_db()
        stored = conn.execute(
            "SELECT group_name FROM projects WHERE id = 'project-id'"
        ).fetchone()["group_name"]
        conn.close()
        self.assertEqual(stored, "a" * 40)

    def test_patch_returns_404_for_unknown_project(self):
        response = self.client.patch(
            "/api/projects/missing/group",
            json={"group_name": "分组"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "项目不存在")
        conn = database.get_db()
        count = conn.execute(
            "SELECT COUNT(*) FROM projects WHERE id = 'missing'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
