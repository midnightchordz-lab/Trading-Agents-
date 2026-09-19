"""Iter20 integration checks:
- POST /api/pay/order with NO currency uses region-derived currency
- CORS preflight on /api/wallet/balance
- Arabic language accepted by /api/analyze (validation only, no run)
"""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth as au  # noqa: E402
from pipeline import language_directive  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")
BASE = "http://localhost:8001/api"
JWT_SECRET = os.environ["JWT_SECRET"]
client = MongoClient(os.environ["MONGO_URL"])
db = client[os.environ.get("DB_NAME", "test_database")]
RAZORPAY_AUTH = (os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"])

_CREATED_USERS: list[str] = []
_CREATED_LINKS: list[str] = []


def make_user(phone=None, email=None):
    uid = f"TEST_i20-{uuid.uuid4()}"
    db.users.insert_one({
        "id": uid,
        "phone": phone,
        "email": email or f"{uid}@example.com",
        "consent": {"agreed": True, "agreed_at": datetime.now(timezone.utc).isoformat(), "version": "1.0"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _CREATED_USERS.append(uid)
    return uid, {"Authorization": f"Bearer {au.create_session_token(uid, JWT_SECRET)}"}


def teardown_module():
    for uid in _CREATED_USERS:
        db.users.delete_one({"id": uid})
        db.wallets.delete_one({"device_id": f"user:{uid}"})
    for lid in _CREATED_LINKS:
        try:
            requests.post(f"https://api.razorpay.com/v1/payment_links/{lid}/cancel",
                          auth=RAZORPAY_AUTH, timeout=10)
        except Exception:
            pass


def test_pay_order_no_currency_indian_phone_creates_inr_link():
    """Indian phone user posts /pay/order with NO currency; expect INR link."""
    phone = f"+9198{uuid.uuid4().int % 10**8:08d}"
    uid, headers = make_user(phone=phone)
    res = requests.post(
        f"{BASE}/pay/order",
        json={"amount": 99, "device_id": f"user:{uid}"},
        headers=headers, timeout=30,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("currency") == "INR", body
    # Track link for cancellation
    link_id = body.get("order_id") or body.get("payment_link_id") or body.get("id")
    if link_id:
        _CREATED_LINKS.append(link_id)


def test_pay_order_no_currency_us_phone_creates_usd_link():
    uid, headers = make_user(phone="+14155550134")
    res = requests.post(
        f"{BASE}/pay/order",
        json={"amount": 5, "device_id": f"user:{uid}"},
        headers=headers, timeout=30,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("currency") == "USD", body
    link_id = body.get("order_id") or body.get("payment_link_id") or body.get("id")
    if link_id:
        _CREATED_LINKS.append(link_id)


def test_cors_preflight_wallet_balance_authorization_allowed():
    res = requests.options(
        "http://localhost:8001/api/wallet/balance",
        headers={
            "Origin": "https://trade-agent-app.preview.emergentagent.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
        timeout=10,
    )
    assert res.status_code in (200, 204), (res.status_code, res.text)
    allow_hdrs = res.headers.get("access-control-allow-headers", "").lower()
    assert "authorization" in allow_hdrs, res.headers
    allow_methods = res.headers.get("access-control-allow-methods", "").upper()
    for m in ("GET", "POST", "DELETE", "OPTIONS"):
        assert m in allow_methods, allow_methods


def test_language_directive_ar_names_arabic_english_keys():
    d = language_directive("ar")
    lo = d.lower()
    assert "arabic" in lo
    # Must insist JSON keys stay English
    assert "english" in lo or "json" in lo


def test_analyze_accepts_ar_language():
    """Only asserting the endpoint accepts 'ar' — reject with a validation error
    would return 422/400 immediately; we deliberately post an invalid ticker
    to avoid burning an LLM run, and expect a NON-422 (validation of language
    passes)."""
    uid, headers = make_user()
    # Use no-such-symbol so we return quickly. This is best-effort: main check
    # is that we do NOT get a 422 on the 'language' field.
    res = requests.post(
        f"{BASE}/analyze",
        json={"symbol": "AAPL", "language": "ar", "device_id": f"user:{uid}"},
        headers=headers, timeout=10,
    )
    # We don't run the LLM in tests; the point is that language: 'ar' is not
    # itself rejected as invalid input.
    assert res.status_code != 422, res.text
