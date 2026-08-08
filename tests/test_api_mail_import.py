# -*- coding: utf-8 -*-
import unittest

from core.api_mail_import import ApiMailImportError, parse_api_mail_line


class ApiMailImportTests(unittest.TestCase):
    def test_auto_detects_generic_messages_page(self):
        row = parse_api_mail_line(
            "dash.recaps.0b@icloud.com----https://mail.ai1998.xyz/messages/TOKEN/dash.recaps.0b%40icloud.com"
        )
        self.assertEqual(row["source"], "generic_api")
        self.assertIn("/messages/TOKEN/", row["code_url"])

    def test_auto_detects_flysms_pickup_by_path_not_domain(self):
        row = parse_api_mail_line(
            "a@icloud.com----https://new-mail-vendor.example/icloud/pickup#email=a%40icloud.com&key=TOKEN"
        )
        self.assertEqual(row["source"], "flysms")
        self.assertIn("key=TOKEN", row["code_url"])

    def test_normalizes_three_dash_token_format(self):
        row = parse_api_mail_line(
            "a@icloud.com---TOKEN---https://flysms.top/icloud/pickup#email=a%40icloud.com&key=TOKEN"
        )
        self.assertEqual(row["source"], "flysms")
        self.assertEqual(
            row["code_url"],
            "https://flysms.top/icloud/pickup#email=a%40icloud.com&key=TOKEN",
        )

    def test_builds_pickup_fragment_from_api_endpoint_and_token(self):
        row = parse_api_mail_line(
            "a@icloud.com---TOKEN---https://flysms.top/icloud/api/pickup/messages/latest"
        )
        self.assertEqual(
            row["code_url"],
            "https://flysms.top/icloud/pickup#email=a%40icloud.com&key=TOKEN",
        )

    def test_rejects_mismatched_flysms_tokens(self):
        with self.assertRaisesRegex(ApiMailImportError, "Token 与 URL key 不一致"):
            parse_api_mail_line(
                "a@icloud.com---TOKEN_A---https://flysms.top/icloud/pickup#email=a%40icloud.com&key=TOKEN_B"
            )

    def test_rejects_mismatched_flysms_email(self):
        with self.assertRaisesRegex(ApiMailImportError, "邮箱与行首邮箱不一致"):
            parse_api_mail_line(
                "a@icloud.com----https://flysms.top/icloud/pickup#email=b%40icloud.com&key=TOKEN"
            )

    def test_manual_generic_override_keeps_pickup_url_generic(self):
        row = parse_api_mail_line(
            "a@icloud.com----https://flysms.top/icloud/pickup#email=a%40icloud.com&key=TOKEN",
            source="generic_api",
        )
        self.assertEqual(row["source"], "generic_api")


if __name__ == "__main__":
    unittest.main()
