"""Iteration 16 — security audit fix verification.

SEC-001: /api/pay/order must NOT write caller-supplied email/phone onto the
         verified user document (privilege-escalation via admin allowlist).
SEC-002: /api/history, /api/analysis/{id}, DELETE /api/analysis/{id} require auth.
SEC-003: server refuses to start when JWT_SECRET is missing or default.
"""
import os
import sys
import uuid
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

import auth as au  # noqa: E402
from auth_helper import AUTH_HEADERS, USER_ID, TOKEN  # noqa: E402

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/") if os.environ.get("EXPO_PUBLIC_BACKEND_URL") else "https://trade-agent-app.preview.emergentagent.com"
JWT_SECRET = os.environ["JWT_SECRET"]
ADMIN_ID = "+918446307145"

_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ["DB_NAME"]]

# Accounts minted by THIS pytest process, so teardown can't touch another
# xdist worker's accounts.
_CREATED_USER_IDS = []


def _mint_user(email=None, phone=None):
    uid = f"TEST_sec16-{uuid.uuid4()}"
    _CREATED_USER_IDS.append(uid)
    _db.users.insert_one({
        "id": uid,
        "email": email,
        "phone": phone,
        "consent": {"agreed": True, "agreed_at": "2026-01-01T00:00:00+00:00", "version": "1.0"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _db.wallets.update_one(
        {"device_id": f"user:{uid}"},
        {"$set": {"device_id": f"user:{uid}", "balance": 5.0}},
        upsert=True,
    )
    tok = au.create_session_token(uid, JWT_SECRET)
    return uid, {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}


@pytest.fixture(scope="module")
def normal_user():
    uid, hdrs = _mint_user(email=f"TEST_normal-{uuid.uuid4()}@example.com")
    yield uid, hdrs
    _db.users.delete_one({"id": uid})
    _db.wallets.delete_one({"device_id": f"user:{uid}"})


@pytest.fixture(scope="module")
def real_admin_user():
    """Test the real, pre-existing admin account (verified phone = admin id)."""
    doc = _db.users.find_one({"id": "admin-" + ADMIN_ID})
    if not doc:
        pytest.skip("real admin account not present in this DB")
    tok = au.create_session_token(doc["id"], JWT_SECRET)
    return doc["id"], {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}


# ================= SEC-001 =================

class TestSEC001NoSelfGrantedAdmin:
    """Any signed-in user hitting /pay/order with the admin phone in the body
    must NOT get their verified fields overwritten, must NOT become admin, and
    must NOT bypass billing."""

    @pytest.mark.parametrize("body", [
        {"amount": 5, "phone": "+918446307145"},
        {"amount": 5, "phone": "  +918446307145  "},          # whitespace
        {"amount": 5, "phone": "918446307145"},               # no plus (should fail normalization or remain unverified either way)
        {"amount": 5, "phone": "+91 84463 07145"},            # spaces
        {"amount": 5, "phone": "+91-8446-307145"},            # dashes
        {"amount": 5, "email": "TEST_ADMIN@Example.COM"},     # uppercase not-really-admin email
    ])
    def test_pay_order_never_escalates(self, normal_user, body):
        uid, hdrs = normal_user
        before = _db.users.find_one({"id": uid}, {"_id": 0})
        orig_phone = before.get("phone")
        orig_email = before.get("email")
        r = requests.post(f"{BASE_URL}/api/pay/order", json=body, headers=hdrs, timeout=30)
        # Order creation may 200 or fail (Razorpay ratelimit / normalizer reject),
        # but under NO outcome should the user doc gain the admin identifier as
        # a VERIFIED field, and the verified fields must be UNCHANGED.
        user = _db.users.find_one({"id": uid}, {"_id": 0})
        assert user.get("phone") == orig_phone, f"verified phone changed! {orig_phone} -> {user.get('phone')} body={body}"
        assert user.get("email") == orig_email, f"verified email changed! {orig_email} -> {user.get('email')} body={body}"
        assert user.get("phone") != ADMIN_ID, f"escalation via phone! body={body}"
        # If the order actually 200'd, admin identifier is allowed to land in billing_*
        # (unverified) fields only. Ensure it did NOT bleed into email/phone.

    def test_wallet_balance_still_reports_not_admin_after_trying(self, normal_user):
        uid, hdrs = normal_user
        # Fire the escalation attempt
        requests.post(f"{BASE_URL}/api/pay/order",
                      json={"amount": 5, "phone": ADMIN_ID},
                      headers=hdrs, timeout=30)
        # Now check wallet balance says is_admin False + enforcement enabled
        r = requests.get(f"{BASE_URL}/api/wallet/balance",
                         params={"device_id": f"user:{uid}"},
                         headers=hdrs, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("is_admin") is False, f"user became admin! {data}"

    def test_real_admin_still_admin(self, real_admin_user):
        uid, hdrs = real_admin_user
        r = requests.get(f"{BASE_URL}/api/wallet/balance",
                         params={"device_id": f"user:{uid}"},
                         headers=hdrs, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("is_admin") is True, f"real admin lost admin! {data}"
        assert data.get("enforcement_enabled") is False


# ================= SEC-002 =================

class TestSEC002AuthRequired:

    @pytest.mark.parametrize("method,path", [
        ("GET", "/api/history"),
        ("GET", "/api/analysis/nonexistent-id"),
        ("DELETE", "/api/analysis/nonexistent-id"),
    ])
    def test_no_auth_returns_401(self, method, path):
        r = requests.request(method, f"{BASE_URL}{path}", timeout=15)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code} (expected 401)"

    @pytest.mark.parametrize("bearer", [
        "garbage",
        "Bearer_wrong_format",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.wrong",
    ])
    def test_bad_token_401(self, bearer):
        r = requests.get(f"{BASE_URL}/api/history",
                         headers={"Authorization": f"Bearer {bearer}"}, timeout=15)
        assert r.status_code == 401

    def test_token_with_wrong_secret_401(self):
        bad_tok = au.create_session_token("some-user", "not-the-real-secret")
        r = requests.get(f"{BASE_URL}/api/history",
                         headers={"Authorization": f"Bearer {bad_tok}"}, timeout=15)
        assert r.status_code == 401

    def test_valid_history_works_no_messages_no_owner_hash(self):
        r = requests.get(f"{BASE_URL}/api/history", headers=AUTH_HEADERS, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "results" in data
        for row in data["results"]:
            assert "messages" not in row, "messages leaked in /history"
            assert "owner_hash" not in row, "owner_hash leaked in /history"

    def test_get_analysis_does_not_leak_owner_hash(self):
        # History is private per account now, so this has to be an analysis the
        # test user actually owns — seed one with their own owner hash.
        import hashlib
        import hmac as _hmac

        aid = f"TEST_sec16-own-{uuid.uuid4()}"
        _db.analyses.insert_one({
            "id": aid, "symbol": "TESTX", "status": "completed",
            "owner_hash": _hmac.new(
                JWT_SECRET.encode(), f"analysis-owner:{USER_ID}".encode(), hashlib.sha256
            ).hexdigest(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        r = requests.get(f"{BASE_URL}/api/analysis/{aid}",
                         headers=AUTH_HEADERS, timeout=15)
        assert r.status_code == 200, r.text
        doc = r.json()
        assert "owner_hash" not in doc, "owner_hash leaked in /analysis/{id}"
        assert "viewer_hashes" not in doc, "viewer_hashes leaked in /analysis/{id}"
        _db.analyses.delete_one({"id": aid})

    def test_delete_requires_session_and_works(self):
        import hashlib
        import hmac as _hmac

        # Seed an analysis owned by the test user to delete
        aid = f"TEST_sec16-del-{uuid.uuid4()}"
        _db.analyses.insert_one({
            "id": aid, "symbol": "TESTX", "status": "completed",
            "owner_hash": _hmac.new(
                JWT_SECRET.encode(), f"analysis-owner:{USER_ID}".encode(), hashlib.sha256
            ).hexdigest(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        # 401 without auth
        r = requests.delete(f"{BASE_URL}/api/analysis/{aid}", timeout=15)
        assert r.status_code == 401
        # 200 with auth
        r = requests.delete(f"{BASE_URL}/api/analysis/{aid}",
                            headers=AUTH_HEADERS, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True
        # 404 after delete
        r = requests.get(f"{BASE_URL}/api/analysis/{aid}",
                         headers=AUTH_HEADERS, timeout=15)
        assert r.status_code == 404


# ================= SEC-003 =================

class TestSEC003FailClosed:

    def test_fails_with_empty_secret(self):
        env = {**os.environ, "JWT_SECRET": "", "AUTH_REQUIRED_ENABLED": "true"}
        r = subprocess.run(
            [sys.executable, "-c", "import server"],
            cwd=str(BACKEND_DIR),
            env=env, capture_output=True, text=True, timeout=30,
        )
        assert r.returncode != 0
        assert "JWT_SECRET must be set" in (r.stderr + r.stdout)

    def test_fails_with_default_secret(self):
        env = {**os.environ,
               "JWT_SECRET": "dev-only-change-me",
               "AUTH_REQUIRED_ENABLED": "true"}
        r = subprocess.run(
            [sys.executable, "-c", "import server"],
            cwd=str(BACKEND_DIR),
            env=env, capture_output=True, text=True, timeout=30,
        )
        assert r.returncode != 0
        assert "JWT_SECRET must be set" in (r.stderr + r.stdout)


# ================= Payment regression =================

class TestPaymentRegression:

    def test_pay_order_200_with_plink(self):
        r = requests.post(f"{BASE_URL}/api/pay/order",
                          json={"amount": 5, "phone": "+15550001234",
                                "email": f"TEST_reg-{uuid.uuid4()}@example.com"},
                          headers=AUTH_HEADERS, timeout=30)
        # Razorpay may 429 under load — accept 502 as documented in iter15
        if r.status_code == 502:
            pytest.skip("razorpay rate-limited (documented)")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["order_id"].startswith("plink_")
        assert "rzp.io" in data["checkout_url"] or "razorpay.com" in data["checkout_url"]

    def test_pay_callback_forged_signature_400(self):
        # Callback expects razorpay_payment_link_id + signature
        r = requests.get(f"{BASE_URL}/api/pay/callback",
                         params={
                             "razorpay_payment_link_id": "plink_fake",
                             "razorpay_payment_link_reference_id": "ref_fake",
                             "razorpay_payment_link_status": "paid",
                             "razorpay_payment_id": "pay_fake",
                             "razorpay_signature": "deadbeef",
                         }, timeout=15, allow_redirects=False)
        # Endpoint returns HTML — status 400 (or 200 with error page depending)
        assert r.status_code in (400, 200)
        if r.status_code == 200:
            assert "signature" in r.text.lower() or "invalid" in r.text.lower() or "nothing was charged" in r.text.lower()

    def test_pay_callback_empty_200(self):
        r = requests.get(f"{BASE_URL}/api/pay/callback", timeout=15, allow_redirects=False)
        assert r.status_code == 200
        assert "nothing was charged" in r.text.lower() or "cancel" in r.text.lower() or "close" in r.text.lower()

    def test_webhook_invalid_signature_400(self):
        r = requests.post(f"{BASE_URL}/api/pay/webhook",
                          data=b'{"event":"payment.captured"}',
                          headers={"X-Razorpay-Signature": "bad",
                                   "Content-Type": "application/json"},
                          timeout=15)
        assert r.status_code == 400

    def test_pay_status_unknown_404(self):
        r = requests.get(f"{BASE_URL}/api/pay/status/plink_doesnotexist",
                         headers=AUTH_HEADERS, timeout=15)
        assert r.status_code == 404


def teardown_module(module):
    # Only ever clean up rows THIS process created. Under xdist the module is
    # split across workers, so a blanket ^TEST_sec16- delete here would wipe
    # another worker's still-in-use account and fail it with "User not found".
    for uid in _CREATED_USER_IDS:
        _db.users.delete_many({"id": uid})
        _db.wallets.delete_many({"device_id": f"user:{uid}"})
    _db.analyses.delete_many({"id": {"$regex": "^TEST_sec16-"}})
