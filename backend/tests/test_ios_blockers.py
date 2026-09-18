"""iOS App Store blockers: account deletion + real Sign in with Apple.

The Apple tests use REAL cryptography — a fresh RSA keypair per test class, a
token genuinely signed with it, and a synthetic JWKS built from the public
half. That way every rejection path (wrong audience, wrong issuer, expired,
unknown kid, forged signature) is proven against real signature verification
rather than a mock that would happily agree with anything.

What isn't covered here, deliberately: the live fetch of Apple's real JWKS
(appleid.apple.com isn't reachable from this environment). It lives in its own
function, `fetch_apple_jwks`, precisely so the untestable network call and the
fully-tested crypto don't have to be verified together.
"""
import base64
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv(Path(__file__).parent.parent.parent / "frontend" / ".env")

import auth as au  # noqa: E402
from pymongo import MongoClient  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or os.environ["PUBLIC_BASE_URL"]
).rstrip("/")
_db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]
AUDIENCE = "com.example.tradingagents"


def _b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _keypair(kid: str):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwk = {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
           "n": _b64(numbers.n), "e": _b64(numbers.e)}
    return key, jwk


def _sign(private_key, kid: str, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://appleid.apple.com",
        "aud": AUDIENCE,
        "sub": "001234.abcdef",
        "email": "someone@privaterelay.appleid.com",
        "iat": now,
        "exp": now + 600,
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


class TestVerifyAppleIdToken:
    KID = "test-kid-1"

    @classmethod
    def setup_class(cls):
        cls.key, cls.jwk = _keypair(cls.KID)
        cls.jwks = [cls.jwk]

    def test_accepts_a_validly_signed_token(self):
        token = _sign(self.key, self.KID)
        claims = au.verify_apple_id_token(token, AUDIENCE, self.jwks)
        assert claims == {"sub": "001234.abcdef", "email": "someone@privaterelay.appleid.com"}

    def test_rejects_the_wrong_audience(self):
        token = _sign(self.key, self.KID, aud="com.someone.else")
        assert au.verify_apple_id_token(token, AUDIENCE, self.jwks) is None

    def test_rejects_a_non_apple_issuer(self):
        token = _sign(self.key, self.KID, iss="https://evil.example.com")
        assert au.verify_apple_id_token(token, AUDIENCE, self.jwks) is None

    def test_rejects_an_expired_token(self):
        past = int(time.time()) - 3600
        token = _sign(self.key, self.KID, iat=past, exp=past + 60)
        assert au.verify_apple_id_token(token, AUDIENCE, self.jwks) is None

    def test_rejects_an_unknown_kid(self):
        token = _sign(self.key, "some-other-kid")
        assert au.verify_apple_id_token(token, AUDIENCE, self.jwks) is None

    def test_rejects_a_token_signed_with_a_different_private_key(self):
        # What a real forgery looks like: right kid, wrong signing key.
        attacker_key, _ = _keypair(self.KID)
        token = _sign(attacker_key, self.KID)
        assert au.verify_apple_id_token(token, AUDIENCE, self.jwks) is None

    def test_rejects_garbage_without_raising(self):
        for bad in ("", "not-a-token", "a.b.c", None):
            assert au.verify_apple_id_token(bad, AUDIENCE, self.jwks) is None

    def test_rejects_when_the_jwks_is_empty(self):
        token = _sign(self.key, self.KID)
        assert au.verify_apple_id_token(token, AUDIENCE, []) is None

    def test_handles_a_token_with_no_email_claim(self):
        # Apple only sends email on the FIRST authentication.
        token = jwt.encode(
            {"iss": "https://appleid.apple.com", "aud": AUDIENCE, "sub": "001234.abcdef",
             "iat": int(time.time()), "exp": int(time.time()) + 600},
            self.key, algorithm="RS256", headers={"kid": self.KID},
        )
        claims = au.verify_apple_id_token(token, AUDIENCE, self.jwks)
        assert claims == {"sub": "001234.abcdef", "email": None}

    def test_rejects_a_token_with_no_sub(self):
        token = _sign(self.key, self.KID, sub=None)
        assert au.verify_apple_id_token(token, AUDIENCE, self.jwks) is None


def _make_account(email: str) -> tuple:
    uid = f"deltest-{uuid.uuid4()}"
    _db.users.insert_one({
        "id": uid, "phone": None, "email": email, "free_credits_remaining": 3,
        "consent": {"agreed": True, "agreed_at": "2026-01-01T00:00:00+00:00", "version": "1.0"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _db.wallets.insert_one({"device_id": f"user:{uid}", "balance": 7.5})
    token = au.create_session_token(uid, os.environ["JWT_SECRET"])
    return uid, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


class TestDeleteAccount:
    def test_deletes_the_user_and_wallet_and_kills_the_session(self):
        uid, headers = _make_account(f"del-{uuid.uuid4().hex[:8]}@example.com")
        try:
            assert _db.wallets.find_one({"device_id": f"user:{uid}"}) is not None
            assert requests.get(f"{BASE_URL}/api/auth/me", headers=headers, timeout=15).status_code == 200

            r = requests.delete(f"{BASE_URL}/api/account", headers=headers, timeout=20)
            assert r.status_code == 200, r.text
            assert r.json() == {"deleted": True}

            assert _db.users.find_one({"id": uid}) is None
            assert _db.wallets.find_one({"device_id": f"user:{uid}"}) is None
            # The token itself is now a dangling reference — it must not work.
            assert requests.get(f"{BASE_URL}/api/auth/me", headers=headers, timeout=15).status_code == 401
            assert requests.get(f"{BASE_URL}/api/wallet/balance", headers=headers, timeout=15).status_code in (400, 401)
        finally:
            _db.users.delete_one({"id": uid})
            _db.wallets.delete_one({"device_id": f"user:{uid}"})

    def test_keeps_payment_records_for_bookkeeping(self):
        uid, headers = _make_account(f"del-{uuid.uuid4().hex[:8]}@example.com")
        ref = f"deltest_{uuid.uuid4().hex[:10]}"
        _db.payments.insert_one({
            "reference_id": ref, "razorpay_payment_link_id": f"plink_{ref}",
            "wallet_key": f"user:{uid}", "amount": 5.0, "currency": "USD", "status": "captured",
        })
        try:
            assert requests.delete(f"{BASE_URL}/api/account", headers=headers, timeout=20).status_code == 200
            assert _db.payments.find_one({"reference_id": ref}) is not None
        finally:
            _db.payments.delete_one({"reference_id": ref})
            _db.users.delete_one({"id": uid})

    def test_a_fresh_signup_with_the_same_email_is_clean(self):
        email = f"reuse-{uuid.uuid4().hex[:8]}@example.com"
        uid, headers = _make_account(email)
        assert requests.delete(f"{BASE_URL}/api/account", headers=headers, timeout=20).status_code == 200
        try:
            uid2, headers2 = _make_account(email)
            assert uid2 != uid
            me = requests.get(f"{BASE_URL}/api/auth/me", headers=headers2, timeout=15)
            assert me.status_code == 200, me.text
            assert me.json()["email"] == email
            # No stale wallet balance carried over.
            bal = requests.get(f"{BASE_URL}/api/wallet/balance", headers=headers2, timeout=15).json()
            assert bal["balance"] == 7.5  # the fixture's own wallet, not the deleted one's
            _db.users.delete_one({"id": uid2})
            _db.wallets.delete_one({"device_id": f"user:{uid2}"})
        finally:
            _db.users.delete_one({"id": uid})

    def test_requires_a_session(self):
        assert requests.delete(f"{BASE_URL}/api/account", timeout=15).status_code == 401
        bad = {"Authorization": "Bearer not-a-real-token"}
        assert requests.delete(f"{BASE_URL}/api/account", headers=bad, timeout=15).status_code == 401


class TestAppleEndpointGuard:
    def test_returns_501_until_the_services_id_is_configured(self):
        if os.environ.get("APPLE_SERVICES_ID"):
            pytest.skip("APPLE_SERVICES_ID is configured — the 501 guard no longer applies")
        r = requests.post(f"{BASE_URL}/api/auth/apple", json={"token": "anything"}, timeout=15)
        assert r.status_code == 501, r.text
        assert "not configured" in r.json()["detail"].lower()
