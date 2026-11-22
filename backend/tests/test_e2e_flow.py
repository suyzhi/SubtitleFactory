"""End-to-end integration test for the simple-edition subtitle workflow.

Covers the complete user journey through the real HTTP API without network,
FFmpeg, or real ML inference:

    创建本地项目 → 导入本地视频 → 一键工作流（音频提取 + 转写 + AI 整理 + AI 翻译）
    → 读取字幕 → 导出并下载原文/双语 SRT
"""

import os
import sys
import tempfile
import unittest
import uuid
import wave
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
if "SUBTITLE_FACTORY_DATA_DIR" not in os.environ:
    os.environ["SUBTITLE_FACTORY_DATA_DIR"] = tempfile.mkdtemp(
        prefix="subtitle-factory-e2e-tests-",
    )

from app.api import projects  # noqa: E402
from app.models import database  # noqa: E402


class _SynchronousTaskManager:
    """Runs ``run_background`` workers inline so endpoints finish the work."""

    def __init__(self) -> None:
        self._counter = 0

    def active_task_ids(self, _project_id):
        return []

    def create_task(self, _project_id, kind, **_options):
        self._counter += 1
        return f"e2e-task-{kind}-{self._counter}"

    def get_task(self, _task_id):
        return {"status": "running", "details": {}}

    def update_task(self, *_args, **_kwargs):
        return None

    def add_log(self, *_args, **_kwargs):
        return None

    def resource_slot(self, *_args, **_kwargs):
        return nullcontext()

    def run_background(self, _task_id, worker, *args, **kwargs):
        worker(_task_id, *args, **kwargs)
        return None

    def checkpoint(self, _task_id):
        return None


def _make_minimal_wav(path: Path, seconds: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * (16000 * seconds))
    return path


def _publish_segments(_task_id, _audio_path, project_id, _language, _model, _runtime=None):
    segments = [
        (1, 0.0, 1.5, "Hello world"),
        (2, 1.6, 3.0, "This is the second line"),
    ]
    db = database.get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM segments WHERE project_id=?", (project_id,))
        for idx, start, end, text in segments:
            db.execute(
                """INSERT INTO segments
                   (id,project_id,idx,start,end,raw_text,clean_text,
                    is_draft,source_stage)
                   VALUES (?,?,?,?,?,?,?,0,'postprocessed')""",
                (str(uuid.uuid4()), project_id, idx, start, end, text, text),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return None


class EndToEndFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_patch = patch.object(database, "DB_PATH", root / "factory.db")
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

    def _create_and_import_project(self) -> str:
        created = self.client.post(
            "/api/projects",
            json={
                "source_type": "local",
                "title": "端到端流程",
                "language": "en",
                "target_language": "zh",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        project_id = created.json()["project_id"]
        uploaded = self.client.post(
            f"/api/projects/{project_id}/import-local",
            files={"file": ("sample.mp4", b"fake-mp4-bytes", "video/mp4")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        return project_id

    def _fake_extract_audio(self, _task_id, project_id, _video_path, **_kwargs):
        audio_path = _make_minimal_wav(self.root / "audio" / project_id / "audio.wav")
        db = database.get_db()
        db.execute(
            "UPDATE projects SET audio_path=? WHERE id=?", (str(audio_path), project_id)
        )
        db.commit()
        db.close()

    def _segments(self, project_id: str):
        response = self.client.get(f"/api/projects/{project_id}/segments")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["segments"]

    def test_workflow_transcribes_and_exports_downloadable_srt(self):
        project_id = self._create_and_import_project()
        manager = _SynchronousTaskManager()

        with (
            patch("app.api.projects.task_manager", manager),
            patch("app.api.projects._do_extract_audio", side_effect=self._fake_extract_audio),
            patch("app.api.projects.transcribe_audio", new=_publish_segments),
        ):
            started = self.client.post(
                f"/api/projects/{project_id}/workflow",
                json={"model": "small", "runtime": "cpu", "language": "en"},
            )
        self.assertEqual(started.status_code, 200, started.text)
        self.assertTrue(started.json()["task_id"])

        segments = self._segments(project_id)
        self.assertEqual(
            [item["clean_text"] for item in segments],
            ["Hello world", "This is the second line"],
        )

        exported = self.client.post(
            f"/api/projects/{project_id}/export",
            json={"format": "srt", "bilingual": False},
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        srt_path = Path(exported.json()["path"])
        self.assertTrue(srt_path.is_file())

        downloaded = self.client.get(
            f"/api/projects/{project_id}/export/download?fmt=srt"
        )
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertIn("Hello world", downloaded.text)
        self.assertIn("This is the second line", downloaded.text)
        self.assertIn("00:00:00,000 --> 00:00:01,500", downloaded.text)

    def test_clean_translate_and_bilingual_srt_export(self):
        project_id = self._create_and_import_project()
        manager = _SynchronousTaskManager()

        def fake_clean(_task_id, pid, _target_length, *_args):
            db = database.get_db()
            db.execute(
                "UPDATE segments SET clean_text='Hello, world.' WHERE project_id=? AND idx=1",
                (pid,),
            )
            db.commit()
            db.close()

        def fake_translate(_task_id, pid, _target_lang, *_args):
            db = database.get_db()
            db.execute(
                "UPDATE segments SET translated_text='你好，世界。' WHERE project_id=? AND idx=1",
                (pid,),
            )
            db.execute(
                "UPDATE segments SET translated_text='这是第二行。' WHERE project_id=? AND idx=2",
                (pid,),
            )
            db.commit()
            db.close()

        with (
            patch("app.api.projects.task_manager", manager),
            patch("app.api.projects._do_extract_audio", side_effect=self._fake_extract_audio),
            patch("app.api.projects.transcribe_audio", new=_publish_segments),
            patch("app.api.projects._do_clean", side_effect=fake_clean),
            patch("app.api.projects._do_translate", side_effect=fake_translate),
        ):
            response = self.client.post(
                f"/api/projects/{project_id}/workflow",
                json={
                    "model": "small",
                    "runtime": "cpu",
                    "language": "en",
                    "enable_clean": True,
                    "enable_translate": True,
                    "target_language": "zh",
                    "text_processing_consent": True,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)

        exported = self.client.post(
            f"/api/projects/{project_id}/export",
            json={
                "format": "srt-bilingual",
                "bilingual": True,
                "primary_language": "translated",
            },
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        downloaded = self.client.get(
            f"/api/projects/{project_id}/export/download?fmt=srt-bilingual"
        )
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertIn("你好，世界。\nHello, world.", downloaded.text)
        self.assertIn("这是第二行。\nThis is the second line", downloaded.text)


if __name__ == "__main__":
    unittest.main()
