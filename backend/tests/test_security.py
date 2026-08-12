import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
if "SUBTITLE_FACTORY_DATA_DIR" not in os.environ:
    os.environ["SUBTITLE_FACTORY_DATA_DIR"] = tempfile.mkdtemp(
        prefix="subtitle-factory-security-tests-",
    )

from app import main as app_main, security
from app.main import app
from app.utils.task_manager import TaskManager, task_manager


class LoopbackSecurityTests(unittest.TestCase):
    def setUp(self):
        self.token_patch = patch.object(security, "API_TOKEN", "test-session-token")
        self.token_patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        task_manager.end_exclusive_maintenance("database_restore")
        self.token_patch.stop()

    def test_api_requires_bearer_token_and_returns_structured_error(self):
        denied = self.client.get("/api/health")
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json()["error"]["code"], "UNAUTHORIZED_LOCAL_SESSION")
        allowed = self.client.get(
            "/api/health", headers={"Authorization": "Bearer test-session-token"}
        )
        self.assertEqual(allowed.status_code, 200)

    def test_unknown_origin_is_not_allowed_and_data_mount_is_removed(self):
        preflight = self.client.options(
            "/api/health",
            headers={
                "Origin": "https://malicious.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertNotEqual(preflight.headers.get("access-control-allow-origin"), "https://malicious.example")
        removed = self.client.get(
            "/data/subtitles.db", headers={"Authorization": "Bearer test-session-token"}
        )
        self.assertEqual(removed.status_code, 404)

    def test_media_signature_is_scoped_and_expires(self):
        path = "/api/projects/missing/video"
        signed = security.signed_media_url(path, ttl_seconds=30)
        # A valid signature reaches the endpoint (which reports the missing media)
        # rather than being rejected by session authentication.
        reached = self.client.get(signed)
        self.assertEqual(reached.status_code, 404)
        tampered = self.client.get(signed.replace("/video", "/thumbnail"))
        self.assertEqual(tampered.status_code, 401)
        expired = security.signed_media_url(path, ttl_seconds=-1)
        self.assertEqual(self.client.get(expired).status_code, 401)

    def test_youtube_bridge_requires_session_then_uses_scoped_signature(self):
        session = self.client.get(
            "/api/player/youtube/dQw4w9WgXcQ/session?channel=test-channel",
            headers={"Authorization": "Bearer test-session-token"},
        )
        self.assertEqual(session.status_code, 200)
        signed_url = session.json()["url"]
        bridge = self.client.get(signed_url)
        self.assertEqual(bridge.status_code, 200)
        self.assertIn("https://www.youtube.com/iframe_api", bridge.text)
        self.assertEqual(
            self.client.get(signed_url.replace("dQw4w9WgXcQ", "aaaaaaaaaaa")).status_code,
            401,
        )

    def test_restore_maintenance_gate_blocks_new_mutations_but_keeps_reads_visible(self):
        manager = TaskManager(max_workers=1)
        with patch.object(app_main, "task_manager", manager):
            acquired, active = manager.begin_exclusive_maintenance("database_restore")
            self.assertTrue(acquired, active)
            headers = {"Authorization": "Bearer test-session-token"}
            denied = self.client.post(
                "/api/projects",
                json={"source_type": "local", "title": "Unauthorized mutation"},
            )
            self.assertEqual(denied.status_code, 401, denied.text)
            self.assertEqual(denied.json()["error"]["code"], "UNAUTHORIZED_LOCAL_SESSION")
            read = self.client.get("/api/projects", headers=headers)
            self.assertEqual(read.status_code, 200, read.text)
            blocked = self.client.post(
                "/api/projects",
                headers=headers,
                json={"source_type": "local", "title": "Must not be created"},
            )
            self.assertEqual(blocked.status_code, 409, blocked.text)
            self.assertEqual(blocked.json()["error"]["code"], "DATABASE_RESTORE_PENDING")
        manager.shutdown()


if __name__ == "__main__":
    unittest.main()
