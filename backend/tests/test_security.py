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
os.environ.setdefault(
    "SUBTITLE_FACTORY_DATA_DIR",
    tempfile.mkdtemp(prefix="subtitle-factory-security-tests-"),
)

from app import security
from app.main import app


class LoopbackSecurityTests(unittest.TestCase):
    def setUp(self):
        self.token_patch = patch.object(security, "API_TOKEN", "test-session-token")
        self.token_patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
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


if __name__ == "__main__":
    unittest.main()
