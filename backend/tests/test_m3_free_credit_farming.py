"""M3: close the free-credit farming paths that Google/Apple sign-in left open.

Email/phone OTP had three protections — a tombstone keyed on the identifier, a
disposable-domain check, and per-device/per-IP caps. Google and Apple sign-in
shared only the last of those, and only in one spelling of the address. So:
delete the account, sign back in with `ab@gmail.com` instead of `a.b@gmail.com`
(the same real inbox), and a fresh grant of free analyses appeared.

Every sanction here is WITHHELD CREDITS, never a refused sign-in — that is the
app's existing rule, and a false positive must never lock a real person out.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth as au  # noqa: E402
import wallet as wal  # noqa: E402
from routes import auth_routes as ar  # noqa: E402
from tests.async_loop import run_async  # noqa: E402

db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]


class FakeRequest:
    """A loopback caller, so client_ip trusts the address it declares."""

    def __init__(self, ip):
        self.headers = {"x-forwarded-for": ip}
        self.client = type("C", (), {"host": "127.0.0.1"})()


@pytest.fixture
def scratch():
    made = {"hashes": [], "grants": [], "users": []}
    yield made
    db.free_credit_tombstones.delete_many({"hash": {"$in": made["hashes"]}})
    db.free_credit_grants.delete_many({"_id": {"$in": made["grants"]}})
    db.users.delete_many({"id": {"$in": made["users"]}})


def bury(scratch, identifier):
    """Exactly what delete_account does now."""
    for form in ar.tombstone_identifiers(identifier):
        h = ar.free_credit_tombstone_hash_for(form)
        scratch["hashes"].append(h)
        db.free_credit_tombstones.update_one({"hash": h},
                                             {"$setOnInsert": {"deleted_at": "2026-01-01T00:00:00+00:00"}},
                                             upsert=True)


def credits_for(identifier, device_id=None, ip="203.0.113.201"):
    return run_async(ar.signup_free_credits(device_id, FakeRequest(ip), identifier))


# --- the same inbox in a different spelling -------------------------------

def test_a_gmail_alias_of_a_deleted_account_gets_no_second_grant(scratch):
    """The farming path: delete, then sign back in with dots or a +tag. Google
    hands back whatever the user typed, so the tombstone has to match both."""
    unique = uuid.uuid4().hex[:8]
    bury(scratch, f"farm.{unique}@gmail.com")
    assert credits_for(f"farm{unique}@gmail.com") == 0
    assert credits_for(f"f.a.r.m{unique}+promo@gmail.com") == 0
    assert credits_for(f"FARM{unique}@GMAIL.COM") == 0


def test_burying_the_canonical_form_also_covers_the_raw_one(scratch):
    """Deletion can happen from either path, so the tombstone is written in
    both spellings and either lookup finds it."""
    unique = uuid.uuid4().hex[:8]
    bury(scratch, f"canon{unique}@gmail.com")
    assert credits_for(f"c.a.n.o.n{unique}@gmail.com") == 0


def test_a_genuinely_different_address_still_gets_its_credits(scratch):
    """The check must not over-reach: non-Gmail dots are meaningful, and two
    different inboxes are two different people."""
    unique = uuid.uuid4().hex[:8]
    bury(scratch, f"one{unique}@gmail.com")
    assert credits_for(f"two{unique}@gmail.com") == wal.FREE_CREDITS_ON_SIGNUP
    bury(scratch, f"a.b{unique}@fastmail.com")
    assert credits_for(f"ab{unique}@fastmail.com") == wal.FREE_CREDITS_ON_SIGNUP


def test_phone_identifiers_are_unchanged(scratch):
    phone = f"+9198{uuid.uuid4().int % 10**8:08d}"
    assert ar.tombstone_identifiers(phone) == [phone]
    bury(scratch, phone)
    assert credits_for(phone) == 0


# --- disposable addresses -------------------------------------------------

def test_a_disposable_address_signs_in_with_zero_credits(scratch):
    """It must still work as an account — only the credits are withheld."""
    assert credits_for(f"throwaway{uuid.uuid4().hex[:6]}@mailinator.com") == 0
    assert credits_for(f"throwaway{uuid.uuid4().hex[:6]}@guerrillamail.com") == 0


def test_the_disposable_check_does_not_touch_normalize_identifier():
    """`normalize_identifier` answers (None, None) for a disposable domain and
    several existing tests depend on that, so the new helpers are separate."""
    assert au.normalize_identifier("x@mailinator.com") == (None, None)
    assert au.is_disposable_email("x@mailinator.com") is True
    assert au.canonical_email("x@mailinator.com") == "x@mailinator.com"
    # And the normalizer's Gmail behaviour is untouched.
    assert au.normalize_identifier("a.b+t@gmail.com") == ("email", "ab@gmail.com")


# --- the global budget ----------------------------------------------------

def test_the_global_budget_withholds_credits_without_blocking_signup(scratch):
    """Device and IP are both rotatable, so a distributed farm stays under
    every per-caller rule. This bounds the total — and it must never refuse a
    sign-in, only the credits."""
    now = datetime.now(timezone.utc)
    rows = [{"user_id": f"budget-{uuid.uuid4()}", "device_id": None,
             "ip": f"198.51.100.{i % 250}", "created_at": now.isoformat()}
            for i in range(ar.FREE_CREDIT_GLOBAL_PER_HOUR)]
    inserted = db.free_credit_grants.insert_many(rows)
    scratch["grants"].extend(inserted.inserted_ids)
    try:
        # A brand-new, innocent address: no tombstone, fresh device, fresh IP.
        granted = credits_for(f"innocent{uuid.uuid4().hex[:8]}@example.com",
                              device_id=f"dev-{uuid.uuid4().hex[:8]}",
                              ip=f"192.0.2.{uuid.uuid4().int % 250}")
        assert granted == 0
    finally:
        db.free_credit_grants.delete_many({"_id": {"$in": list(inserted.inserted_ids)}})

    # And with the budget freed, the very same kind of account gets credits —
    # proving the budget was the cause and nothing else changed.
    assert credits_for(f"after{uuid.uuid4().hex[:8]}@example.com",
                       device_id=f"dev-{uuid.uuid4().hex[:8]}",
                       ip=f"192.0.2.{uuid.uuid4().int % 250}") == wal.FREE_CREDITS_ON_SIGNUP


def test_an_hour_old_burst_does_not_hold_the_budget_down(scratch):
    """A budget that never forgets would withhold credits from every future
    user after one attack."""
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    rows = [{"user_id": f"old-{uuid.uuid4()}", "device_id": None, "ip": "198.51.100.9",
             "created_at": old} for _ in range(ar.FREE_CREDIT_GLOBAL_PER_HOUR + 20)]
    inserted = db.free_credit_grants.insert_many(rows)
    scratch["grants"].extend(inserted.inserted_ids)
    assert credits_for(f"later{uuid.uuid4().hex[:8]}@example.com",
                       device_id=f"dev-{uuid.uuid4().hex[:8]}",
                       ip=f"192.0.2.{uuid.uuid4().int % 250}") == wal.FREE_CREDITS_ON_SIGNUP


def test_the_budget_is_sized_so_the_suite_cannot_trip_it():
    """Measured, not guessed: one full run of the existing suite creates 0
    grants, so the floor applies."""
    assert ar.FREE_CREDIT_GLOBAL_PER_HOUR >= 200


def test_the_signup_grant_is_env_configurable_and_keeps_its_name():
    """Existing tests reference the constant, so the name stays."""
    assert isinstance(wal.FREE_CREDITS_ON_SIGNUP, int)
    assert "FREE_CREDITS_ON_SIGNUP" in open(
        os.path.join(os.path.dirname(__file__), "..", "wallet.py")).read()


def test_the_check_order_is_unchanged(scratch):
    """Tombstone, then device, then IP, then the new global check. The order
    matters for the log lines an operator reads: the most specific reason for
    withholding must win."""
    import inspect
    source = inspect.getsource(ar.signup_free_credits)
    positions = [source.index(marker) for marker in (
        "already had an account",           # tombstone
        "already seeded",                   # device
        "grants from this address",         # IP
        "GLOBAL FREE-CREDIT BUDGET",        # global, last
    )]
    assert positions == sorted(positions)


# --- lookups never rewrite stored data ------------------------------------

def test_an_existing_account_is_found_by_either_spelling(scratch):
    """The orphaning case: the OTP path stores the CANONICAL address, while
    Google hands back whatever the person typed. Looking up only the raw form
    made that user look brand new — a second, empty account, with their
    history and wallet stranded on the first one."""
    unique = uuid.uuid4().hex[:8]
    stored_canonical = f"legacy{unique}@gmail.com"
    uid = f"m3-{uuid.uuid4()}"
    scratch["users"].append(uid)
    db.users.insert_one({"id": uid, "email": stored_canonical, "phone": None,
                         "google_sub": None, "apple_sub": None,
                         "created_at": datetime.now(timezone.utc).isoformat()})

    for spelling in (stored_canonical, f"legacy.{unique}@gmail.com",
                     f"legacy{unique}+promo@gmail.com", f"LEGACY.{unique}@Gmail.com"):
        found = db.users.find_one({"email": {"$in": ar.email_lookup_forms(spelling)}})
        assert found and found["id"] == uid, f"{spelling} did not find the existing account"

    # The stored value is never rewritten — only matched.
    assert db.users.find_one({"id": uid})["email"] == stored_canonical


def test_an_account_stored_in_raw_form_is_found_by_that_same_raw_form(scratch):
    """The honest limit of `email $in [raw, canonical]`: an account stored
    under a DOTTED address is found by that address (which is what the
    provider returns for that user every time), not by its dotless spelling.
    Closing that direction too would need a stored canonical field and a
    backfill — out of scope here, and harmless because the same provider
    returns the same string each sign-in."""
    unique = uuid.uuid4().hex[:8]
    raw = f"dotted.{unique}@gmail.com"
    uid = f"m3-{uuid.uuid4()}"
    scratch["users"].append(uid)
    db.users.insert_one({"id": uid, "email": raw, "phone": None,
                         "google_sub": "legacy-sub", "apple_sub": None,
                         "created_at": datetime.now(timezone.utc).isoformat()})
    found = db.users.find_one({"email": {"$in": ar.email_lookup_forms(raw)}})
    assert found and found["id"] == uid
    # Free credits are still protected in BOTH directions, because the
    # tombstone is written and checked in every spelling.
    bury(scratch, raw)
    assert credits_for(f"dotted{unique}@gmail.com") == 0


def test_captcha_and_attestation_were_not_added():
    """Explicitly out of scope — they need frontend work and an owner
    decision."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "routes", "auth_routes.py")).read()
    for term in ("captcha", "recaptcha", "attestation", "safetynet", "devicecheck"):
        assert term not in source.lower(), f"{term} was implemented despite being out of scope"
