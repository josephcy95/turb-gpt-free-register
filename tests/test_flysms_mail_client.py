# -*- coding: utf-8 -*-
import datetime
import unittest
from unittest.mock import patch

from core.flysms_mail_client import (
    FlySmsEmailAccount,
    FlySmsMailError,
    _fetch_latest_message,
    _parse_flysms_pickup_url,
    fetch_latest_otp,
)


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {
            "email": "test.box@icloud.com",
            "message": {
                "uid": 42,
                "subject": "ChatGPT の一時的な認証コード",
                "mailboxReceivedAt": "2026-08-08T07:29:24.000Z",
                "text": "この一時検証コードを入力して続行してください: 688913",
                "html": "<p style='color:#353740'>688913</p>",
            },
        }


class FakeSession:
    def __init__(self):
        self.url = ""
        self.headers = {}

    def get(self, url, **kwargs):
        self.url = url
        self.headers = kwargs.get("headers") or {}
        return FakeResponse()


class FlySmsMailClientTests(unittest.TestCase):
    def setUp(self):
        self.pickup_url = "https://flysms.xyz/icloud/pickup#email=test.box%40icloud.com&key=TOKEN"

    def test_parse_pickup_url(self):
        self.assertEqual(
            _parse_flysms_pickup_url(self.pickup_url),
            ("https://flysms.xyz/icloud/api/pickup/messages/latest", "TOKEN", "test.box@icloud.com"),
        )

    def test_fetch_latest_message_with_auth_headers(self):
        session = FakeSession()
        code, meta = _fetch_latest_message(session, self.pickup_url)
        self.assertEqual(code, "688913")
        self.assertEqual(meta["mail_id"], 42)
        self.assertEqual(session.headers["Authorization"], "Bearer TOKEN")
        self.assertEqual(session.headers["X-Mailbox-Email"], "test.box@icloud.com")

    def test_fetch_ignores_message_before_registration(self):
        after = datetime.datetime(2026, 8, 8, 7, 30, tzinfo=datetime.timezone.utc).timestamp()
        self.assertIsNone(_fetch_latest_message(FakeSession(), self.pickup_url, after_ts=after))

    def test_fetch_ignores_last_consumed_message(self):
        self.assertIsNone(_fetch_latest_message(
            FakeSession(),
            self.pickup_url,
            last_received_at="2026-08-08T07:29:24.000Z",
            last_uid=42,
        ))

    def test_fetch_accepts_higher_uid_at_same_received_time(self):
        class NewUidResponse(FakeResponse):
            def json(self):
                data = super().json()
                data["message"]["uid"] = 43
                data["message"]["text"] = "verification code: 778899"
                data["message"]["html"] = ""
                return data

        class NewUidSession(FakeSession):
            def get(self, url, **kwargs):
                super().get(url, **kwargs)
                return NewUidResponse()

        code, meta = _fetch_latest_message(
            NewUidSession(),
            self.pickup_url,
            last_received_at="2026-08-08T07:29:24.000Z",
            last_uid=42,
        )
        self.assertEqual(code, "778899")
        self.assertEqual(meta["mail_id"], 43)

    def test_sequential_operations_require_a_new_message(self):
        state = {
            "last_consumed_mailbox_received_at": None,
            "last_consumed_uid": None,
        }
        message = {
            "uid": 42,
            "subject": "ChatGPT verification code",
            "mailboxReceivedAt": "2026-08-08T07:29:24.000Z",
            "text": "verification code: 688913",
            "html": "",
        }

        class MutableResponse(FakeResponse):
            def json(self):
                return {"email": "test.box@icloud.com", "message": dict(message)}

        class MutableSession(FakeSession):
            def get(self, url, **kwargs):
                super().get(url, **kwargs)
                return MutableResponse()

        def get_row(_email):
            return dict(state)

        def consume(_email, received_at, uid):
            if str(uid) == str(state.get("last_consumed_uid") or ""):
                return False
            state["last_consumed_mailbox_received_at"] = received_at
            state["last_consumed_uid"] = str(uid)
            return True

        account = FlySmsEmailAccount("test.box@icloud.com", self.pickup_url)
        with (
            patch("core.flysms_mail_client.get_account_context", return_value=account),
            patch("core.flysms_mail_client.requests.Session", side_effect=MutableSession),
            patch("core.db.get_flysms_email_by_email", side_effect=get_row),
            patch("core.db.consume_flysms_message", side_effect=consume),
        ):
            self.assertEqual(fetch_latest_otp(account.email, max_wait=0.03, poll_interval=0.001, settle_seconds=0), "688913")
            with self.assertRaises(FlySmsMailError):
                fetch_latest_otp(account.email, max_wait=0.01, poll_interval=0.001, settle_seconds=0)

            message.update({
                "uid": 43,
                "mailboxReceivedAt": "2026-08-08T07:29:25.000Z",
                "text": "verification code: 778899",
            })
            self.assertEqual(fetch_latest_otp(account.email, max_wait=0.03, poll_interval=0.001, settle_seconds=0), "778899")


if __name__ == "__main__":
    unittest.main()
