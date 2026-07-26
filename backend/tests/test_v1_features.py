import json
import sys
import tempfile
import unittest
import uuid
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import database, migrations
from app.services import project_packages, waveform
from app.services.backups import create_backup, list_backups
from app.services.editor import history_step, import_segment_snapshot
from app.services.quality import scan
from app.services.subtitle_importer import parse_subtitle


class V1FeatureTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.patches = [
            patch.object(database, "DB_PATH", self.root / "factory.db"),
            patch.object(project_packages, "PROJECTS_DIR", self.root / "projects"),
            patch.object(project_packages, "EXPORTS_DIR", self.root / "exports"),
            patch.object(waveform, "PROJECTS_DIR", self.root / "projects"),
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

    def test_schema_quality_glossary_and_import_history(self):
        db = database.get_db()
        version = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        glossary_id = str(uuid.uuid4())
        db.execute("INSERT INTO glossaries VALUES (?,?,'Project terms','en','zh','now','now')", (glossary_id, self.project_id))
        db.execute(
            """INSERT INTO glossary_terms VALUES (?,?, 'OpenAI','开放人工智能',0,1,0,'','now','now')""",
            (str(uuid.uuid4()), glossary_id),
        )
        db.commit(); db.close()
        self.assertEqual(version, migrations.CURRENT_SCHEMA_VERSION)
        issues = scan(self.project_id)
        rules = {item["rule_id"] for item in issues}
        self.assertTrue({"number_mismatch", "glossary", "short_duration", "missing_translation"}.issubset(rules))

        srt = b"1\n00:00:00,000 --> 00:00:01,000\nImported one\n\n2\n00:00:01,100 --> 00:00:02,000\nImported two\n"
        cues = parse_subtitle(srt, "track.srt")
        result = import_segment_snapshot(self.project_id, 0, cues)
        self.assertEqual(result["affected_count"], 2)
        undone = history_step(self.project_id, result["revision"], "undo")
        self.assertEqual(undone["segments"][0]["clean_text"], "OpenAI has 2 models")

    def test_waveform_cache_backup_and_project_package_roundtrip(self):
        audio = self.root / "audio.wav"
        with wave.open(str(audio), "wb") as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
            output.writeframes((b"\x00\x20\x00\xe0") * 8000)
        db = database.get_db(); db.execute("UPDATE projects SET audio_path=? WHERE id=?", (str(audio), self.project_id)); db.commit(); db.close()
        first = waveform.get_waveform(self.project_id, 1000)
        second = waveform.get_waveform(self.project_id, 1000)
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(len(first["peaks"]), 1000)

        backup = create_backup("manual")
        self.assertTrue(Path(backup["path"]).is_file())
        self.assertTrue(list_backups())

        db = database.get_db()
        db.execute(
            "INSERT INTO project_styles(project_id,settings_json,updated_at) VALUES (?,?, 'now')",
            (self.project_id, json.dumps({"fontFamily": "PingFang SC", "fontSize": 24})),
        )
        db.commit(); db.close()

        package = project_packages.export_project_package(self.project_id)
        imported = project_packages.import_project_package(str(package))
        self.assertNotEqual(imported["project_id"], self.project_id)
        self.assertEqual(imported["media_status"], "relink_required")
        db = database.get_db()
        copied = db.execute("SELECT COUNT(*) FROM segments WHERE project_id=?", (imported["project_id"],)).fetchone()[0]
        copied_style = json.loads(db.execute(
            "SELECT settings_json FROM project_styles WHERE project_id=?", (imported["project_id"],)
        ).fetchone()[0])
        db.close()
        self.assertEqual(copied, 2)
        self.assertEqual(copied_style["fontSize"], 24)

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
