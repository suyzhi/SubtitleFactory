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
        prefix="subtitle-factory-security-tests-",
    )

from app import main as app_main
from app.main import app
from app.utils.task_manager import TaskManager, task_manager


class LoopbackSecurityTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        task_manager.end_exclusive_maintenance("database_restore")

    def test_api_does_not_require_a_session(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_unrelated_website_is_rejected_even_without_preflight(self):
        response = self.client.post("/api/projects", headers={"Origin": "https://unrelated.example"},
                                    json={"source_type": "local"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/data/subtitles.db").status_code, 404)

    def test_local_origin_can_read_api_and_unsigned_media_reaches_endpoint(self):
        self.assertEqual(self.client.get("/api/health", headers={"Origin": "http://localhost:5173"}).status_code, 200)
        self.assertEqual(self.client.get("/api/projects/missing/video").status_code, 404)

    def test_embedded_web_player_is_removed(self):
        self.assertEqual(self.client.get("/api/player/youtube/dQw4w9WgXcQ/session?channel=test").status_code, 404)

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
            self.assertEqual(denied.status_code, 409, denied.text)
            self.assertEqual(denied.json()["error"]["code"], "DATABASE_RESTORE_PENDING")
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


class DesktopSessionSecurityTests(unittest.TestCase):
    def test_desktop_token_and_scoped_media_signature_are_required(self):
        from app import security
        with patch.object(security, 'REQUIRE_SESSION', True), patch.object(security, 'API_TOKEN', 'desktop-test'), TestClient(app) as client:
            self.assertEqual(client.get('/api/health').status_code, 401)
            self.assertEqual(client.get('/api/health', headers={'Authorization':'Bearer wrong'}).status_code, 401)
            self.assertEqual(client.get('/api/health', headers={'Authorization':'Bearer desktop-test'}).status_code, 200)
            signed=security.signed_media_url('/api/projects/missing/video',30)
            self.assertEqual(client.get(signed).status_code,404)
            self.assertEqual(client.get(signed.replace('/video','/thumbnail')).status_code,401)
            self.assertEqual(client.get(security.signed_media_url('/api/projects/missing/video',-1)).status_code,401)
            self.assertEqual(client.get('/api/health',headers={'Origin':'https://unrelated.example','Authorization':'Bearer desktop-test'}).status_code,403)
