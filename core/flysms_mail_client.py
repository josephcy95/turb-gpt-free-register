# -*- coding: utf-8 -*-
"""FlySMS pickup 邮箱客户端。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse, urlunparse

import requests

from config import email as _email_cfg
from core.generic_api_mail_client import _extract_yangyang_openai_code, _parse_generic_api_ts

logger = logging.getLogger(__name__)
_CONTEXT_CACHE: dict[str, "FlySmsEmailAccount"] = {}


class FlySmsMailError(RuntimeError):
    """FlySMS 取码错误。"""


@dataclass
class FlySmsEmailAccount:
    email: str
    pickup_url: str


def _parse_flysms_pickup_url(pickup_url: str) -> tuple[str, str, str] | None:
    """解析 pickup 页面地址，返回 (latest_message_api, token, email)。"""
    try:
        parsed = urlparse(pickup_url)
        fragment = parse_qs(parsed.fragment, keep_blank_values=False)
    except Exception:
        return None
    if not parsed.netloc or not (parsed.path or "").rstrip("/").endswith("/pickup"):
        return None
    email = str((fragment.get("email") or [""])[0]).strip()
    token = str((fragment.get("key") or [""])[0]).strip()
    if not email or not token:
        return None
    pickup_root = (parsed.path or "").rstrip("/")[:-len("/pickup")]
    origin = urlunparse((parsed.scheme or "https", parsed.netloc, "", "", "", ""))
    return f"{origin.rstrip('/')}{pickup_root}/api/pickup/messages/latest", token, email


def _fetch_latest_message(
    session: requests.Session,
    pickup_url: str,
    after_ts: float | None = None,
    last_received_at: str | None = None,
    last_uid=None,
) -> tuple[str, dict] | None:
    parsed = _parse_flysms_pickup_url(pickup_url)
    if not parsed:
        raise FlySmsMailError("FlySMS pickup 地址缺少 #email=...&key=...")
    api_url, token, email = parsed
    resp = session.get(
        api_url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Mailbox-Email": email,
            "Referer": pickup_url.split("#", 1)[0],
            "User-Agent": "Mozilla/5.0 (compatible; gpt-register/1.0)",
        },
        timeout=20,
        verify=False,
    )
    if resp.status_code != 200:
        raise FlySmsMailError(f"latest API HTTP {resp.status_code}: {(resp.text or '')[:160]}")
    try:
        data = resp.json()
    except Exception as exc:
        raise FlySmsMailError(f"latest API 返回非 JSON: {(resp.text or '')[:160]}") from exc
    if not isinstance(data, dict) or str(data.get("email") or "").strip().lower() != email.lower():
        return None
    message = data.get("message")
    if not isinstance(message, dict):
        return None

    ts_raw = message.get("mailboxReceivedAt")
    msg_ts = _parse_generic_api_ts(ts_raw)
    if (after_ts or last_received_at) and not msg_ts:
        logger.warning("[FlySMS] latest API 邮件缺少有效 mailboxReceivedAt，跳过 uid=%s", message.get("uid"))
        return None
    if after_ts and msg_ts and msg_ts < after_ts:
        return None

    message_uid = str(message.get("uid") or "").strip()
    consumed_uid = str(last_uid or "").strip()
    consumed_ts = _parse_generic_api_ts(last_received_at)
    if message_uid and consumed_uid and message_uid == consumed_uid:
        return None
    if consumed_ts and msg_ts:
        if msg_ts < consumed_ts:
            return None
        if msg_ts == consumed_ts:
            try:
                if not message_uid or not consumed_uid or int(message_uid) <= int(consumed_uid):
                    return None
            except ValueError:
                return None

    subject = str(message.get("subject") or "")
    body = "\n".join([str(message.get("text") or ""), str(message.get("html") or "")])
    code = _extract_yangyang_openai_code(subject, body)
    if not code:
        return None
    logger.info(
        "[FlySMS] 提取到 OTP=%s, uid=%s, ts=%s, subject=%r",
        code, message.get("uid"), ts_raw, subject[:80],
    )
    return code, {
        "mail_id": message.get("uid"),
        "received_at": ts_raw,
        "subject": subject,
        "msg_ts": msg_ts,
    }


def pick_account() -> FlySmsEmailAccount:
    from core.db import claim_next_flysms_email, flysms_email_pool_summary, migrate_flysms_from_generic_api_pool

    migrated = migrate_flysms_from_generic_api_pool()
    if migrated:
        logger.info("[FlySMS] 已从通用 API 邮箱池迁移 %s 个 FlySMS 邮箱", migrated)
    row = claim_next_flysms_email()
    if row is None:
        raise FlySmsMailError(f"FlySMS 邮箱池没有可用账号: {flysms_email_pool_summary()}")
    account = FlySmsEmailAccount(email=row["email"], pickup_url=row["pickup_url"])
    _CONTEXT_CACHE[account.email] = account
    logger.info("[FlySMS] 选中邮箱: %s（DB id=%s）", account.email, row.get("id"))
    return account


def get_account_context(email: str) -> FlySmsEmailAccount | None:
    if email in _CONTEXT_CACHE:
        return _CONTEXT_CACHE[email]
    from core.db import get_flysms_email_by_email

    row = get_flysms_email_by_email(email)
    if row is None:
        return None
    account = FlySmsEmailAccount(email=row["email"], pickup_url=row["pickup_url"])
    _CONTEXT_CACHE[email] = account
    return account


def release_account(email: str, status: str = "available", note: str | None = None) -> None:
    from core.db import release_flysms_email

    release_flysms_email(email, status=status, note=note)
    _CONTEXT_CACHE.pop(email, None)


def fetch_latest_otp(
    email: str,
    after_ts: float | None = None,
    max_wait: int | None = None,
    poll_interval: int | None = None,
    settle_seconds: int | None = None,
) -> str:
    account = get_account_context(email)
    if account is None:
        raise FlySmsMailError(f"FlySMS 邮箱不存在或未导入: {email}")

    deadline = time.time() + (max_wait or _email_cfg.OTP_MAX_WAIT)
    interval = poll_interval or _email_cfg.OTP_POLL_INTERVAL
    settle = settle_seconds if settle_seconds is not None else _email_cfg.OTP_SETTLE_SECONDS
    best_otp: str | None = None
    best_meta: dict | None = None
    best_message_key: tuple[str, str] | None = None
    settle_until: float | None = None
    last_error = ""
    logger.info("[FlySMS] 开始轮询邮箱: %s，最长 %ss, settle=%ss", email, max_wait or _email_cfg.OTP_MAX_WAIT, settle)

    while time.time() < deadline:
        try:
            from core.db import get_flysms_email_by_email

            pool_row = get_flysms_email_by_email(email) or {}
            result = _fetch_latest_message(
                requests.Session(),
                account.pickup_url,
                after_ts=after_ts,
                last_received_at=pool_row.get("last_consumed_mailbox_received_at"),
                last_uid=pool_row.get("last_consumed_uid"),
            )
            if result:
                code, meta = result
                now = time.time()
                message_key = (str(meta.get("received_at") or ""), str(meta.get("mail_id") or ""))
                if message_key != best_message_key:
                    best_otp = code
                    best_meta = meta
                    best_message_key = message_key
                    settle_until = now + settle
                    logger.info(
                        "[FlySMS] 锁定新邮件候选 OTP=%s, uid=%s, mailboxReceivedAt=%s，等待 %ss 确认",
                        code, meta.get("mail_id"), meta.get("received_at"), settle,
                    )
            else:
                last_error = "latest API 尚未返回比操作时间和上次已消费邮件更新的验证码"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        now = time.time()
        if best_otp and settle_until is not None and now >= settle_until:
            from core.db import consume_flysms_message

            if best_meta and consume_flysms_message(email, best_meta.get("received_at"), best_meta.get("mail_id")):
                logger.info(
                    "[FlySMS] 已消费新邮件并返回 OTP=%s, uid=%s, mailboxReceivedAt=%s",
                    best_otp, best_meta.get("mail_id"), best_meta.get("received_at"),
                )
                return best_otp
            logger.info("[FlySMS] 候选邮件已被另一取码操作消费，继续等待下一封新邮件")
            best_otp = None
            best_meta = None
            best_message_key = None
            settle_until = None
        remaining = max(0, int(deadline - now))
        logger.info("[FlySMS] %s，%ss 后重试（剩余 %ss）...", "已锁定候选验证码" if best_otp else "暂未拿到验证码", interval, remaining)
        time.sleep(interval)

    if best_otp and best_meta:
        from core.db import consume_flysms_message

        if consume_flysms_message(email, best_meta.get("received_at"), best_meta.get("mail_id")):
            return best_otp
    raise FlySmsMailError(f"等待 FlySMS 验证码超时: {email}; {last_error}")
