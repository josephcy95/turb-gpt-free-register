# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from webui.app import create_app


class ApiMailImportWebUiTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app(auth_code="test-auth").test_client()
        self.client.environ_base["HTTP_X_AUTH_CODE"] = "test-auth"

    @patch("webui.app.db.import_flysms_emails", return_value=(1, 0))
    @patch("webui.app.db.import_generic_api_emails", return_value=(1, 0))
    def test_auto_import_routes_mixed_batch(self, import_generic, import_flysms):
        response = self.client.post("/api/outlook/import", json={
            "source": "api_auto",
            "as_registered": False,
            "text": "\n".join([
                "a@icloud.com---TOKEN---https://flysms.top/icloud/pickup#email=a%40icloud.com&key=TOKEN",
                "b@icloud.com----https://mail.ai1998.xyz/messages/TOKEN/b%40icloud.com",
            ]),
        })
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["inserted"], 2)
        self.assertEqual(set(data["imported_by_source"]), {"flysms", "generic_api"})
        import_flysms.assert_called_once()
        import_generic.assert_called_once()

    def test_auto_import_reports_token_mismatch(self):
        response = self.client.post("/api/outlook/import", json={
            "source": "api_auto",
            "as_registered": False,
            "text": "a@icloud.com---TOKEN_A---https://flysms.top/icloud/pickup#email=a%40icloud.com&key=TOKEN_B",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("Token 与 URL key 不一致", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
