# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import db


class FlySmsDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(db, "_FLYSMS_EMAIL_JSON", root / "flysms.json"),
            patch.object(db, "_FLYSMS_EMAIL_TXT", root / "flysms.txt"),
            patch.object(db, "_GENERIC_API_EMAIL_JSON", root / "generic.json"),
            patch.object(db, "_GENERIC_API_EMAIL_TXT", root / "generic.txt"),
            patch.object(db, "_ACCOUNTS_JSON", root / "accounts.json"),
            patch.object(db, "_ACCOUNTS_TXT", root / "accounts.txt"),
            patch.object(db, "_TOKENS_TXT", root / "tokens.txt"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_import_and_claim_flysms_email(self):
        pickup = "https://flysms.xyz/icloud/pickup#email=a%40icloud.com&key=TOKEN"
        self.assertEqual(db.import_flysms_emails([{"email": "a@icloud.com", "code_url": pickup}]), (1, 0))
        row = db.claim_next_flysms_email()
        self.assertEqual(row["email"], "a@icloud.com")
        self.assertEqual(row["pickup_url"], pickup)
        self.assertEqual(db.flysms_email_pool_summary()["used"], 1)

    def test_migrate_removes_flysms_from_generic_pool(self):
        pickup = "https://flysms.xyz/icloud/pickup#email=a%40icloud.com&key=TOKEN"
        db.import_generic_api_emails([{"email": "a@icloud.com", "code_url": pickup}])
        db.release_generic_api_email("a@icloud.com", status="failed", note="旧取码方式失败")
        self.assertEqual(db.migrate_flysms_from_generic_api_pool(), 1)
        self.assertIsNone(db.get_generic_api_email_by_email("a@icloud.com"))
        row = db.get_flysms_email_by_email("a@icloud.com")
        self.assertEqual(row["pickup_url"], pickup)
        self.assertEqual(row["status"], "available")

    def test_consumed_message_identity_is_persisted_once(self):
        pickup = "https://flysms.xyz/icloud/pickup#email=a%40icloud.com&key=TOKEN"
        db.import_flysms_emails([{"email": "a@icloud.com", "code_url": pickup}])
        received_at = "2026-08-08T07:29:24.000Z"
        self.assertTrue(db.consume_flysms_message("a@icloud.com", received_at, 42))
        self.assertFalse(db.consume_flysms_message("a@icloud.com", received_at, 42))
        row = db.get_flysms_email_by_email("a@icloud.com")
        self.assertEqual(row["last_consumed_mailbox_received_at"], received_at)
        self.assertEqual(row["last_consumed_uid"], "42")
        self.assertFalse(db.consume_flysms_message("a@icloud.com", "2026-08-08T07:29:23.000Z", 41))
        self.assertTrue(db.consume_flysms_message("a@icloud.com", received_at, 43))


if __name__ == "__main__":
    unittest.main()
