"""M2: the migration must not take a verified email away from its owner.

The original selection — any user with a phone, an email and no Google/Apple id
— also matched a legacy EMAIL-OTP account carrying a stray phone (the pre-fix
payment path could write one). For those the email IS the verified sign-in
identity, so moving it to `billing_email` locked the person out: the next email
sign-in created a new empty account, with their history and wallet left on the
old one.

The distinguishing evidence is `otp_requests`, which is never pruned: a row is
`verified: True` only after that identifier completed a sign-in.
"""
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402
from tests.async_loop import run_async  # noqa: E402

db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]
NOW = datetime.now(timezone.utc).isoformat()


@pytest.fixture
def scratch():
    made = {"users": [], "otps": []}
    yield made
    db.users.delete_many({"id": {"$in": made["users"]}})
    db.otp_requests.delete_many({"identifier": {"$in": made["otps"]}})


def add_user(scratch, **fields):
    uid = f"m2-{uuid.uuid4()}"
    scratch["users"].append(uid)
    db.users.insert_one({"id": uid, "created_at": NOW, **fields})
    return uid


def add_verified_otp(scratch, identifier):
    scratch["otps"].append(identifier)
    db.otp_requests.insert_one({
        "id": str(uuid.uuid4()), "identifier": identifier,
        "identifier_type": "email" if "@" in identifier else "phone",
        "otp_hash": "x", "attempts": 0, "verified": True, "created_at": NOW,
    })


def run_migration(fresh=True):
    """The real startup hook. The marker is cleared first so each test drives a
    genuine first run rather than the no-op."""
    if fresh:
        db.migrations.delete_one({"name": server.MIGRATION_NAME})
    run_async(server.migrate_unverified_billing_email())


def user(uid):
    return db.users.find_one({"id": uid})


def test_an_email_otp_account_with_a_stray_phone_keeps_its_email(scratch):
    """The bug. This person signed in with their email; the phone came from a
    payment form. Their email must survive."""
    email = f"legacy-{uuid.uuid4().hex[:8]}@example.com"
    phone = "+919800000001"
    uid = add_user(scratch, phone=phone, email=email, google_sub=None, apple_sub=None)
    add_verified_otp(scratch, email)

    run_migration()

    after = user(uid)
    assert after["email"] == email, "a verified sign-in email was stripped — the account is orphaned"
    assert after.get("identity_type") != "phone"


def test_a_genuine_phone_signup_with_a_billing_email_is_still_migrated(scratch):
    """The case the migration exists for: verified phone, and the email only
    ever came from a Razorpay receipt form."""
    email = f"receipt-{uuid.uuid4().hex[:8]}@example.com"
    phone = "+919800000002"
    uid = add_user(scratch, phone=phone, email=email, google_sub=None, apple_sub=None)
    add_verified_otp(scratch, phone)

    run_migration()

    after = user(uid)
    assert after.get("email") is None
    assert after["billing_email"] == email
    assert after["identity_type"] == "phone"


def test_a_gmail_alias_counts_as_the_same_verified_inbox(scratch):
    """OTP rows store the CANONICAL address, so a stored `a.b+x@gmail.com`
    must be matched against `ab@gmail.com` too — otherwise the alias looks
    unverified and gets stripped."""
    stored = "test.user+promo@gmail.com"
    uid = add_user(scratch, phone="+919800000003", email=stored, google_sub=None, apple_sub=None)
    add_verified_otp(scratch, "testuser@gmail.com")
    add_verified_otp(scratch, "+919800000003")

    run_migration()

    assert user(uid)["email"] == stored, "a Gmail alias of a verified address was treated as unverified"


def test_an_account_with_no_evidence_either_way_is_skipped(scratch):
    """No verified OTP for either identifier. Unknown means leave it alone."""
    email = f"unknown-{uuid.uuid4().hex[:8]}@example.com"
    uid = add_user(scratch, phone="+919800000004", email=email, google_sub=None, apple_sub=None)

    run_migration()

    after = user(uid)
    assert after["email"] == email
    assert "billing_email" not in after or after.get("billing_email") is None


