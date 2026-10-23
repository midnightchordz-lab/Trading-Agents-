"""SECURITY_FIXES.md remediation — the findings that were still open.

Each one was checked against the CURRENT code before being touched, as the
document itself demands, because the document was written against the old
pre-refactor `server.py`:

  Finding 1 (admin self-promotion via /pay/order)  — ALREADY FIXED, differently
      and better: unverified contact goes to `billing_email` / `billing_phone`
      and never over the verified `email` / `phone` the admin allowlist matches
      on. Covered by tests/test_security_fixes_iter16.py. Not re-touched.
  Finding 2 (unauthenticated read/delete of analyses) — ALREADY FIXED, and
      since hardened to per-account private history via a keyed `owner_hash`
      (no identity written onto an analysis, so the privacy commitment the
      document flagged is kept). Covered by tests/test_private_history.py.
  Finding 3c (charge race) — ALREADY FIXED, currency-aware. Covered by
      tests/test_security_fixes_iter20.py.

  Applied here: 3a (OTP verify race), 3b (wallet merge race), 4 (NoSQL
  injection in the payment callback), 6 (JWT_SECRET fail-closed).
"""
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
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

_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ["DB_NAME"]]

_CREATED_IDENTIFIERS = []


def seed_otp(identifier: str, code: str = "123456"):
    """A pending, unverified OTP for an identifier — the state a user is in
    between requesting a code and typing it."""
    rec_id = str(uuid.uuid4())
    id_type, normalized = au.normalize_identifier(identifier)
    _CREATED_IDENTIFIERS.append(normalized)
    _db.otp_requests.insert_one({
        "id": rec_id,
        "identifier": normalized,
        "identifier_type": id_type,
        "otp_hash": au.hash_otp(code),
        "attempts": 0,
        "verified": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return rec_id, normalized


def cleanup_identifier(identifier: str, device_id=None):
    user = _db.users.find_one({"$or": [{"email": identifier}, {"phone": identifier}]})
    if user:
        _db.wallets.delete_many({"device_id": f"user:{user['id']}"})
        _db.users.delete_many({"id": user["id"]})
    _db.otp_requests.delete_many({"identifier": identifier})
    if device_id:
        _db.wallets.delete_many({"device_id": device_id})


def balance_of_device(device_id):
    return float((_db.wallets.find_one({"device_id": device_id}) or {}).get("balance") or 0.0)


def balance_of_identifier(identifier):
    user = _db.users.find_one({"$or": [{"email": identifier}, {"phone": identifier}]})
    if not user:
        return None
    return float((_db.wallets.find_one({"device_id": f"user:{user['id']}"}) or {}).get("balance") or 0.0)


class TestFinding3aOtpVerifyIsSingleUse:
    """A valid code must be redeemable exactly once, even when several
    requests carrying it arrive at the same instant — otherwise each one
    triggers its own device-wallet merge."""

    def verify_many(self, identifier, code, device_id, n):
        def run(_):
            return requests.post(
                f"{BASE_URL}/api/auth/otp/verify",
                json={"identifier": identifier, "otp": code, "device_id": device_id},
                timeout=30,
            )
        with ThreadPoolExecutor(max_workers=n) as pool:
            return list(pool.map(run, range(n)))

    def test_only_one_of_three_simultaneous_verifies_succeeds(self):
        identifier = f"TEST-sec21-{uuid.uuid4().hex[:10]}@example.com"
        device_id = f"TEST_sec21-dev-{uuid.uuid4()}"
        seed_otp(identifier)
        _db.wallets.insert_one({"device_id": device_id, "balance": 10.0, "currency": "USD"})
        try:
            codes = [r.status_code for r in self.verify_many(identifier, "123456", device_id, 3)]
            assert codes.count(200) == 1, f"a single code was redeemed {codes.count(200)} times: {codes}"
            assert all(c in (200, 400) for c in codes), codes
        finally:
            cleanup_identifier(identifier.lower(), device_id)

    def test_the_device_wallet_is_merged_exactly_once(self):
        """The real damage: three merges of one $10 device wallet would leave
        $30 of balance that nobody paid for."""
        identifier = f"TEST-sec21-{uuid.uuid4().hex[:10]}@example.com"
        device_id = f"TEST_sec21-dev-{uuid.uuid4()}"
        seed_otp(identifier)
        _db.wallets.insert_one({"device_id": device_id, "balance": 10.0, "currency": "USD"})
        try:
            self.verify_many(identifier, "123456", device_id, 3)
            merged = balance_of_identifier(identifier.lower())
            assert merged == pytest.approx(10.0), f"expected exactly 10.0 merged, got {merged}"
            assert balance_of_device(device_id) == 0.0, "device wallet should be emptied, not re-merged"
        finally:
            cleanup_identifier(identifier.lower(), device_id)

    def test_reusing_a_code_after_a_successful_verify_is_rejected(self):
        identifier = f"TEST-sec21-{uuid.uuid4().hex[:10]}@example.com"
        seed_otp(identifier)
        try:
            first = requests.post(f"{BASE_URL}/api/auth/otp/verify",
                                  json={"identifier": identifier, "otp": "123456"}, timeout=25)
            assert first.status_code == 200, first.text
            second = requests.post(f"{BASE_URL}/api/auth/otp/verify",
                                   json={"identifier": identifier, "otp": "123456"}, timeout=25)
            assert second.status_code == 400, second.text
        finally:
            cleanup_identifier(identifier.lower())

    def test_a_wrong_code_still_fails_normally(self):
        identifier = f"TEST-sec21-{uuid.uuid4().hex[:10]}@example.com"
        seed_otp(identifier)
        try:
            r = requests.post(f"{BASE_URL}/api/auth/otp/verify",
                              json={"identifier": identifier, "otp": "000000"}, timeout=25)
            assert r.status_code == 400
            assert "Incorrect" in r.json()["detail"]
        finally:
            cleanup_identifier(identifier.lower())


class TestFinding3bWalletMergeIsAtomic:
    def test_a_zero_balance_device_wallet_is_a_no_op(self):
        identifier = f"TEST-sec21-{uuid.uuid4().hex[:10]}@example.com"
        device_id = f"TEST_sec21-dev-{uuid.uuid4()}"
        seed_otp(identifier)
        _db.wallets.insert_one({"device_id": device_id, "balance": 0.0})
        try:
            r = requests.post(f"{BASE_URL}/api/auth/otp/verify",
                              json={"identifier": identifier, "otp": "123456", "device_id": device_id},
                              timeout=25)
            assert r.status_code == 200, r.text
            assert balance_of_identifier(identifier.lower()) in (None, 0.0)
        finally:
            cleanup_identifier(identifier.lower(), device_id)

    def test_the_legitimate_merge_still_moves_the_money(self):
        identifier = f"TEST-sec21-{uuid.uuid4().hex[:10]}@example.com"
        device_id = f"TEST_sec21-dev-{uuid.uuid4()}"
        seed_otp(identifier)
        _db.wallets.insert_one({"device_id": device_id, "balance": 7.5, "currency": "USD"})
        try:
            r = requests.post(f"{BASE_URL}/api/auth/otp/verify",
                              json={"identifier": identifier, "otp": "123456", "device_id": device_id},
                              timeout=25)
            assert r.status_code == 200, r.text
            assert balance_of_identifier(identifier.lower()) == pytest.approx(7.5)
            assert balance_of_device(device_id) == 0.0
        finally:
            cleanup_identifier(identifier.lower(), device_id)

    def test_merging_into_an_existing_account_balance_adds_rather_than_replaces(self):
        """Credited with $inc, so it can't be computed from a stale read."""
        identifier = f"TEST-sec21-{uuid.uuid4().hex[:10]}@example.com"
        device_id = f"TEST_sec21-dev-{uuid.uuid4()}"
        seed_otp(identifier)
        uid = f"TEST_sec21-{uuid.uuid4()}"
        _db.users.insert_one({"id": uid, "email": identifier.lower(),
                              "created_at": datetime.now(timezone.utc).isoformat()})
        _db.wallets.insert_one({"device_id": f"user:{uid}", "balance": 3.0, "currency": "USD"})
        _db.wallets.insert_one({"device_id": device_id, "balance": 4.0})
        try:
            r = requests.post(f"{BASE_URL}/api/auth/otp/verify",
                              json={"identifier": identifier, "otp": "123456", "device_id": device_id},
                              timeout=25)
            assert r.status_code == 200, r.text
            assert balance_of_identifier(identifier.lower()) == pytest.approx(7.0)
        finally:
            _db.wallets.delete_many({"device_id": f"user:{uid}"})
            _db.users.delete_many({"id": uid})
            cleanup_identifier(identifier.lower(), device_id)


class TestFinding4NoSqlInjectionInThePaymentCallback:
    """A JSON body can put an operator where a string belongs. `{"$ne": ""}`
    as an order id would make MongoDB match an ARBITRARY payment record — i.e.
    settle someone else's pending payment."""

    def seed_pending_payment(self):
        order_id = f"TEST_sec21-order-{uuid.uuid4()}"
        wallet_key = f"user:TEST_sec21-{uuid.uuid4()}"
        _db.payments.insert_one({
            "razorpay_order_id": order_id,
            "wallet_key": wallet_key,
            "amount": 25.0,
            "currency": "USD",
            "status": "created",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        _db.wallets.insert_one({"device_id": wallet_key, "balance": 0.0, "currency": "USD"})
        return order_id, wallet_key

    @pytest.mark.parametrize("payload", [
        {"razorpay_order_id": {"$ne": ""}, "razorpay_payment_id": "pay_x", "razorpay_signature": "sig"},
        {"razorpay_order_id": {"$exists": True}, "razorpay_payment_id": "pay_x", "razorpay_signature": "sig"},
        {"razorpay_order_id": {"$gt": ""}, "razorpay_payment_id": {"$ne": ""}, "razorpay_signature": {"$ne": ""}},
        {"razorpay_payment_link_id": {"$ne": ""}},
    ])
    def test_an_operator_payload_never_settles_a_pending_payment(self, payload):
        order_id, wallet_key = self.seed_pending_payment()
        try:
            r = requests.post(f"{BASE_URL}/api/pay/callback", json=payload, timeout=25)
            # 400 + the "couldn't verify that payment" page is the correct
            # answer for an unusable callback; what must never happen is a 5xx
            # or a settlement.
            assert r.status_code in (200, 400), r.text
            row = _db.payments.find_one({"razorpay_order_id": order_id})
            assert row["status"] == "created", "an injected operator matched a real payment record"
            assert float((_db.wallets.find_one({"device_id": wallet_key}) or {}).get("balance") or 0) == 0.0, \
                "an injected operator credited a wallet"
        finally:
            _db.payments.delete_many({"razorpay_order_id": order_id})
            _db.wallets.delete_many({"device_id": wallet_key})

    def test_a_list_body_does_not_crash_the_callback(self):
        r = requests.post(f"{BASE_URL}/api/pay/callback", json=["not", "a", "dict"], timeout=25)
        assert r.status_code < 500, r.text

    def test_an_unknown_string_order_id_behaves_as_before(self):
        r = requests.post(
            f"{BASE_URL}/api/pay/callback",
            json={"razorpay_order_id": "order_does_not_exist", "razorpay_payment_id": "pay_x",
                  "razorpay_signature": "sig"},
            timeout=25,
        )
        # A plain-string order id that doesn't exist gets the same "couldn't
        # verify" page as before the fix — legitimate behaviour is unchanged.
        assert r.status_code == 400
        assert "couldn" in r.text.lower()

    def test_a_cancelled_redirect_with_no_fields_still_renders(self):
        r = requests.get(f"{BASE_URL}/api/pay/callback", timeout=25)
        assert r.status_code == 200
        assert "<html" in r.text.lower()


class TestFinding6JwtSecretFailsClosed:
    """An unset, short or default secret means anyone can forge a session for
    any account, so the app must refuse to start — loudly, not silently."""

    def import_with(self, secret, auth_required="true"):
        env = {**os.environ, "JWT_SECRET": secret, "AUTH_REQUIRED_ENABLED": auth_required}
        return subprocess.run(
            [sys.executable, "-c", "import server"],
            cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True, timeout=60,
        )

    @pytest.mark.parametrize("secret", ["", "dev-only-change-me", "short", "a" * 31])
    def test_refuses_to_start_with_a_weak_secret(self, secret):
        r = self.import_with(secret)
        assert r.returncode != 0, f"started with secret {secret!r}"
        assert "JWT_SECRET must be set" in (r.stderr + r.stdout)

    def test_refuses_even_when_auth_enforcement_is_off(self):
        """Tokens are still issued (and owner hashes still keyed) with auth
        enforcement off, so the check can't be conditional on that flag."""
        r = self.import_with("", auth_required="false")
        assert r.returncode != 0
        assert "JWT_SECRET must be set" in (r.stderr + r.stdout)

    def test_starts_with_a_real_secret(self):
        r = self.import_with("x" * 64)
        assert r.returncode == 0, r.stderr[-800:]


def teardown_module():
    # Only ever clean up what THIS process created. Under xdist the module is
    # split across workers, and a blanket regex delete here would wipe another
    # worker's freshly-seeded OTP record mid-test (symptom: a first verify
    # failing with "no pending code").
    for identifier in _CREATED_IDENTIFIERS:
        cleanup_identifier(identifier)
