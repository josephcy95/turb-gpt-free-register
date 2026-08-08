# -*- coding: utf-8 -*-
"""API 邮箱导入格式解析与供应商自动识别。"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


class ApiMailImportError(ValueError):
    """API 邮箱素材格式或凭据不一致。"""


def _looks_like_url(value: str) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _split_api_mail_line(line: str) -> tuple[str, str, str, str, str]:
    """返回 email, provider_token, url, access_token, totp_secret。"""
    raw = str(line or "").strip()
    if not raw:
        raise ApiMailImportError("空行")

    if "----" in raw:
        parts = [part.strip() for part in raw.split("----")]
    elif "====" in raw:
        parts = [part.strip() for part in raw.split("====")]
    elif "---" in raw:
        parts = [part.strip() for part in raw.split("---", 2)]
    else:
        raise ApiMailImportError("缺少支持的分隔符：----、==== 或 ---")

    if len(parts) < 2 or not parts[0]:
        raise ApiMailImportError("缺少邮箱或取码地址")
    email = parts[0]
    if "@" not in email:
        raise ApiMailImportError(f"邮箱格式无效: {email}")

    provider_token = ""
    access_token = ""
    totp_secret = ""
    if len(parts) >= 3 and not _looks_like_url(parts[1]) and _looks_like_url(parts[2]):
        provider_token = parts[1]
        url = parts[2]
        if len(parts) > 3:
            access_token = parts[3]
        if len(parts) > 4:
            totp_secret = parts[4]
    else:
        url = parts[1]
        if len(parts) > 2:
            access_token = parts[2]
        if len(parts) > 3:
            totp_secret = parts[3]

    if not _looks_like_url(url):
        raise ApiMailImportError(f"取码地址无效: {url}")
    return email, provider_token, url, access_token, totp_secret


def _normalize_flysms_url(email: str, url: str, provider_token: str = "") -> str:
    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/api/pickup/messages/latest"):
        path = path[:-len("/api/pickup/messages/latest")] + "/pickup"
    elif not path.endswith("/pickup"):
        raise ApiMailImportError("FlySMS 地址路径必须以 /pickup 或 /api/pickup/messages/latest 结尾")

    fragment = dict(parse_qsl(parsed.fragment, keep_blank_values=False))
    url_email = str(fragment.get("email") or "").strip()
    url_token = str(fragment.get("key") or "").strip()
    if url_email and url_email.lower() != email.lower():
        raise ApiMailImportError(f"FlySMS URL 邮箱与行首邮箱不一致: {url_email}")
    if provider_token and url_token and provider_token != url_token:
        raise ApiMailImportError("FlySMS 中间 Token 与 URL key 不一致")

    token = url_token or provider_token
    if not token:
        raise ApiMailImportError("FlySMS 地址缺少 key，且未提供中间 Token")
    fragment["email"] = email
    fragment["key"] = token
    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, urlencode(fragment)))


def parse_api_mail_line(line: str, source: str = "api_auto") -> dict:
    """解析一行 API 邮箱，并返回规范化记录及具体内部来源。"""
    email, provider_token, url, access_token, totp_secret = _split_api_mail_line(line)
    selected = str(source or "api_auto").strip().lower()
    if selected not in ("api_auto", "generic_api", "flysms"):
        raise ApiMailImportError(f"不支持的 API 邮箱来源: {selected}")

    path = (urlparse(url).path or "").rstrip("/")
    detected = "flysms" if path.endswith("/pickup") or path.endswith("/api/pickup/messages/latest") else "generic_api"
    concrete_source = detected if selected == "api_auto" else selected
    if concrete_source == "flysms":
        url = _normalize_flysms_url(email, url, provider_token)
    elif provider_token:
        raise ApiMailImportError("三段 Token 格式仅适用于 FlySMS；通用 API 请使用 邮箱----取码地址")

    return {
        "source": concrete_source,
        "email": email,
        "code_url": url,
        "access_token": access_token,
        "totp_secret": totp_secret,
    }
