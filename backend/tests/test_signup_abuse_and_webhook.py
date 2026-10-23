"""Free-credit farming guard, and the webhook's share of the injection fix.

A brand-new account starts with wal.FREE_CREDITS_ON_SIGNUP real LLM analyses,
so every disposable email address is worth that much in free compute. Free
credits are per ACCOUNT, not per device, so rotating a device id alone grants
nothing — the farming path is creating another account. The device id and the
client IP are the two signals for spotting the same person doing that
repeatedly.

The deliberate shape of the fix: exceeding a cap costs FREE CREDITS, never
access. The account is still created and fully usable. Blocking sign-in on a
shared office or carrier-NAT address would lock out genuine users, which is a
worse outcome than someone getting ten free analyses.
"""
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
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
import wallet as wal  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("PUBLIC_BASE_URL")
    or "http://localhost:8001"
).rstrip("/")

_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ["DB_NAME"]]

_IDENTIFIERS = []
_DEVICES = []
_IPS = []


def seed_otp(identifier: str, code: str = "123456"):
    id_type, normalized = au.normalize_identifier(identifier)
    _IDENTIFIERS.append(normalized)
    _db.otp_requests.insert_one({
        "id": str(uuid.uuid4()),
        "identifier": normalized,
        "identifier_type": id_type,
        "otp_hash": au.hash_otp(code),
        "attempts": 0,
        "verified": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return normalized


def fresh_email():
    return f"TEST-sec22-{uuid.uuid4().hex[:10]}@example.com"


def fresh_device():
    d = f"TEST_sec22-dev-{uuid.uuid4()}"
    _DEVICES.append(d)
    return d


def fresh_ip():
    ip = f"203.0.113.{uuid.uuid4().int % 250 + 1}"
    _IPS.append(ip)
    return ip


def sign_up(email, device_id=None, ip=None, code="123456"):
    """A complete new-account sign-in through the real OTP endpoint."""
    seed_otp(email, code)
    headers = {"Content-Type": "application/json"}
    if ip:
        # The ingress sets this; the guard reads the left-most entry.
        headers["X-Forwarded-For"] = ip
    body = {"identifier": email, "otp": code}
    if device_id:
        body["device_id"] = device_id
    return requests.post(f"{BASE_URL}/api/auth/otp/verify", json=body, headers=headers, timeout=30)


def credits_of(email):
    user = _db.users.find_one({"email": email.lower()})
    return None if not user else int(user.get("free_credits_remaining") or 0)


def cleanup(email):
    email = email.lower()
    user = _db.users.find_one({"email": email})
    if user:
        _db.free_credit_grants.delete_many({"user_id": user["id"]})
        _db.wallets.delete_many({"device_id": f"user:{user['id']}"})
        _db.users.delete_many({"id": user["id"]})
    _db.otp_requests.delete_many({"identifier": email})


class TestFirstAccountStillGetsItsCredits:
    def test_a_genuine_new_signup_is_unaffected(self):
        email, device = fresh_email(), fresh_device()
        try:
            r = sign_up(email, device, fresh_ip())
            assert r.status_code == 200, r.text
            assert credits_of(email) == wal.FREE_CREDITS_ON_SIGNUP
        finally:
            cleanup(email)

    def test_a_signup_with_no_device_id_still_gets_credits(self):
        """The web app doesn't always have a device id — absence of a signal
        must not be treated as abuse."""
        email = fresh_email()
        try:
            r = sign_up(email, None, fresh_ip())
            assert r.status_code == 200, r.text
            assert credits_of(email) == wal.FREE_CREDITS_ON_SIGNUP
        finally:
            cleanup(email)

    def test_signing_in_again_to_an_existing_account_does_not_regrant(self):
        email, device = fresh_email(), fresh_device()
        try:
            assert sign_up(email, device, fresh_ip()).status_code == 200
            _db.users.update_one({"email": email.lower()}, {"$set": {"free_credits_remaining": 2}})
            assert sign_up(email, device, fresh_ip()).status_code == 200
            assert credits_of(email) == 2, "an existing account was re-granted free credits"
        finally:
            cleanup(email)


class TestDeviceCannotSeedASecondAccount:
    def test_a_second_account_from_the_same_device_gets_no_free_credits(self):
        device = fresh_device()
        first, second = fresh_email(), fresh_email()
        try:
            assert sign_up(first, device, fresh_ip()).status_code == 200
            assert credits_of(first) == wal.FREE_CREDITS_ON_SIGNUP

            r = sign_up(second, device, fresh_ip())
            # Still a successful sign-in — the account works, it just pays.
            assert r.status_code == 200, r.text
            assert credits_of(second) == 0, "the same device farmed a second free allowance"
        finally:
            cleanup(first)
            cleanup(second)

    def test_a_withheld_grant_does_not_consume_the_allowance_again(self):
        """Only real grants are recorded, so a denied attempt can't inflate
        the counters it was already denied by."""
        device = fresh_device()
        first, second = fresh_email(), fresh_email()
        try:
            sign_up(first, device, fresh_ip())
            sign_up(second, device, fresh_ip())
            user = _db.users.find_one({"email": second.lower()})
            assert _db.free_credit_grants.count_documents({"user_id": user["id"]}) == 0
            assert _db.free_credit_grants.count_documents({"device_id": device}) == 1
        finally:
            cleanup(first)
            cleanup(second)

    def test_rotating_the_device_id_is_still_caught_by_the_address_cap(self):
        """This is the actual attack the guard is named for: a new device id
        every time. The per-network cap is the backstop."""
        ip = fresh_ip()
        emails = [fresh_email() for _ in range(wal.FREE_CREDIT_GRANTS_PER_IP_PER_DAY + 2)]
        try:
            granted = []
            for email in emails:
                r = sign_up(email, fresh_device(), ip)
                assert r.status_code == 200, r.text
                granted.append(credits_of(email))
            assert granted.count(wal.FREE_CREDITS_ON_SIGNUP) == wal.FREE_CREDIT_GRANTS_PER_IP_PER_DAY, granted
            assert granted[-1] == 0, granted
            assert granted[-2] == 0, granted
        finally:
            for email in emails:
                cleanup(email)

    def test_a_different_address_is_not_penalised_by_someone_elses_abuse(self):
        busy_ip = fresh_ip()
        abusers = [fresh_email() for _ in range(wal.FREE_CREDIT_GRANTS_PER_IP_PER_DAY)]
        innocent = fresh_email()
        try:
            for email in abusers:
                sign_up(email, fresh_device(), busy_ip)
            assert sign_up(innocent, fresh_device(), fresh_ip()).status_code == 200
            assert credits_of(innocent) == wal.FREE_CREDITS_ON_SIGNUP
        finally:
            for email in abusers:
                cleanup(email)
            cleanup(innocent)

    def test_yesterdays_grants_do_not_count_against_today(self):
        """The address cap is per day, not forever — an office shouldn't be
        permanently barred."""
        ip = fresh_ip()
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        stale_ids = []
        for _ in range(wal.FREE_CREDIT_GRANTS_PER_IP_PER_DAY + 2):
            uid = f"TEST_sec22-stale-{uuid.uuid4()}"
            stale_ids.append(uid)
            _db.free_credit_grants.insert_one({"user_id": uid, "device_id": None, "ip": ip, "created_at": old})
        email = fresh_email()
        try:
            assert sign_up(email, fresh_device(), ip).status_code == 200
            assert credits_of(email) == wal.FREE_CREDITS_ON_SIGNUP
        finally:
            _db.free_credit_grants.delete_many({"user_id": {"$in": stale_ids}})
            cleanup(email)


class TestWebhookInjectionHardening:
    """Same class as the /pay/callback fix: a dict where a string belongs would
    be read by MongoDB as a query operator. This body is signature-verified, so
    it can't be forged without the secret — hence defence in depth, verified by
    confirming an unsigned or operator-shaped body settles nothing."""

    def seed_pending(self):
        link_id = f"TEST_sec22-plink-{uuid.uuid4()}"
        wallet_key = f"user:TEST_sec22-{uuid.uuid4()}"
        _db.payments.insert_one({
            "razorpay_payment_link_id": link_id,
            "reference_id": f"TEST_sec22-ref-{uuid.uuid4()}",
            "wallet_key": wallet_key,
            "amount": 25.0,
            "currency": "USD",
            "status": "created",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        _db.wallets.insert_one({"device_id": wallet_key, "balance": 0.0, "currency": "USD"})
        return link_id, wallet_key

    @pytest.mark.parametrize("body", [
        {"event": "payment_link.paid",
         "payload": {"payment_link": {"entity": {"id": {"$ne": ""}, "amount_paid": 2500, "currency": "USD"}},
                     "payment": {"entity": {"id": "pay_x", "order_id": "order_x"}}}},
        {"event": "payment.captured",
         "payload": {"payment": {"entity": {"id": {"$ne": ""}, "order_id": {"$ne": ""}, "amount": 2500}}}},
        ["not", "a", "dict"],
        {"event": "payment.captured", "payload": {"payment": {"entity": {"order_id": {"$exists": True}}}}},
    ])
    def test_an_operator_payload_settles_nothing(self, body):
        link_id, wallet_key = self.seed_pending()
        try:
            r = requests.post(
                f"{BASE_URL}/api/pay/webhook",
                data=json.dumps(body),
                headers={"Content-Type": "application/json", "X-Razorpay-Signature": "not-a-real-signature"},
                timeout=25,
            )
            # Unsigned, so rejected before parsing — the signature check is the
            # first gate and must stay that way.
            assert r.status_code == 400, r.text
            row = _db.payments.find_one({"razorpay_payment_link_id": link_id})
            assert row["status"] == "created"
            assert float((_db.wallets.find_one({"device_id": wallet_key}) or {}).get("balance") or 0) == 0.0
        finally:
            _db.payments.delete_many({"razorpay_payment_link_id": link_id})
            _db.wallets.delete_many({"device_id": wallet_key})

    def test_an_unsigned_webhook_is_always_rejected(self):
        r = requests.post(f"{BASE_URL}/api/pay/webhook", json={"event": "payment.captured"}, timeout=25)
        assert r.status_code == 400
        assert "signature" in r.text.lower()

    def test_the_string_coercion_is_actually_in_place(self):
        """The signature gate means the operator payloads above never reach the
        parser, so assert the coercion directly on the source — otherwise this
        whole class would pass even if the fix were reverted."""
        src = (BACKEND_DIR / "routes" / "payments.py").read_text()
        webhook = src[src.index("async def pay_webhook"):src.index("async def pay_status")]
        assert 'isinstance(entity.get("id"), str)' in webhook
        assert 'isinstance(entity.get("order_id"), str)' in webhook
        assert 'isinstance(link_entity.get("id"), str)' in webhook
        assert 'razorpay_payment_link_id": link_id' in webhook
        assert 'link_entity["id"]' not in webhook, "raw body value still used in a lookup"


def teardown_module():
    # Process-scoped only: a blanket regex delete would wipe another xdist
    # worker's in-flight accounts.
    for identifier in _IDENTIFIERS:
        cleanup(identifier)
    for device in _DEVICES:
        _db.free_credit_grants.delete_many({"device_id": device})
        _db.wallets.delete_many({"device_id": device})
    for ip in _IPS:
        _db.free_credit_grants.delete_many({"ip": ip})
