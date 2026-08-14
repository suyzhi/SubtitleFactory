"""End-to-end integration test for the core subtitle workflow.

This test drives the real HTTP API surface (project creation, audio
preparation, transcription, segment editing, and SRT export) without any
network, FFmpeg/yt-dlp, or real model inference.  It covers the primary user
journey:

    创建本地项目 → 准备音频 → 转写 → 编辑字幕（改时间/文本、锁定）
    → 导出 SRT → 校验导出内容

Mocks (all offline and deterministic):
  * Audio preparation: a minimal 16 kHz mono 16-bit WAV is written directly to
    the managed audio directory and linked into the project row, replacing any
    FFmpeg/PyAV extraction step.
  * Transcription: ``app.api.projects.transcribe_audio`` is monkeypatched to
    publish two fixed subtitle segments straight into ``segments`` (the same
    schema the real transcriber writes), and ``app.api.projects.task_manager``
    is replaced with a synchronous fake so the background worker runs inline
    and the endpoint returns only after segments are committed.
  * Everything else (creation, editing, locking, export) runs through the real
    endpoints and real file system.

A second test exercises a recovery-oriented invariant: a locked segment is
never silently overwritten by a content edit, and the exported SRT still
carries the locked original text.
"""

import os
import sys
import tempfile
import unittest
import uuid
import wave
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

    def create_task(self, _project_id, kind):
        self._counter += 1
        return f"e2e-task-{kind}-{self._counter}"

    def update_task(self, *_args, **_kwargs):
        return None

    def add_log(self, *_args, **_kwargs):
        return None

    def run_background(self, _task_id, worker, *args, **kwargs):
        # Execute the worker inline; no thread is spawned.
        worker(_task_id, *args, **kwargs)
        return None

    def checkpoint(self, _task_id):
        return None


def _make_minimal_wav(path: Path, seconds: int = 2) -> Path:
    """Write a valid 16 kHz mono 16-bit PCM WAV long enough to pass preflight."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * (16000 * seconds))
    return path


def _publish_segments(_task_id, _audio_path, project_id, _language, _model, _runtime=None):
    """Replace real model inference: write deterministic segments atomically."""
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
        # Pin every managed directory under the isolated temp root.
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

    def _create_project(self) -> str:
        response = self.client.post("/api/projects", json={
            "source_type": "local",
            "title": "端到端流程",
            "language": "en",
            "target_language": "zh",
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["project_id"]

    def _prepare_audio(self, project_id: str) -> Path:
        audio_path = _make_minimal_wav(self.root / "audio" / project_id / "audio.wav")
        db = database.get_db()
        db.execute(
            "UPDATE projects SET audio_path=? WHERE id=?", (str(audio_path), project_id)
        )
        db.commit()
        db.close()
        return audio_path

    def _transcribe(self, project_id: str):
        manager = _SynchronousTaskManager()
        with patch("app.api.projects.task_manager", manager), patch(
            "app.api.projects.transcribe_audio", new=_publish_segments
        ):
            response = self.client.post(
                f"/api/projects/{project_id}/transcribe",
                data={"model": "small", "runtime": "cpu", "language": "en"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["task_id"]

    def _segments(self, project_id: str):
        response = self.client.get(f"/api/projects/{project_id}/segments")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["segments"]

    def test_full_workflow_creates_edits_and_exports_subtitles(self):
        # 1) 创建本地项目
        project_id = self._create_project()

        # 2) 准备音频（写入最小 WAV，替代 FFmpeg 提取）
        audio_path = self._prepare_audio(project_id)
        self.assertTrue(audio_path.is_file())

        # 3) 转写（monkeypatch 推理，写入确定性子幕段）
        task_id = self._transcribe(project_id)
        self.assertTrue(task_id)
        segments = self._segments(project_id)
        self.assertEqual([item["clean_text"] for item in segments], [
            "Hello world", "This is the second line",
        ])

        # 4) 编辑：改第一条的文本与时间码；锁定第二条
        edited = self.client.patch(
            f"/api/projects/{project_id}/segments/1",
            json={"start": 0.1, "end": 1.4, "clean_text": "Hello edited world"},
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["clean_text"], "Hello edited world")
        self.assertAlmostEqual(edited.json()["start"], 0.1)
        self.assertAlmostEqual(edited.json()["end"], 1.4)

        locked = self.client.patch(
            f"/api/projects/{project_id}/segments/2",
            json={"locked": True},
        )
        self.assertEqual(locked.status_code, 200, locked.text)
        self.assertTrue(locked.json()["locked"])

        # 读回验证编辑已持久化
        segments = self._segments(project_id)
        self.assertEqual(segments[0]["clean_text"], "Hello edited world")
        self.assertTrue(segments[1]["locked"])
        self.assertEqual(segments[1]["clean_text"], "This is the second line")

        # 5) 导出 SRT
        exported = self.client.post(
            f"/api/projects/{project_id}/export",
            json={"format": "srt", "bilingual": False},
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        srt_path = Path(exported.json()["path"])
        self.assertTrue(srt_path.is_file())

        # 6) 校验导出内容包含编辑后的文本与锁定原文
        content = srt_path.read_text(encoding="utf-8")
        self.assertIn("Hello edited world", content)
        self.assertIn("This is the second line", content)
        self.assertIn("00:00:00,100 --> 00:00:01,400", content)

    def test_locked_segment_survives_content_edit_and_export(self):
        # 创建、准备音频、转写（与主链路一致）
        project_id = self._create_project()
        self._prepare_audio(project_id)
        self._transcribe(project_id)

        # 锁定第二条，随后尝试改它的 clean_text（不带 include_locked）
        self.client.patch(
            f"/api/projects/{project_id}/segments/2",
            json={"locked": True},
        )
        attempted = self.client.patch(
            f"/api/projects/{project_id}/segments/2",
            json={"clean_text": "Must not overwrite"},
        )
        # 单条编辑接口对锁定段的正文修改保持静默跳过（正文未变化）
        self.assertEqual(attempted.status_code, 200)
        self.assertEqual(attempted.json()["clean_text"], "This is the second line")

        # 导出后锁定原文仍存在，且锁定状态仍为 True
        exported = self.client.post(
            f"/api/projects/{project_id}/export",
            json={"format": "srt"},
        )
        content = Path(exported.json()["path"]).read_text(encoding="utf-8")
        self.assertIn("This is the second line", content)
        self.assertNotIn("Must not overwrite", content)
        self.assertTrue(self._segments(project_id)[1]["locked"])


if __name__ == "__main__":
    unittest.main()
