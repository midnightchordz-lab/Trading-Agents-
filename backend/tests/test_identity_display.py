"""The account must show the identity it signed in WITH.

Reported bug: signing in with +91829… showed someone's gmail address. Cause: a
pre-fix `/pay/order` wrote the email typed for a Razorpay receipt onto
`users.email`, and the app displayed `email || phone`. Now the backend decides
which identity is the sign-in one, and the legacy rows are migrated.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth as au  # noqa: E402
from tests.async_loop import run_async  # noqa: E402
from routes.auth_routes import identity_for, identity_type_for  # noqa: E402
from server import migrate_unverified_billing_email  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")
BASE = "http://localhost:8001/api"
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-change-me")
db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]

_CREATED: list[str] = []


def seed(**fields):
    uid = f"TEST_ident-{uuid.uuid4()}"
    db.users.insert_one({
        "id": uid,
        "phone": None,
        "email": None,
        "google_sub": None,
        "apple_sub": None,
        "consent": {"agreed": True, "agreed_at": datetime.now(timezone.utc).isoformat(), "version": "1.0"},
        "created_at": datetime.now(timezone.utc).isoformat(),
        **fields,
    })
    _CREATED.append(uid)
    return uid, {"Authorization": f"Bearer {au.create_session_token(uid, JWT_SECRET)}"}


def teardown_module():
    for uid in _CREATED:
        db.users.delete_one({"id": uid})


# --- the decision itself --------------------------------------------------
def test_stored_identity_type_is_authoritative():
    user = {"identity_type": "phone", "phone": "+918291026526", "email": "someone@gmail.com"}
    assert identity_type_for(user) == "phone"
    assert identity_for(user) == "+918291026526"


def test_legacy_phone_account_with_a_stray_email_reports_the_phone():
    """No identity_type, no Google — the email cannot have been verified."""
    user = {"phone": "+918291026526", "email": "someone@gmail.com"}
    assert identity_type_for(user) == "phone"
    assert identity_for(user) == "+918291026526"


def test_google_account_reports_its_email():
    user = {"google_sub": "1089", "email": "someone@gmail.com", "phone": None}
    assert identity_type_for(user) == "email"
    assert identity_for(user) == "someone@gmail.com"


def test_email_otp_account_reports_its_email():
    user = {"email": "someone@example.com", "phone": None}
    assert identity_type_for(user) == "email"
    assert identity_for(user) == "someone@example.com"


# --- /auth/me -------------------------------------------------------------
def test_auth_me_returns_the_phone_for_a_phone_signup():
    uid, headers = seed(phone=f"+9198{uuid.uuid4().int % 10**8:08d}", identity_type="phone",
                        billing_email="receipt@gmail.com")
    body = requests.get(f"{BASE}/auth/me", headers=headers, timeout=20).json()
    assert body["identity_type"] == "phone"
    assert body["identity"] == db.users.find_one({"id": uid})["phone"]
    # The receipt address is never presented as the account identity.
    assert body["identity"] != "receipt@gmail.com"
    assert body["email"] is None


def test_auth_me_returns_the_email_for_an_email_signup():
    _, headers = seed(email=f"{uuid.uuid4().hex[:8]}@example.com", identity_type="email")
    body = requests.get(f"{BASE}/auth/me", headers=headers, timeout=20).json()
    assert body["identity_type"] == "email"
    assert body["identity"] == body["email"]


def test_otp_verify_response_carries_the_identity():
    """The login response itself, not just /auth/me — the app renders from it."""
    identifier = f"+9198{uuid.uuid4().int % 10**8:08d}"
    _, canonical = au.normalize_identifier(identifier)
    record_id = str(uuid.uuid4())
    db.otp_requests.insert_one({
        "id": record_id,
        "identifier": canonical,
        "otp_hash": au.hash_otp("424242"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verified": False,
        "attempts": 0,
    })
    try:
        res = requests.post(f"{BASE}/auth/otp/verify",
                            json={"identifier": identifier, "otp": "424242"}, timeout=25)
        assert res.status_code == 200, res.text
        user = res.json()["user"]
        assert user["identity_type"] == "phone"
        assert user["identity"] == canonical
        db.users.delete_one({"id": user["id"]})
        db.wallets.delete_one({"device_id": f"user:{user['id']}"})
    finally:
        db.otp_requests.delete_one({"id": record_id})


# --- the migration --------------------------------------------------------
# Both cases run inside ONE asyncio.run: motor binds its client to the loop it
# first used, so a second asyncio.run would talk to a closed loop and the
# migration (which swallows its own errors) would appear to do nothing.
def verified_otp_row(identifier):
    """The migration now requires EVIDENCE that the phone is the real identity
    before it takes an email away — a verified otp_requests row, which a real
    phone signup always leaves behind (those rows are never pruned). Without
    this the account is "ambiguous" and correctly skipped, because the same
    shape is also a legacy EMAIL-OTP account carrying a stray phone, and
    stripping THAT email orphans the person's account.

    Setup only: the assertions below are unchanged."""
    db.otp_requests.insert_one({
        "id": str(uuid.uuid4()), "identifier": identifier, "identifier_type": "phone",
        "otp_hash": "x", "attempts": 0, "verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return identifier


def test_migration_moves_stray_emails_only():
    phone = verified_otp_row(f"+9198{uuid.uuid4().int % 10**8:08d}")
    phone_uid, phone_headers = seed(phone=phone, email="receipt@gmail.com")
    google_uid, _ = seed(google_sub="test-sub-1089", email="real@gmail.com")
    email_uid, _ = seed(email="otp@example.com")
    kept_phone = verified_otp_row(f"+9198{uuid.uuid4().int % 10**8:08d}")
    kept_uid, _ = seed(phone=kept_phone, email="second@gmail.com", billing_email="first@gmail.com")

    db.migrations.delete_one({"name": "billing_email_v2"})

    async def run_twice():
        await migrate_unverified_billing_email()
        # Idempotent: a restart must not undo or duplicate anything.
        await migrate_unverified_billing_email()

    run_async(run_twice())

    moved = db.users.find_one({"id": phone_uid})
    assert moved.get("email") is None
    assert moved["billing_email"] == "receipt@gmail.com"
    assert moved["identity_type"] == "phone"

    # A verified email must survive untouched — this is the half of the
    # migration that could silently sign people out of their own account.
    assert db.users.find_one({"id": google_uid})["email"] == "real@gmail.com"
    assert db.users.find_one({"id": email_uid})["email"] == "otp@example.com"

    # An address already recorded for billing is not overwritten.
    kept = db.users.find_one({"id": kept_uid})
    assert kept.get("email") is None
    assert kept["billing_email"] == "first@gmail.com"

    body = requests.get(f"{BASE}/auth/me", headers=phone_headers, timeout=20).json()
    assert body["identity"] == moved["phone"]