def test_an_account_already_carrying_an_identity_type_is_never_touched(scratch):
    """Anything that has signed in since the fix already records its identity
    properly, so the migration has no business reading it."""
    email = f"modern-{uuid.uuid4().hex[:8]}@example.com"
    uid = add_user(scratch, phone="+919800000005", email=email, identity_type="email",
                   google_sub=None, apple_sub=None)
    add_verified_otp(scratch, "+919800000005")

    run_migration()

    assert user(uid)["email"] == email


def test_it_runs_once_and_then_no_ops(scratch):
    """A migration that re-runs on every boot is how the original damage would
    have kept happening after a rollback."""
    email = f"once-{uuid.uuid4().hex[:8]}@example.com"
    uid = add_user(scratch, phone="+919800000006", email=email, google_sub=None, apple_sub=None)
    add_verified_otp(scratch, "+919800000006")
    run_migration()
    assert user(uid).get("email") is None
    assert db.migrations.find_one({"name": server.MIGRATION_NAME})

    # A new matching account appearing later must NOT be migrated, because the
    # migration is done.
    email2 = f"after-{uuid.uuid4().hex[:8]}@example.com"
    uid2 = add_user(scratch, phone="+919800000007", email=email2, google_sub=None, apple_sub=None)
    add_verified_otp(scratch, "+919800000007")
    run_migration(fresh=False)
    assert user(uid2)["email"] == email2, "the migration ran a second time"


def test_google_and_apple_accounts_are_untouched(scratch):
    email = f"google-{uuid.uuid4().hex[:8]}@example.com"
    uid = add_user(scratch, phone="+919800000008", email=email,
                   google_sub="google-123", apple_sub=None)
    add_verified_otp(scratch, "+919800000008")
    run_migration()
    assert user(uid)["email"] == email


# --- the repair script ----------------------------------------------------

def repair(apply: bool):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "repair_mod",
        os.path.join(os.path.dirname(__file__), "..", "scripts", "repair_wrongly_migrated_emails.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return run_async(mod.main(apply))


def test_the_repair_dry_run_changes_nothing(scratch):
    email = f"damaged-{uuid.uuid4().hex[:8]}@example.com"
    uid = add_user(scratch, phone="+919800000011", billing_email=email, email=None,
                   identity_type="phone")
    add_verified_otp(scratch, email)

    repair(apply=False)

    after = user(uid)
    assert after.get("email") is None, "a dry run wrote to the database"
    assert after["billing_email"] == email


def test_the_repair_restores_a_wrongly_migrated_account(scratch):
    email = f"damaged-{uuid.uuid4().hex[:8]}@example.com"
    uid = add_user(scratch, phone="+919800000012", billing_email=email, email=None,
                   identity_type="phone")
    add_verified_otp(scratch, email)

    repair(apply=True)

    after = user(uid)
    assert after["email"] == email
    assert after["identity_type"] == "email"
    assert "billing_email" not in after


def test_the_repair_skips_an_address_another_account_now_holds(scratch):
    """The orphaned sign-in already created a second account with that email.
    Restoring it would leave two accounts with one identity, so a human
    decides. Silent merging is not this script's job."""
    email = f"taken-{uuid.uuid4().hex[:8]}@example.com"
    damaged = add_user(scratch, phone="+919800000013", billing_email=email, email=None,
                       identity_type="phone")
    add_user(scratch, email=email, phone=None, identity_type="email")
    add_verified_otp(scratch, email)

    repair(apply=True)

    after = user(damaged)
    assert after.get("email") is None, "the address was restored onto a second account"
    assert after["billing_email"] == email


def test_the_repair_leaves_genuine_billing_emails_alone(scratch):
    """No verified OTP for the address, so it really is just a receipt email
    and the migration was right about it."""
    email = f"receiptonly-{uuid.uuid4().hex[:8]}@example.com"
    uid = add_user(scratch, phone="+919800000014", billing_email=email, email=None,
                   identity_type="phone")

    repair(apply=True)

    after = user(uid)
    assert after.get("email") is None
    assert after["billing_email"] == email


def test_the_repair_is_not_wired_into_startup():
    """A data repair that runs itself on every boot is how the original damage
    happened."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "server.py")).read()
    assert "repair_wrongly_migrated_emails" not in source
