"""Transactional email for login codes, via Emergent's managed email proxy.

Only one send path exists (the sign-in code below): the recipient comes from
the OTP record the server itself created, and the HTML is a fixed server-side
template — callers never supply a recipient, subject or body.
"""
import ipaddress
import logging
import os
import re
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

# server.py imports this module before it calls load_dotenv, so load the env
# here too — otherwise the key reads as empty and every send is skipped.
load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

# Emergent managed email proxy. This is a CONSTANT — never read it from
# os.environ, so it survives deployment.
EMAIL_BASE_URL = "https://integrations.emergentagent.com"
EMAIL_KEY = os.environ.get("EMERGENT_EMAIL_KEY", "")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "TradingAgents")
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO")

_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = ("reply with your password", "reply with the code", "send your password", "cvv",
             "send us your password", "enter your password below", "confirm your card number",
             "your full card number", "seed phrase", "recovery phrase", "verify your card",
             "social security number", "confirm your bank details")
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)


def _host_ok(host: str) -> bool:
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)


def _same_site(shown: str, real: str) -> bool:
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)


class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []


def _assert_safe_email(subject: str, html: str) -> None:
    """Structural guard for every send path. If a legitimate email trips this,
    rewrite the copy — never weaken or bypass it."""
    scan = _EmailScan()
    scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks the recipient for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links/assets must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened, numeric-host or credential-bearing URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} ≠ real link host {real!r} (G3)")


def email_configured() -> bool:
    return bool(EMAIL_KEY)


async def send_email(*, to: str, subject: str, html: str) -> str | None:
    _assert_safe_email(subject, html)
    payload = {"to": [to], "subject": subject, "html": html, "from_name": EMAIL_FROM_NAME}
    if EMAIL_REPLY_TO:
        payload["contact_email"] = EMAIL_REPLY_TO
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{EMAIL_BASE_URL}/api/v1/email/send",
            headers={"X-Email-Key": EMAIL_KEY},
            json=payload,
        )
    resp.raise_for_status()
    return resp.json().get("id")


def otp_email_html(otp: str, ttl_minutes: int) -> str:
    """Fixed server-side template — the code is the only interpolated value."""
    brand = escape(EMAIL_FROM_NAME)
    return (
        '<table role="presentation" width="100%"><tr><td style="padding:24px;'
        'font-family:Arial,Helvetica,sans-serif;color:#09090B">'
        f'<p style="font-size:15px;margin:0 0 16px">Here is your {brand} sign-in code:</p>'
        f'<p style="font-size:32px;letter-spacing:6px;font-weight:bold;margin:0 0 16px">{escape(otp)}</p>'
        f'<p style="font-size:13px;color:#3F3F55;margin:0 0 8px">It expires in {ttl_minutes} minutes and '
        'can be used once.</p>'
        '<p style="font-size:13px;color:#3F3F55;margin:0 0 16px">If you did not try to sign in, you can '
        'safely ignore this email.</p>'
        f'<p style="font-size:12px;color:#888;margin:0">Sent by {brand}. We will never ask you for your '
        'password or payment details by email.</p>'
        '</td></tr></table>'
    )


async def send_otp_email(to: str, otp: str, ttl_minutes: int) -> bool:
    """True if the code was handed to the provider. Never raises."""
    if not email_configured():
        logger.warning("email OTP requested but EMERGENT_EMAIL_KEY is unset")
        return False
    try:
        await send_email(
            to=to,
            subject=f"Your {EMAIL_FROM_NAME} sign-in code",
            html=otp_email_html(otp, ttl_minutes),
        )
        return True
    except Exception as e:
        logger.error(f"OTP email send failed: {e}")
        return False
