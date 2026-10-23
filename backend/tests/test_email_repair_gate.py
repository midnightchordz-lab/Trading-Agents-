"""The env-gated email repair that runs inside the container.

WHY IT EXISTS: the production database only accepts connections from the
deployed pod, so the CLI script in scripts/ cannot reach it from anywhere the
owner or an agent can run a shell. Routing it through an admin endpoint is not
an option either — a privacy test deliberately forbids wiring `require_admin`
to any route. So the repair runs as a startup hook that is OFF unless
`EMAIL_REPAIR` is set to `dryrun` or `apply`.

What these tests pin down is the gate: silent on every normal boot, read-only
in dryrun, idempotent in apply, and never able to stop the API from starting.
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


def damaged_account(scratch):
    """An account the old migration wrongly stripped: phone identity, the
    address parked in `billing_email`, and a verified OTP proving the person
    really did sign in with it."""
    email = f"gate-{uuid.uuid4().hex[:8]}@example.com"
    uid = f"gate-{uuid.uuid4()}"
    scratch["users"].append(uid)
    scratch["otps"].append(email)
    db.users.insert_one({"id": uid, "created_at": NOW, "phone": "+919800000021",
                         "billing_email": email, "email": None, "identity_type": "phone"})
    db.otp_requests.insert_one({"id": str(uuid.uuid4()), "identifier": email,
                                "identifier_type": "email", "otp_hash": "x",
                                "attempts": 0, "verified": True, "created_at": NOW})
    return uid, email


def boot(monkeypatch, mode):
    monkeypatch.setattr(server, "EMAIL_REPAIR_MODE", mode)
    run_async(server.run_gated_email_repair())


def user(uid):
    return db.users.find_one({"id": uid})


def test_unset_is_a_no_op(monkeypatch, scratch):
    """Every normal boot. A repair that runs itself is how the original damage
    happened."""
    uid, email = damaged_account(scratch)
    boot(monkeypatch, "")
    after = user(uid)
    assert after.get("email") is None, "the repair ran without being asked for"
    assert after["billing_email"] == email


def test_an_unrecognised_value_is_also_a_no_op(monkeypatch, scratch):
    """Only the two documented words do anything — a typo must not write."""
    uid, email = damaged_account(scratch)
    boot(monkeypatch, "yes")
    assert user(uid).get("email") is None
    assert user(uid)["billing_email"] == email


def test_dryrun_reports_but_changes_nothing(monkeypatch, scratch):
    uid, email = damaged_account(scratch)
    boot(monkeypatch, "dryrun")
    after = user(uid)
    assert after.get("email") is None, "a dry run wrote to the database"
    assert after["billing_email"] == email


def test_apply_restores_the_account(monkeypatch, scratch):
    uid, email = damaged_account(scratch)
    boot(monkeypatch, "apply")
    after = user(uid)
    assert after["email"] == email
    assert after["identity_type"] == "email"
    assert "billing_email" not in after


def test_apply_is_idempotent(monkeypatch, scratch):
    """The variable will be left set for at least one more boot, so a second
    run must find nothing rather than undo the first."""
    uid, email = damaged_account(scratch)
    boot(monkeypatch, "apply")
    boot(monkeypatch, "apply")
    after = user(uid)
    assert after["email"] == email
    assert after["identity_type"] == "email"


def test_a_failure_cannot_stop_the_api_from_starting(monkeypatch):
    async def explode():
        raise RuntimeError("mongo is having a day")

    monkeypatch.setattr(server, "find_email_repair_candidates", explode)
    boot(monkeypatch, "apply")  # must not raise


def test_the_gate_reads_the_environment_and_defaults_to_off():
    """Pinned as source, because the default is the whole safety property and
    the module-level value is fixed at import time."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "server.py")).read()
    assert 'os.environ.get("EMAIL_REPAIR", "")' in source
    assert 'if EMAIL_REPAIR_MODE not in ("dryrun", "apply"):' in source
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    # Quoting is not ours to control — the deployment panel rewrites this file
    # and has already turned `off` into `"off"`.
    value = [ln.split("=", 1)[1].strip().strip('"\'')
             for ln in open(env_path).read().splitlines()
             if ln.startswith("EMAIL_REPAIR=")]
    assert value == ["off"], \
        f"the preview value must stay off, found {value} — the key only lives in .env so the " \
        "deployment Secrets panel exposes it; it is a one-shot, not a setting"
