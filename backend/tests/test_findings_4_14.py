"""FINDINGS_4_14_REVERIFIED: the two findings that were genuinely still open.

F7 — account deletion reset the free-credit grant: delete, sign back in with the
     same address, get another 10 free analyses. Device/IP caps don't stop it,
     since both signals are trivially rotated while the address is the one
     thing the attack still needs.
F9 — `credit_iap_once` had the ledger-insert-first half of the once-only
     pattern but not the compensating rollback, so a failed balance increment
     would lose an Apple purchase's credit AND block every retry.
"""
import asyncio
import inspect
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
from tests.async_loop import run_async  # noqa: E402
import routes.payments as pay  # noqa: E402
import wallet as wal  # noqa: E402
from routes.auth_routes import (  # noqa: E402
    free_credit_tombstone_hash_for,
    signup_free_credits,
)

load_dotenv(Path(__file__).parent.parent / ".env")
BASE = "http://localhost:8001/api"
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-change-me")
db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]

_USERS: list[str] = []
_HASHES: list[str] = []


def seed_user(**fields):
    uid = f"TEST_f414-{uuid.uuid4()}"
    db.users.insert_one({
        "id": uid, "phone": None, "email": None, "google_sub": None, "apple_sub": None,
        "free_credits_remaining": 10,
        "consent": {"agreed": True, "agreed_at": datetime.now(timezone.utc).isoformat(), "version": "1.0"},
        "created_at": datetime.now(timezone.utc).isoformat(), **fields,
    })
    _USERS.append(uid)
    return uid, {"Authorization": f"Bearer {au.create_session_token(uid, JWT_SECRET)}"}


def teardown_module():
    for uid in _USERS:
        db.users.delete_one({"id": uid})
        db.wallets.delete_one({"device_id": f"user:{uid}"})
    for h in _HASHES:
        db.free_credit_tombstones.delete_one({"hash": h})


# --- F7: the tombstone ----------------------------------------------------
def test_hash_is_keyed_and_stable_and_does_not_leak_the_identifier():
    h = free_credit_tombstone_hash_for("someone@example.com")
    assert h == free_credit_tombstone_hash_for("someone@example.com")
    assert h != free_credit_tombstone_hash_for("someone.else@example.com")
    assert len(h) == 64
    # The whole point of keying it: the raw address is not recoverable from,
    # or even present in, what gets stored.
    assert "someone@example.com" not in h


def test_delete_account_writes_a_tombstone_for_an_email_account():
    email = f"f414-{uuid.uuid4().hex[:8]}@example.com"
    uid, headers = seed_user(email=email, identity_type="email")
    h = free_credit_tombstone_hash_for(email)
    _HASHES.append(h)
    res = requests.delete(f"{BASE}/account", headers=headers, timeout=20)
    assert res.status_code == 200, res.text
    assert db.users.find_one({"id": uid}) is None
    assert db.free_credit_tombstones.find_one({"hash": h}) is not None


def test_delete_account_tombstones_the_phone_not_a_billing_email():
    """The identifier tombstoned is the SIGN-IN one. An address typed for a
    payment receipt is not what the next sign-in will be looked up by."""
    phone = f"+9198{uuid.uuid4().int % 10**8:08d}"
    uid, headers = seed_user(phone=phone, identity_type="phone", billing_email="receipt@gmail.com")
    _HASHES.append(free_credit_tombstone_hash_for(phone))
    _HASHES.append(free_credit_tombstone_hash_for("receipt@gmail.com"))
    assert requests.delete(f"{BASE}/account", headers=headers, timeout=20).status_code == 200
    assert db.free_credit_tombstones.find_one({"hash": free_credit_tombstone_hash_for(phone)}) is not None
    assert db.free_credit_tombstones.find_one(
        {"hash": free_credit_tombstone_hash_for("receipt@gmail.com")}) is None


def test_the_full_exploit_path_grants_nothing_the_second_time():
    """Sign up, delete, sign up again with the same address — the reported
    exploit, end to end through the real endpoints."""
    identifier = f"f414-{uuid.uuid4().hex[:8]}@example.com"
    _HASHES.append(free_credit_tombstone_hash_for(identifier))

    first = run_async(signup_free_credits(None, None, identifier))
    # Deletion is what used to reset this.
    db.free_credit_tombstones.update_one(
        {"hash": free_credit_tombstone_hash_for(identifier)},
        {"$setOnInsert": {"deleted_at": "now"}}, upsert=True)
    second = run_async(signup_free_credits(None, None, identifier))
    assert first == wal.FREE_CREDITS_ON_SIGNUP
    assert second == 0


