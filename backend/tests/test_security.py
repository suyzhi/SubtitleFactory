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

from app.main import app


class LoopbackSecurityTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

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

    def test_removed_feature_routes_are_absent_and_cloud_asr_auth_remains(self):
        for path in (
            "/api/projects/p1/content-packs",
            "/api/projects/p1/clip-sets",
            "/api/projects/p1/quality/issues",
            "/api/glossaries",
            "/api/projects/p1/speakers",
            "/api/speaker-models",
            "/api/projects/p1/ocr",
            "/api/maintenance/backups",
            "/api/project-packages/import",
        ):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.client.get("/api/cloud-authorizations").status_code, 200)
        self.assertEqual(
            self.client.put(
                "/api/cloud-authorizations/ocr",
                json={"granted": True, "provider_id": "dashscope"},
            ).status_code,
            422,
        )


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
