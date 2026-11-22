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
        response = self.client.post(
            "/api/projects",
            headers={"Origin": "https://unrelated.example"},
            json={"source_type": "local"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/data/subtitles.db").status_code, 404)

    def test_local_origin_can_read_api_and_unsigned_export_reaches_endpoint(self):
        self.assertEqual(
            self.client.get(
                "/api/health", headers={"Origin": "http://localhost:5173"}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/projects/missing/export/download?fmt=srt").status_code,
            404,
        )

    def test_removed_full_edition_endpoints_return_404(self):
        for path in (
            "/api/player/youtube/dQw4w9WgXcQ/session?channel=test",
            "/api/projects/missing/video",
            "/api/projects/missing/thumbnail",
            "/api/settings/app",
            "/api/backups",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)


class DesktopSessionSecurityTests(unittest.TestCase):
    def test_desktop_token_and_scoped_export_signature_are_required(self):
        from app import security

        with (
            patch.object(security, "REQUIRE_SESSION", True),
            patch.object(security, "API_TOKEN", "desktop-test"),
            TestClient(app) as client,
        ):
            self.assertEqual(client.get("/api/health").status_code, 401)
            self.assertEqual(
                client.get(
                    "/api/health", headers={"Authorization": "Bearer wrong"}
                ).status_code,
                401,
            )
            self.assertEqual(
                client.get(
                    "/api/health", headers={"Authorization": "Bearer desktop-test"}
                ).status_code,
                200,
            )
            signed = security.signed_media_url(
                "/api/projects/missing/export/download", 30
            )
            self.assertEqual(client.get(signed).status_code, 404)
            self.assertEqual(
                client.get(
                    signed.replace("/missing/", "/other/")
                ).status_code,
                401,
            )
            self.assertEqual(
                client.get(
                    security.signed_media_url(
                        "/api/projects/missing/export/download", -1
                    )
                ).status_code,
                401,
            )
            self.assertEqual(
                client.get(
                    "/api/health",
                    headers={
                        "Origin": "https://unrelated.example",
                        "Authorization": "Bearer desktop-test",
                    },
                ).status_code,
                403,
            )


if __name__ == "__main__":
    unittest.main()
