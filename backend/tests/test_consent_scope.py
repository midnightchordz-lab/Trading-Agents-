"""Scope check: the consent gate must exist ONLY on /api/analyze.

A fresh non-consented signed-in account should be able to hit every other
endpoint without a 403 consent_required response. Also confirms anonymous
requests are unaffected. Reused as regression once consent lands.
"""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from pymongo import MongoClient

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(BACKEND_DIR.parent / "frontend" / ".env")

import auth as au  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("PUBLIC_BASE_URL")
    or "http://localhost:8001"
).rstrip("/")
JWT_SECRET = os.environ["JWT_SECRET"]

_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ["DB_NAME"]]


def mk_fresh_user():
    uid = f"TEST_scope-{uuid.uuid4()}"
    _db.users.insert_one({
        "id": uid,
        "email": f"{uid}@example.com",
        "phone": f"+9198{uuid.uuid4().int % 100000000:08d}",
        "free_credits_remaining": 5,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _db.wallets.insert_one({"device_id": f"user:{uid}", "balance": 50.0, "currency": "USD"})
    tok = au.create_session_token(uid, JWT_SECRET)
    return uid, {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}


def cleanup(uid):
    _db.users.delete_many({"id": uid})
    _db.wallets.delete_many({"device_id": f"user:{uid}"})


class TestConsentGateScope:
    """None of these endpoints should 403 consent_required for a fresh
    (non-consented) signed-in user."""

    def test_no_consent_gate_on_unrelated_endpoints(self):
        uid, hdrs = mk_fresh_user()
        dev = f"dev-{uuid.uuid4()}"
        try:
            # Baseline: /analyze IS gated
            r = requests.post(f"{BASE_URL}/api/analyze", headers=hdrs,
                              json={"symbol": "TESTSCOPE", "device_id": dev}, timeout=25)
            assert r.status_code == 403 and r.json().get("detail") == "consent_required", (
                f"/analyze must be consent-gated: {r.status_code} {r.text}")

            def assert_no_consent_gate(method, path, **kw):
                url = f"{BASE_URL}{path}"
                r = requests.request(method, url, headers=hdrs, timeout=25, **kw)
                if r.status_code == 403:
                    body = r.text.lower()
                    assert "consent" not in body, f"{method} {path} unexpectedly consent-gated: {r.text}"
                # Whatever the endpoint's own logic says (404/400/402/501/...) is
                # fine; we just care the consent gate didn't fire.
                return r

            # Wallet / balance
            assert_no_consent_gate("GET", f"/api/wallet/balance?device_id={dev}")
            # Payment orchestration surface (do NOT complete an order)
            assert_no_consent_gate("POST", "/api/pay/order",
                                   json={"device_id": dev, "amount": 5, "currency": "USD"})
            assert_no_consent_gate("GET", "/api/pay/status/does-not-exist")
            assert_no_consent_gate("GET", "/api/pay/iap/config")
            # Auth surface
            assert_no_consent_gate("GET", "/api/auth/me")
            # History / analysis reads
            assert_no_consent_gate("GET", "/api/history")
            assert_no_consent_gate("GET", "/api/analysis/does-not-exist")
            # Portfolio optimize
            assert_no_consent_gate("POST", "/api/portfolio/optimize",
                                   json={"holdings": [{"symbol": "AAPL", "quantity": 1, "avg_price": 100}],
                                         "objective": "hrp", "use_agent_views": False, "cash": 100})
            # Market surface (with a bearer token — should still not consent-gate)
            for path in ["/api/search?q=AAPL", "/api/quote/AAPL", "/api/chart/AAPL?range=1M",
                         "/api/news/AAPL", "/api/trending", "/api/markets/us",
                         "/api/ohlc/AAPL?range=1M"]:
                assert_no_consent_gate("GET", path)
        finally:
            cleanup(uid)

    def test_anonymous_requests_not_consent_gated(self):
        # /analyze anonymous — must NOT be consent-gated. May be 401 (auth
        # required) or 200/402 depending on config, never 403 consent_required.
        r = requests.post(f"{BASE_URL}/api/analyze",
                          json={"symbol": "TESTSCOPE", "device_id": f"dev-{uuid.uuid4()}"},
                          timeout=25)
        if r.status_code == 403:
            assert "consent" not in r.text.lower()

        # Market endpoints are always public
        assert requests.get(f"{BASE_URL}/api/trending", timeout=20).status_code == 200
        assert requests.get(f"{BASE_URL}/api/quote/AAPL", timeout=20).status_code == 200


def teardown_module():
    _db.users.delete_many({"id": {"$regex": "^TEST_scope-"}})
    _db.wallets.delete_many({"device_id": {"$regex": "^user:TEST_scope-"}})
    _db.analyses.delete_many({"symbol": "TESTSCOPE"})
