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
import hashlib
import hmac
import logging
import os
import time
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


async def razorpay_request(method: str, path: str, **kwargs) -> dict:
    async with httpx.AsyncClient(base_url=RAZORPAY_API, auth=(KEY_ID, KEY_SECRET), timeout=20) as c:
        r = await c.request(method, path, **kwargs)
        if r.status_code >= 400:
            try:
                err = r.json().get("error", {}) or {}
            except Exception:
                err = {}
            raise RazorpayError(
                r.status_code,
                err.get("description") or r.text[:300],
                err.get("code") or "",
            )
        return r.json()


async def fetch_payment(payment_id: str) -> dict:
    return await razorpay_request("GET", f"/payments/{payment_id}")


LINK_TTL_SECONDS = 60 * 60


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
