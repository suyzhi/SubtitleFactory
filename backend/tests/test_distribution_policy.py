import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
if "SUBTITLE_FACTORY_DATA_DIR" not in os.environ:
    os.environ["SUBTITLE_FACTORY_DATA_DIR"] = tempfile.mkdtemp(
        prefix="subtitle-factory-distribution-tests-",
    )

from app.main import app
from app.models.database import get_db
from app.services import (
    app_settings,
    runtime_diagnostics,
    workflow_recovery,
)
from app.services.distribution import (
    APP_STORE_CHANNEL,
    CHANNEL_ENV,
    DIRECT_CHANNEL,
    distribution_capabilities,
)
from app.utils import config
from app.utils.task_manager import task_manager


class DistributionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.token = "distribution-test-token"
        self.client = TestClient(
            app,
            headers={"Authorization": f"Bearer {self.token}"},
        )

    def tearDown(self):
        self.client.close()

    def test_unknown_channel_falls_back_to_direct(self):
        with patch.dict(os.environ, {CHANNEL_ENV: "unexpected"}):
            capabilities = distribution_capabilities()
        self.assertEqual(capabilities.channel, DIRECT_CHANNEL)
        self.assertTrue(capabilities.youtube)
        self.assertTrue(capabilities.filesystem_automation)

    def test_health_exposes_app_store_capabilities(self):
        with (
            patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}),
            patch.object(
                runtime_diagnostics,
                "_yt_dlp_module",
                side_effect=AssertionError("App Store health loaded yt-dlp"),
            ),
        ):
            response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        capabilities = response.json()["distribution"]
        self.assertEqual(capabilities["channel"], APP_STORE_CHANNEL)
        self.assertFalse(capabilities["youtube"])
        self.assertFalse(capabilities["browser_cookies"])
        self.assertFalse(capabilities["filesystem_automation"])
        runtime = response.json()["runtime"]
        self.assertEqual(runtime["yt_dlp"]["status"], "disabled")
        self.assertEqual(runtime["deno"]["status"], "disabled")
        self.assertEqual(runtime["ejs"]["status"], "disabled")

    def test_app_store_does_not_resume_interrupted_youtube_workflow(self):
        created = self.client.post(
            "/api/projects",
            json={
                "title": "legacy direct project",
                "source_type": "youtube",
                "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        interrupted = [
            {
                "id": "legacy-task",
                "type": "workflow",
                "details": json.dumps(
                    {
                        "resume_payload": {
                            "project_id": created.json()["project_id"],
                            "model": "small",
                            "language": "auto",
                            "runtime": "cpu",
                            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                        }
                    }
                ),
            }
        ]
        with (
            patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}),
            patch.object(task_manager, "create_task") as create_task,
        ):
            resumed = workflow_recovery.resume_interrupted_workflows(interrupted)
        self.assertEqual(resumed, 0)
        create_task.assert_not_called()

    def test_restart_never_auto_resumes_ai_workflows(self):
        interrupted = [
            {
                "id": "ai-clean-workflow",
                "type": "workflow",
                "status": "running",
                "details": json.dumps(
                    {
                        "resume_payload": {
                            "project_id": "project",
                            "model": "small",
                            "language": "en",
                            "runtime": "cpu",
                            "options": {"enable_clean": True},
                        }
                    }
                ),
            },
            {
                "id": "ai-translate-workflow",
                "type": "workflow",
                "status": "running",
                "details": json.dumps(
                    {
                        "resume_payload": {
                            "project_id": "project",
                            "model": "small",
                            "language": "en",
                            "runtime": "cpu",
                            "options": {"enable_translate": True},
                        }
                    }
                ),
            },
        ]
        with (
            patch.dict(os.environ, {CHANNEL_ENV: DIRECT_CHANNEL}),
            patch.object(task_manager, "create_task") as create_task,
        ):
            resumed = workflow_recovery.resume_interrupted_workflows(interrupted)
        self.assertEqual(resumed, 0)
        create_task.assert_not_called()

    def test_restart_links_automatic_local_workflow_continuation(self):
        created = self.client.post(
            "/api/projects",
            json={
                "title": "local restart recovery",
                "source_type": "local",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        project_id = created.json()["project_id"]
        payload = {
            "project_id": project_id,
            "model": "small",
            "language": "en",
            "runtime": "cpu",
            "source_url": None,
        }
        db = get_db()
        try:
            db.execute(
                """INSERT INTO tasks(
                       id,project_id,type,status,message,error,error_code,recoverable,
                       available_actions,details,logs,created_at,updated_at
                   ) VALUES (
                       'local-workflow-before-restart',?,'workflow','failed',
                       '上次运行被中断','应用在任务执行期间退出','APP_INTERRUPTED',1,
                       '["retry"]',?,'[]','before','before'
                   )""",
                (project_id, json.dumps({"resume_payload": payload})),
            )
            db.commit()
        finally:
            db.close()

        interrupted = [
            {
                "id": "local-workflow-before-restart",
                "type": "workflow",
                "status": "running",
                "details": json.dumps({"resume_payload": payload}),
            }
        ]
        with (
            patch.dict(os.environ, {CHANNEL_ENV: DIRECT_CHANNEL}),
            patch.object(
                task_manager, "create_task", return_value="continued-workflow"
            ) as create_task,
            patch.object(task_manager, "update_task") as update_task,
            patch.object(task_manager, "run_background") as run_background,
        ):
            resumed = workflow_recovery.resume_interrupted_workflows(interrupted)
        self.assertEqual(resumed, 1)
        create_task.assert_called_once_with(project_id, "workflow")
        update_task.assert_called_once()
        run_background.assert_called_once()

        db = get_db()
        try:
            original = db.execute(
                "SELECT recoverable,available_actions,message,details FROM tasks WHERE id=?",
                ("local-workflow-before-restart",),
            ).fetchone()
        finally:
            db.close()
        self.assertFalse(original["recoverable"])
        self.assertEqual(json.loads(original["available_actions"]), [])
        self.assertEqual(original["message"], "应用重启后已在新任务中继续")
        self.assertEqual(
            json.loads(original["details"])["resumed_by_task_id"], "continued-workflow"
        )

    def test_app_store_rejects_youtube_entry_points_before_work(self):
        with patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}):
            response = self.client.post(
                "/api/projects",
                json={
                    "title": "blocked",
                    "source_type": "youtube",
                    "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                },
            )
        self.assertEqual(response.status_code, 403, response.text)
        payload = response.json()["error"]
        self.assertEqual(payload["code"], "DISTRIBUTION_FEATURE_UNAVAILABLE")
        self.assertEqual(payload["details"]["feature"], "youtube")
        self.assertFalse(payload["recoverable"])

    def test_app_store_projects_runtime_settings_without_erasing_direct_preferences(self):
        original = app_settings.get_app_settings()
        try:
            with patch.dict(os.environ, {CHANNEL_ENV: DIRECT_CHANNEL}):
                app_settings.save_app_settings(
                    {
                        "transcription_runtime_by_model": {
                            "small": "cpu",
                            "parakeet-tdt-0.6b-v3-coreml": "external_coreml",
                        },
                    }
                )
            with patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}):
                effective = app_settings.get_effective_app_settings()
                self.assertEqual(
                    effective["transcription_runtime_by_model"], {"small": "cpu"}
                )
                app_settings.save_app_settings(
                    {
                        "clean_provider_id": "openai",
                        "transcription_runtime_by_model": {"small": "mlx"},
                    }
                )
            with patch.dict(os.environ, {CHANNEL_ENV: DIRECT_CHANNEL}):
                persisted = app_settings.get_app_settings()
                self.assertEqual(persisted["clean_provider_id"], "openai")
                self.assertEqual(
                    persisted["transcription_runtime_by_model"],
                    {
                        "small": "mlx",
                        "parakeet-tdt-0.6b-v3-coreml": "external_coreml",
                    },
                )
        finally:
            with patch.dict(os.environ, {CHANNEL_ENV: DIRECT_CHANNEL}):
                app_settings.save_app_settings(original)

    def test_app_store_runtime_resolution_never_falls_back_to_system_path(self):
        with (
            patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}),
            patch.object(runtime_diagnostics, "_bundled_candidates", return_value=[]),
            patch.object(
                runtime_diagnostics.shutil,
                "which",
                side_effect=AssertionError("App Store resolver consulted PATH"),
            ),
        ):
            resolved = runtime_diagnostics.resolve_ffmpeg_path("/tmp/external-ffmpeg")
        self.assertIsNone(resolved)

    def test_app_store_ignores_ambient_path_overrides(self):
        external = "/tmp/must-not-be-treated-as-bundled/ffmpeg"
        with patch.dict(
            os.environ,
            {
                CHANNEL_ENV: APP_STORE_CHANNEL,
                "SUBTITLE_FACTORY_ALLOW_ENV_PATHS": "1",
                "SUBTITLE_FACTORY_BUNDLED_FFMPEG": external,
            },
        ):
            self.assertFalse(config.environment_path_overrides_enabled())
            candidates = list(runtime_diagnostics._bundled_candidates("ffmpeg"))
        self.assertNotIn(Path(external), {path for path, _source in candidates})

    def test_app_store_hides_and_rejects_external_models(self):
        with patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}):
            catalog = self.client.get("/api/transcription/models")
            blocked = self.client.post(
                "/api/transcription/models/parakeet-tdt-0.6b-v3-coreml/prepare",
                json={"runtime": "external_coreml"},
            )
        self.assertEqual(catalog.status_code, 200, catalog.text)
        models = catalog.json()["models"]
        model_ids = {item["id"] for item in models}
        self.assertEqual(len(models), 27)
        self.assertNotIn("custom", model_ids)
        self.assertNotIn("parakeet-tdt-0.6b-v3-coreml", model_ids)
        self.assertFalse(any(model_id.startswith("local:") for model_id in model_ids))
        self.assertEqual(blocked.status_code, 403, blocked.text)
        error = blocked.json()["error"]
        self.assertEqual(error["code"], "DISTRIBUTION_FEATURE_UNAVAILABLE")
        self.assertEqual(error["details"]["feature"], "external_runtime_paths")

    def test_app_store_hides_legacy_youtube_projects_without_mutating_them(self):
        created = self.client.post(
            "/api/projects",
            json={
                "title": "legacy hidden project",
                "source_type": "youtube",
                "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        project_id = created.json()["project_id"]
        db = get_db()
        try:
            db.execute(
                """INSERT INTO segments(
                       id,project_id,idx,start,end,raw_text,clean_text,
                       translated_text,is_draft,source_stage
                   ) VALUES (?,?,?,?,?,?,?,?,0,'final')""",
                (
                    f"legacy-segment-{project_id}",
                    project_id,
                    1,
                    0.0,
                    1.0,
                    "legacyneedle hidden text",
                    "legacyneedle hidden text",
                    "",
                ),
            )
            db.commit()
        finally:
            db.close()
        with patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}):
            listing = self.client.get("/api/projects")
            detail = self.client.get(f"/api/projects/{project_id}")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertNotIn(
            project_id, {item["id"] for item in listing.json()["projects"]}
        )
        self.assertEqual(detail.status_code, 403, detail.text)
        self.assertEqual(detail.json()["error"]["details"]["feature"], "youtube")
        db = get_db()
        try:
            persisted = db.execute(
                "SELECT source_type,source_url FROM projects WHERE id=?", (project_id,)
            ).fetchone()
        finally:
            db.close()
        self.assertEqual(persisted["source_type"], "youtube")
        self.assertIn("youtube.com", persisted["source_url"])


if __name__ == "__main__":
    unittest.main()
