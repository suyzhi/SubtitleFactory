import os
import json
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

from app import security
from app.main import app
from app.services.distribution import (
    APP_STORE_CHANNEL,
    CHANNEL_ENV,
    DIRECT_CHANNEL,
    distribution_capabilities,
)
from app.models.database import get_db
from app.services import (
    app_settings,
    project_packages,
    runtime_diagnostics,
    watch_runtime,
)
from app.utils import config
from app.utils.task_manager import task_manager


class DistributionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.token = "distribution-test-token"
        self.token_patch = patch.object(security, "API_TOKEN", self.token)
        self.token_patch.start()
        self.client = TestClient(
            app,
            headers={"Authorization": f"Bearer {self.token}"},
        )

    def tearDown(self):
        self.client.close()
        self.token_patch.stop()

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
                runtime_diagnostics, "_yt_dlp_module",
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
        created = self.client.post("/api/projects", json={
            "title": "legacy direct project",
            "source_type": "youtube",
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        })
        self.assertEqual(created.status_code, 201, created.text)
        interrupted = [{
            "id": "legacy-task",
            "type": "workflow",
            "details": json.dumps({"resume_payload": {
                "project_id": created.json()["project_id"],
                "model": "small",
                "language": "auto",
                "runtime": "cpu",
                "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            }}),
        }]
        with (
            patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}),
            patch.object(task_manager, "create_task") as create_task,
        ):
            resumed = watch_runtime.resume_interrupted_workflows(interrupted)
        self.assertEqual(resumed, 0)
        create_task.assert_not_called()

    def test_app_store_rejects_all_youtube_entry_points_before_work(self):
        with patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}):
            responses = [
                self.client.post("/api/projects", json={
                    "title": "blocked",
                    "source_type": "youtube",
                    "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                }),
                self.client.get(
                    "/api/player/youtube/dQw4w9WgXcQ/session?channel=test-channel",
                ),
                self.client.post("/api/batches/playlist/preview", json={
                    "url": "https://www.youtube.com/playlist?list=PL-test",
                }),
            ]
        for response in responses:
            self.assertEqual(response.status_code, 403, response.text)
            payload = response.json()["error"]
            self.assertEqual(payload["code"], "DISTRIBUTION_FEATURE_UNAVAILABLE")
            self.assertEqual(payload["details"]["feature"], "youtube")
            self.assertFalse(payload["recoverable"])

    def test_app_store_rejects_persistent_path_automation(self):
        with patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}):
            batch = self.client.post("/api/batches", json={
                "name": "blocked",
                "paths": ["/tmp/example.mp4"],
                "configuration": {},
            })
            watch = self.client.get("/api/watch-folders")
            batch_history = self.client.get("/api/batches")
            custom_path = self.client.post("/api/settings/app/validate-path", json={
                "kind": "download_directory",
                "path": "/tmp",
            })
        for response in (batch, watch, batch_history, custom_path):
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(
                response.json()["error"]["code"],
                "DISTRIBUTION_FEATURE_UNAVAILABLE",
            )

    def test_app_store_projects_settings_without_erasing_direct_preferences(self):
        original = app_settings.get_app_settings()
        try:
            with patch.dict(os.environ, {CHANNEL_ENV: DIRECT_CHANNEL}):
                app_settings.save_app_settings({
                    "default_model": "local:direct-model",
                    "ffmpeg_path": "/tmp/direct-ffmpeg",
                    "coreml_model_path": "/tmp/direct-coreml",
                    "transcription_runtime_by_model": {
                        "small": "cpu",
                        "local:direct-model": "external_coreml",
                    },
                })
            with patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}):
                effective = app_settings.get_effective_app_settings()
                self.assertEqual(effective["default_model"], "auto")
                self.assertIsNone(effective["ffmpeg_path"])
                self.assertIsNone(effective["coreml_model_path"])
                self.assertEqual(
                    effective["transcription_runtime_by_model"], {"small": "cpu"},
                )
                app_settings.save_app_settings({
                    "source_language": "ja",
                    "ffmpeg_path": "/tmp/app-store-must-ignore",
                    "transcription_runtime_by_model": {"small": "mlx"},
                })
            with patch.dict(os.environ, {CHANNEL_ENV: DIRECT_CHANNEL}):
                persisted = app_settings.get_app_settings()
                self.assertEqual(persisted["default_model"], "local:direct-model")
                self.assertEqual(persisted["ffmpeg_path"], "/tmp/direct-ffmpeg")
                self.assertEqual(persisted["coreml_model_path"], "/tmp/direct-coreml")
                self.assertEqual(persisted["source_language"], "ja")
                self.assertEqual(persisted["transcription_runtime_by_model"], {
                    "small": "mlx",
                    "local:direct-model": "external_coreml",
                })
        finally:
            with patch.dict(os.environ, {CHANNEL_ENV: DIRECT_CHANNEL}):
                app_settings.save_app_settings(original)

    def test_app_store_runtime_resolution_never_falls_back_to_system_path(self):
        with (
            patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}),
            patch.object(runtime_diagnostics, "_bundled_candidates", return_value=[]),
            patch.object(
                runtime_diagnostics.shutil, "which",
                side_effect=AssertionError("App Store resolver consulted PATH"),
            ),
        ):
            resolved = runtime_diagnostics.resolve_ffmpeg_path("/tmp/external-ffmpeg")
        self.assertIsNone(resolved)

    def test_app_store_ignores_ambient_path_overrides(self):
        external = "/tmp/must-not-be-treated-as-bundled/ffmpeg"
        with patch.dict(os.environ, {
            CHANNEL_ENV: APP_STORE_CHANNEL,
            "SUBTITLE_FACTORY_ALLOW_ENV_PATHS": "1",
            "SUBTITLE_FACTORY_BUNDLED_FFMPEG": external,
        }):
            self.assertFalse(config.environment_path_overrides_enabled())
            candidates = list(runtime_diagnostics._bundled_candidates("ffmpeg"))
        self.assertNotIn(Path(external), {path for path, _source in candidates})

    def test_app_store_hides_and_rejects_external_models(self):
        with patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}):
            catalog = self.client.get("/api/transcription/models")
            responses = [
                self.client.get("/api/transcription/models/imported"),
                self.client.get("/api/transcription/models/custom/validate"),
                self.client.post(
                    "/api/transcription/models/parakeet-tdt-0.6b-v3-coreml/prepare",
                    json={"runtime": "external_coreml"},
                ),
                self.client.put("/api/settings/app", json={
                    "default_model": "local:external-model",
                }),
            ]
        self.assertEqual(catalog.status_code, 200, catalog.text)
        model_ids = {item["id"] for item in catalog.json()["models"]}
        self.assertNotIn("custom", model_ids)
        self.assertNotIn("parakeet-tdt-0.6b-v3-coreml", model_ids)
        self.assertFalse(any(model_id.startswith("local:") for model_id in model_ids))
        for response in responses:
            self.assertEqual(response.status_code, 403, response.text)
            error = response.json()["error"]
            self.assertEqual(error["code"], "DISTRIBUTION_FEATURE_UNAVAILABLE")
            self.assertEqual(error["details"]["feature"], "external_runtime_paths")

    def test_app_store_hides_legacy_youtube_projects_without_mutating_them(self):
        created = self.client.post("/api/projects", json={
            "title": "legacy hidden project",
            "source_type": "youtube",
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        })
        self.assertEqual(created.status_code, 201, created.text)
        project_id = created.json()["project_id"]
        db = get_db()
        try:
            db.execute(
                """INSERT INTO segments(
                       id,project_id,idx,start,end,raw_text,clean_text,
                       translated_text,speaker,locked,is_draft,source_stage
                   ) VALUES (?,?,?,?,?,?,?,?,?,0,0,'final')""",
                (
                    f"legacy-segment-{project_id}", project_id, 1, 0.0, 1.0,
                    "legacyneedle hidden text", "legacyneedle hidden text", "", "",
                ),
            )
            db.commit()
        finally:
            db.close()
        direct_search = self.client.get("/api/search/segments?q=legacyneedle")
        self.assertEqual(direct_search.status_code, 200, direct_search.text)
        self.assertEqual(direct_search.json()["total"], 1)
        with patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}):
            listing = self.client.get("/api/projects")
            detail = self.client.get(f"/api/projects/{project_id}")
            youtube_filter = self.client.get("/api/projects?source_type=youtube")
            hidden_search = self.client.get("/api/search/segments?q=legacyneedle")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertNotIn(
            project_id, {item["id"] for item in listing.json()["projects"]},
        )
        self.assertEqual(hidden_search.status_code, 200, hidden_search.text)
        self.assertEqual(hidden_search.json()["total"], 0)
        for response in (detail, youtube_filter):
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(
                response.json()["error"]["details"]["feature"], "youtube",
            )
        db = get_db()
        try:
            persisted = db.execute(
                "SELECT source_type,source_url FROM projects WHERE id=?", (project_id,),
            ).fetchone()
        finally:
            db.close()
        self.assertEqual(persisted["source_type"], "youtube")
        self.assertIn("youtube.com", persisted["source_url"])

    def test_app_store_sanitizes_imported_project_package_to_local(self):
        created = self.client.post("/api/projects", json={
            "title": "portable legacy project",
            "source_type": "youtube",
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "media_mode": "web",
        })
        self.assertEqual(created.status_code, 201, created.text)
        package = project_packages.export_project_package(
            created.json()["project_id"], include_media=False,
        )
        try:
            with patch.dict(os.environ, {CHANNEL_ENV: APP_STORE_CHANNEL}):
                imported = project_packages.import_project_package(str(package))
            db = get_db()
            try:
                project = db.execute(
                    "SELECT source_type,source_url,media_mode,media_status "
                    "FROM projects WHERE id=?", (imported["project_id"],),
                ).fetchone()
            finally:
                db.close()
            self.assertEqual(project["source_type"], "local")
            self.assertIsNone(project["source_url"])
            self.assertEqual(project["media_mode"], "local")
            self.assertEqual(project["media_status"], "relink_required")
        finally:
            package.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
