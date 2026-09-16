"""Razorpay wallet top-ups.

Flow: the app asks the backend for an order (amount validated against
wallet.TOPUP_PACKS), the backend creates a Razorpay order and returns a hosted
checkout URL that this same backend serves. Razorpay posts the result back to
/api/pay/callback, and also sends a payment.captured webhook. Both paths
verify server-side and credit through the same idempotent ledger, so a balance
can never be credited twice — or credited at all without a captured payment
confirmed by Razorpay itself.

No credentials, amounts or balances are ever trusted from the client.
"""
import hashlib
import hmac
import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

import wallet as wal

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


async def razorpay_request(method: str, path: str, **kwargs) -> dict:
    async with httpx.AsyncClient(base_url=RAZORPAY_API, auth=(KEY_ID, KEY_SECRET), timeout=20) as c:
        r = await c.request(method, path, **kwargs)
        r.raise_for_status()
        return r.json()


async def create_order(amount_rupees: float, receipt: str, notes: dict) -> dict:
    """Amount goes to Razorpay in the smallest currency unit (cents), as an
    integer. Currency follows the wallet's own currency."""
    return await razorpay_request(
        "POST",
        "/orders",
        json={
            "amount": int(round(amount_rupees * 100)),
            "currency": wal.CURRENCY,
            "receipt": receipt,
            "payment_capture": 1,
            "notes": notes,
        },
    )


async def fetch_payment(payment_id: str) -> dict:
    return await razorpay_request("GET", f"/payments/{payment_id}")


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


def checkout_html(*, order_id: str, amount_paise: int, callback_url: str, brand: str) -> str:
    """Hosted Razorpay Standard Checkout page. Every value here is
    server-derived. Rendered inside a WebView on native and a popup on web."""
    from html import escape

    return f"""<!doctype html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{escape(brand)} — secure checkout</title>
<style>body{{margin:0;background:#05070B;color:#EEF7E0;font:14px -apple-system,Arial,sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;text-align:center}}</style>
</head><body>
<p>Opening secure Razorpay checkout…</p>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<script>
  var options = {{
    key: "{escape(KEY_ID, quote=True)}",
    amount: {amount_paise},
    currency: "{wal.CURRENCY}",
    name: "{escape(brand, quote=True)}",
    description: "Wallet top-up",
    order_id: "{escape(order_id, quote=True)}",
    // Razorpay only POSTs the three razorpay_* fields to callback_url when
    // redirect is on; the order id rides along as a query param so a failed /
    // cancelled return (which carries no fields) can still be matched.
    callback_url: "{escape(callback_url, quote=True)}?order_id={escape(order_id, quote=True)}",
    redirect: true,
    modal: {{ confirm_close: true, escape: false, backdropclose: false }}
  }};
  window.onload = function () {{ new Razorpay(options).open(); }};
</script>
</body></html>"""


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
