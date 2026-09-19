"""Razorpay wallet top-ups.

Flow: the app asks the backend for a top-up (amount validated against
wallet.TOPUP_PACKS), the backend creates a Razorpay **Payment Link** and
returns its short_url, which the app opens in a WebView. Razorpay redirects
back to /api/pay/callback with the link's signed result, and also sends a
payment_link.paid webhook. Both paths verify server-side and credit through
the same idempotent ledger, so a balance can never be credited twice — or
credited at all without a captured payment confirmed by Razorpay itself.

Payment Links (Razorpay-hosted) replaced self-hosted Standard Checkout because
checkout.js rejects any payment whose origin isn't in the account's registered
websites list ("Payment blocked as website does not match registered
website(s)"), which is out of our control at runtime.

No credentials, amounts or balances are ever trusted from the client.
"""
import asyncio
import hashlib
import hmac
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv


load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

RAZORPAY_API = "https://api.razorpay.com/v1"
KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")


def payments_configured() -> bool:
    return bool(KEY_ID and KEY_SECRET)


def is_live_mode() -> bool:
    return KEY_ID.startswith("rzp_live_")


class RazorpayError(RuntimeError):
    """Carries Razorpay's own description so failures are diagnosable from the
    logs (and so the app can show the customer something actionable) instead of
    a bare 'Client error 400'."""

    def __init__(self, status_code: int, description: str, code: str = ""):
        self.status_code = status_code
        self.description = description
        self.code = code
        super().__init__(f"razorpay {status_code} {code}: {description}")


def is_rate_limited(error: "RazorpayError") -> bool:
    """Razorpay throttling, which it reports as a 400 saying "Too many
    requests" rather than a 429. The most common failure in our logs by far,
    and the one thing here that is NOT the customer's fault."""
    return error.status_code == 429 or "too many requests" in (error.description or "").lower()


async def razorpay_request(method: str, path: str, retries: int = 1, **kwargs) -> dict:
    """One retry on throttling only. Razorpay's rate limit is short-lived, so a
    single 700ms wait turns most of those failures into a normal success
    instead of an error the user has to react to. Nothing else is retried: a
    rejected field would fail identically, and a payment must never be created
    twice because we guessed the first attempt didn't land."""
    attempt = 0
    while True:
        async with httpx.AsyncClient(base_url=RAZORPAY_API, auth=(KEY_ID, KEY_SECRET), timeout=20) as c:
            r = await c.request(method, path, **kwargs)
        if r.status_code < 400:
            return r.json()
        try:
            err = (r.json() or {}).get("error", {}) or {}
        except Exception:
            err = {}
        error = RazorpayError(
            r.status_code,
            # `r.text` as the fallback: two failures in our logs printed an
            # EMPTY description, which told us nothing about what went wrong.
            err.get("description") or f"{r.status_code} {r.reason_phrase}: {r.text[:200]}".strip(),
            err.get("code") or "",
        )
        if attempt < retries and is_rate_limited(error):
            attempt += 1
            logger.warning(f"razorpay throttled on {method} {path}; retrying in 0.7s")
            await asyncio.sleep(0.7)
            continue
        raise error


async def fetch_payment(payment_id: str) -> dict:
    return await razorpay_request("GET", f"/payments/{payment_id}")


LINK_TTL_SECONDS = 60 * 60


def link_expiry_iso(link: dict) -> str:
    """When the link Razorpay just created stops being payable, as an ISO
    string. Read back from Razorpay's own response rather than recomputed, so a
    reused link can never be one Razorpay has already expired."""
    expire_by = link.get("expire_by") or (int(time.time()) + LINK_TTL_SECONDS)
    return datetime.fromtimestamp(int(expire_by), tz=timezone.utc).isoformat()


async def create_payment_link(*, amount: float, currency: str, reference_id: str, notes: dict, callback_url: str, description: str, customer: dict) -> dict:
    """A one-time, Razorpay-hosted payment page. Amount goes in the smallest
    currency unit. callback_method must be "get" whenever callback_url is set,
    and this account requires customer email + contact on every link. Currency
    is the account's own locked currency (USD or INR) — never a client-chosen
    value per call, and it's what determines whether UPI shows at all."""
    return await razorpay_request(
        "POST",
        "/payment_links",
        json={
            "amount": int(round(amount * 100)),
            "currency": currency,
            "accept_partial": False,
            "description": description,
            "reference_id": reference_id,
            "notes": notes,
            "customer": customer,
            "callback_url": callback_url,
            "callback_method": "get",
            "expire_by": int(time.time()) + LINK_TTL_SECONDS,
            "reminder_enable": False,
            "notify": {"email": False, "sms": False},
        },
    )


async def fetch_payment_link(link_id: str) -> dict:
    return await razorpay_request("GET", f"/payment_links/{link_id}")


def verify_link_signature(*, link_id: str, reference_id: str, status: str, payment_id: str, supplied: str) -> bool:
    """Payment Links use a different message from Standard Checkout:
    link_id|reference_id|status|payment_id, HMAC-SHA256 with the key secret."""
    expected = hmac.new(
        KEY_SECRET.encode(),
        f"{link_id}|{reference_id}|{status}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, supplied or "")


def verify_checkout_signature(order_id: str, payment_id: str, supplied: str) -> bool:
    """HMAC-SHA256(order_id|payment_id) with the key secret, compared in
    constant time. order_id must be the one WE stored, never one echoed by
    the client."""
    expected = hmac.new(
        KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, supplied or "")


def verify_webhook_signature(raw_body: bytes, supplied: str) -> bool:
    """Verified against the RAW body, before any JSON parsing."""
    if not WEBHOOK_SECRET or not supplied:
        return False
    expected = hmac.new(WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


def result_html(message: str, ok: bool) -> str:
    from html import escape

    colour = "#AEFA3C" if ok else "#FF6B6B"
    return f"""<!doctype html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>body{{margin:0;background:#05070B;color:#EEF7E0;font:15px -apple-system,Arial,sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;padding:24px;text-align:center}}
strong{{color:{colour}}}</style></head>
<body><div><p><strong>{escape(message)}</strong></p>
<p style="color:#66755A;font-size:13px">You can close this and return to the app.</p></div></body></html>"""