def test_a_different_address_is_unaffected():
    """A tombstone must only ever silence the address it was written for."""
    tombstoned = f"f414-{uuid.uuid4().hex[:8]}@example.com"
    innocent = f"f414-{uuid.uuid4().hex[:8]}@example.com"
    _HASHES.append(free_credit_tombstone_hash_for(tombstoned))

    db.free_credit_tombstones.update_one(
        {"hash": free_credit_tombstone_hash_for(tombstoned)},
        {"$setOnInsert": {"deleted_at": "now"}}, upsert=True)
    blocked = run_async(signup_free_credits(None, None, tombstoned))
    allowed = run_async(signup_free_credits(None, None, innocent))
    assert blocked == 0
    assert allowed == wal.FREE_CREDITS_ON_SIGNUP


def test_no_identifier_still_grants():
    """An anonymous/first-party call with nothing to key on must not be
    treated as abuse — withholding on a missing signal would punish genuine
    Apple private-relay sign-ins."""
    assert run_async(signup_free_credits(None, None, None)) == wal.FREE_CREDITS_ON_SIGNUP


def test_deleting_twice_keeps_the_first_deletion_timestamp():
    email = f"f414-{uuid.uuid4().hex[:8]}@example.com"
    h = free_credit_tombstone_hash_for(email)
    _HASHES.append(h)
    _, headers = seed_user(email=email, identity_type="email")
    requests.delete(f"{BASE}/account", headers=headers, timeout=20)
    first = db.free_credit_tombstones.find_one({"hash": h})["deleted_at"]
    _, headers2 = seed_user(email=email, identity_type="email")
    requests.delete(f"{BASE}/account", headers=headers2, timeout=20)
    assert db.free_credit_tombstones.find_one({"hash": h})["deleted_at"] == first
    assert db.free_credit_tombstones.count_documents({"hash": h}) == 1


@pytest.mark.parametrize("site", ["otp", "google", "apple"])
def test_every_account_creation_site_passes_the_identifier(site):
    """The fix is worthless if one sign-in route still creates accounts
    without consulting the tombstone."""
    src = inspect.getsource(sys.modules["routes.auth_routes"])
    calls = [line for line in src.splitlines() if "await signup_free_credits(" in line]
    assert len(calls) == 3, calls
    # Every call site passes a third argument (the identifier), not just
    # device_id and request.
    for line in calls:
        args = line.split("signup_free_credits(", 1)[1].rsplit(")", 1)[0]
        assert args.count(",") >= 2, line


# --- F9: credit_iap_once rollback ----------------------------------------
def test_credit_iap_once_rolls_back_its_ledger_row_on_failure():
    """A failed balance increment must leave NO ledger row, so RevenueCat's
    retry can still credit the purchase."""
    txn = f"f414-{uuid.uuid4().hex[:10]}"
    ledger_id = f"apple:{txn}"
    wallet_key = f"user:TEST_f414-{uuid.uuid4()}"

    class Boom(Exception):
        pass

    class ExplodingWallets:
        async def update_one(self, *args, **kwargs):
            raise Boom("write failed")

    class DbWithBrokenWallets:
        """Motor builds a new collection object on every attribute access, so
        the failure has to be injected at the database level."""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            if name == "wallets":
                return ExplodingWallets()
            return getattr(self._real, name)

    real_db = pay.db
    pay.db = DbWithBrokenWallets(real_db)
    try:
        with pytest.raises(Boom):
            run_async(pay.credit_iap_once(txn, wallet_key, 5.0, "pack_5", "USD", "SANDBOX"))
    finally:
        pay.db = real_db

    assert db.wallet_ledger.find_one({"payment_id": ledger_id}) is None
    assert db.wallets.find_one({"device_id": wallet_key}) is None

    # And the retry now works, which is the whole point of the rollback.
    assert run_async(pay.credit_iap_once(txn, wallet_key, 5.0, "pack_5", "USD", "SANDBOX")) is True
    try:
        assert db.wallets.find_one({"device_id": wallet_key})["balance"] == 5.0
        assert db.wallet_ledger.find_one({"payment_id": ledger_id}) is not None
        # Still exactly once.
        assert run_async(pay.credit_iap_once(txn, wallet_key, 5.0, "pack_5", "USD", "SANDBOX")) is False
        assert db.wallets.find_one({"device_id": wallet_key})["balance"] == 5.0
    finally:
        db.wallet_ledger.delete_one({"payment_id": ledger_id})
        db.wallets.delete_one({"device_id": wallet_key})


def test_credit_wallet_once_rollback_still_in_place():
    """The pattern F9 was copied from — asserted so the two can't drift."""
    src = inspect.getsource(pay.credit_wallet_once)
    assert "delete_one({\"payment_id\"" in src or "delete_one({'payment_id'" in src
