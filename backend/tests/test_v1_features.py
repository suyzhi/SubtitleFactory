import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import database, migrations
from app.services.backups import (
    apply_pending_restore,
    create_backup,
    last_restore,
    pending_restore,
    restore_backup,
)
from app.services.subtitle_importer import parse_subtitle


class V1FeatureTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.patches = [
            patch.object(database, "DB_PATH", self.root / "factory.db"),
        ]
        for item in self.patches: item.start()
        database.init_db()
        self.project_id = "v1-project"
        db = database.get_db()
        db.execute(
            """INSERT INTO projects(id,title,source_type,language,target_language,created_at,updated_at)
               VALUES (?, 'V1', 'local', 'en', 'zh', 'now', 'now')""", (self.project_id,),
        )
        for index, (start, end, text, translation) in enumerate([
            (0, 1, "OpenAI has 2 models", "OpenAI 有 3 个模型"),
            (1.1, 1.3, "Very fast subtitle text", ""),
        ], 1):
            db.execute(
                """INSERT INTO segments(id,project_id,idx,start,end,raw_text,clean_text,translated_text,speaker,locked,is_draft,source_stage)
                   VALUES (?,?,?,?,?,?,?,?, '',0,0,'final')""",
                (str(uuid.uuid4()), self.project_id, index, start, end, text, text, translation),
            )
        db.commit(); db.close()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.folder.cleanup()




    def test_backup_restore_is_verified_staged_and_applied_before_restart(self):
        backup = create_backup("manual")
        backup_path = Path(backup["path"])
        checksum_path = backup_path.with_name(f"{backup_path.name}.sha256")
        self.assertTrue(checksum_path.is_file())
        self.assertTrue(checksum_path.read_text().startswith(backup["hash"]))
        self.assertEqual(stat.S_IMODE(backup_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(checksum_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(backup_path.parent.stat().st_mode), 0o700)

        db = database.get_db()
        db.execute("UPDATE projects SET title='After backup' WHERE id=?", (self.project_id,))
        db.commit()
        db.close()

        staged = restore_backup(backup_path.name)
        self.assertTrue(staged["pending"])
        self.assertTrue(staged["requires_restart"])
        marker = pending_restore()
        self.assertEqual(marker["source_name"], backup_path.name)
        recovery = Path(database.DB_PATH).parent / "recovery"
        self.assertEqual(stat.S_IMODE(recovery.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((recovery / "pending-restore.json").stat().st_mode),
            0o600,
        )
        self.assertEqual(
            stat.S_IMODE((recovery / marker["staging_name"]).stat().st_mode),
            0o600,
        )
        self.assertTrue(Path(staged["safety_backup"]).is_file())
        with self.assertRaisesRegex(RuntimeError, "等待重启"):
            restore_backup(backup_path.name)

        # Staging must not overwrite a live database connection or its current
        # contents. Only the next process-start boundary applies the restore.
        db = database.get_db()
        self.assertEqual(
            db.execute("SELECT title FROM projects WHERE id=?", (self.project_id,)).fetchone()[0],
            "After backup",
        )
        db.close()

        applied = apply_pending_restore()
        self.assertEqual(applied["source_name"], backup_path.name)
        self.assertIsNone(pending_restore())
        self.assertEqual(last_restore()["source_name"], backup_path.name)
        db = database.get_db()
        self.assertEqual(
            db.execute("SELECT title FROM projects WHERE id=?", (self.project_id,)).fetchone()[0],
            "V1",
        )
        db.close()
        self.assertIsNone(apply_pending_restore())

    def test_backup_restore_rejects_tampered_recorded_backup(self):
        backup = create_backup("manual")
        path = Path(backup["path"])
        with path.open("ab") as output:
            output.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "校验值已变化"):
            restore_backup(path.name)
        self.assertIsNone(pending_restore())

    def test_backup_restore_rejects_a_non_database_file(self):
        invalid = Path(database.DB_PATH).parent / "backups" / "manual-invalid.db"
        invalid.parent.mkdir(parents=True, exist_ok=True)
        invalid.write_bytes(b"this is not sqlite")
        with self.assertRaisesRegex(ValueError, "有效的 SQLite"):
            restore_backup(invalid.name)
        self.assertIsNone(pending_restore())




    def test_vtt_and_ass_parsers(self):
        vtt = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n"
        ass = b"[Events]\nDialogue: 0,0:00:00.00,0:00:01.20,Default,,0,0,0,,{\\i1}Hello\\Nworld\n"
        self.assertEqual(parse_subtitle(vtt, "a.vtt")[0]["text"], "Hello")
        self.assertEqual(parse_subtitle(ass, "a.ass")[0]["text"], "Hello\nworld")


    def test_failed_migration_restores_pre_migration_database(self):
        def fail_after_ddl(conn):
            conn.executescript("CREATE TABLE partial_upgrade(value TEXT); INSERT INTO partial_upgrade VALUES ('bad');")
            raise RuntimeError("synthetic migration failure")

        conn = database.get_db()
        with patch.object(
            migrations, "MIGRATIONS",
            migrations.MIGRATIONS + ((migrations.CURRENT_SCHEMA_VERSION + 1, fail_after_ddl),),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                migrations.run_migrations(conn, Path(database.DB_PATH))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        conn.close()
        self.assertNotIn("partial_upgrade", tables)
        self.assertEqual(version, migrations.CURRENT_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
