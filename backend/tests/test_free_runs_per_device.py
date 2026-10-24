"""One set of free runs per phone (free_grants.py), offline.

The reported exploit: same phone, a new email address each day, and every
account got the sign-up credits, two free Trade Reviews and (in a launch
window) the daily launch-free runs. These tests run the real sign-up and
claim code against an in-memory stand-in for Mongo — no server, no database.
"""
import asyncio
import os
import sys

import pytest
from pymongo.errors import DuplicateKeyError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import free_grants as fg  # noqa: E402
import wallet as wal  # noqa: E402
from routes import auth_routes as ar  # noqa: E402
from routes import trade_review as tr_routes  # noqa: E402

PHONE_A = "android:1a2b3c4d5e6f7a8b"
PHONE_B = "android:99aa88bb77cc66dd"
IPHONE = "ios:6F9619FF-8B86-D011-B42D-00C04FC964FF"


def run(coro):
    return asyncio.run(coro)


# --- a small in-memory Mongo ---------------------------------------------------

def _matches(doc, flt):
    for key, cond in flt.items():
        if key == "$or":
            if not any(_matches(doc, c) for c in cond):
                return False
            continue
        val = doc.get(key)
        if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
            for op, arg in cond.items():
                if op == "$gt" and not (val is not None and val > arg):
                    return False
                if op == "$lt" and not (val is not None and val < arg):
                    return False
                if op == "$in" and val not in arg:
                    return False
                if op == "$exists" and (key in doc) != arg:
                    return False
                if op == "$ne" and val == arg:
                    return False
        elif val != cond:
            return False
    return True


class _Res:
    def __init__(self, modified=0, inserted_id=None):
        self.modified_count = modified
        self.inserted_id = inserted_id


class Coll:
    def __init__(self, unique=None):
        self.docs = []
        self.unique = unique  # a field that must be unique when present

    async def find_one(self, flt, projection=None):
        return next((dict(d) for d in self.docs if _matches(d, flt)), None)

    async def count_documents(self, flt):
        return sum(1 for d in self.docs if _matches(d, flt))

    async def insert_one(self, doc):
        if self.unique and doc.get(self.unique) is not None and any(
                d.get(self.unique) == doc[self.unique] for d in self.docs):
            raise DuplicateKeyError("duplicate")
        doc = {**doc, "_id": len(self.docs) + 1}
        self.docs.append(doc)
        return _Res(inserted_id=doc["_id"])

    async def update_one(self, flt, upd, upsert=False):
        for d in self.docs:
            if _matches(d, flt):
                d.update(upd.get("$set", {}))
                for k, v in upd.get("$inc", {}).items():
                    d[k] = d.get(k, 0) + v
                return _Res(modified=1)
        return _Res()

    async def delete_one(self, flt):
        self.docs = [d for d in self.docs if not _matches(d, flt)]


class FakeDb:
    def __init__(self):
        self.users = Coll()
        self.free_credit_grants = Coll(unique="install_hash")
        self.free_credit_tombstones = Coll()
        self.wallets = Coll()


class FakeRequest:
    """A loopback caller (client_ip trusts its declared address) with an
    optional install id header."""

    def __init__(self, install=None, ip="203.0.113.7"):
        self.headers = {"x-forwarded-for": ip}
        if install:
            self.headers["x-install-id"] = install
        self.client = type("C", (), {"host": "127.0.0.1"})()


@pytest.fixture
def db(monkeypatch):
    fake = FakeDb()
    for mod in (ar, fg, tr_routes):
        monkeypatch.setattr(mod, "db", fake)
    monkeypatch.setattr(ar.au, "is_disposable_email", lambda e: False)
    return fake


def sign_up(email, install=None, ip="203.0.113.7"):
    return run(ar.find_or_create_user("email", email, None, FakeRequest(install, ip), email))


# --- the exploit --------------------------------------------------------------

def test_a_second_email_on_the_same_phone_gets_nothing_free(db):
    first = sign_up("one@example.com", PHONE_A)
    second = sign_up("two@example.com", PHONE_A)
    assert first["free_credits_remaining"] == wal.FREE_CREDITS_ON_SIGNUP
    assert first["free_grant_state"] == fg.GRANTED and "trade_review_free_used" not in first
    stored = run(db.users.find_one({"email": "two@example.com"}))
    assert stored["free_credits_remaining"] == 0 and stored["free_grant_state"] == fg.WITHHELD
    # All three allowances follow the one decision.
    assert stored["trade_review_free_used"] == wal.TRADE_REVIEW_FREE_RUNS
    assert stored["launch_free_eligible"] is False and not fg.launch_free_allowed(stored)
    assert second["free_credits_remaining"] == 0


def test_a_different_phone_still_gets_its_own_grant(db):
    sign_up("one@example.com", PHONE_A)
    other = sign_up("two@example.com", PHONE_B, ip="198.51.100.9")
    assert other["free_credits_remaining"] == wal.FREE_CREDITS_ON_SIGNUP


def test_ios_ids_count_the_same_way(db):
    assert sign_up("one@example.com", IPHONE)["free_grant_state"] == fg.GRANTED
    assert sign_up("two@example.com", IPHONE)["free_grant_state"] == fg.WITHHELD


def test_the_older_checks_still_win(db, monkeypatch):
    monkeypatch.setattr(ar.au, "is_disposable_email", lambda e: True)
    user = sign_up("burner@example.com", PHONE_A)
    assert user["free_grant_state"] == fg.WITHHELD and user["free_credits_remaining"] == 0
    # A refused account does not use up the phone's grant.
    assert run(db.free_credit_grants.count_documents({})) == 0


def test_the_raw_install_id_is_never_stored(db):
    sign_up("one@example.com", PHONE_A)
    row = db.free_credit_grants.docs[0]
    assert row["install_hash"] and PHONE_A not in str(row) and "1a2b3c4d5e6f7a8b" not in str(row)


def test_two_sign_ups_on_one_phone_at_the_same_moment_get_one_grant(db):
    # Both passed the "seen before?" check; the unique index refuses the second row.
    req = FakeRequest(PHONE_A)
    run(db.users.insert_one({"id": "u1", "free_credits_remaining": 10}))
    run(db.users.insert_one({"id": "u2", "free_credits_remaining": 10}))
    assert run(fg.record_grant("u1", None, req)) is True
    assert run(fg.record_grant("u2", None, req)) is False
    u2 = run(db.users.find_one({"id": "u2"}))
    assert u2["free_credits_remaining"] == 0 and u2["free_grant_state"] == fg.WITHHELD


# --- old app versions, the web app, bare scripts -------------------------------

def test_no_install_id_means_deferred_not_granted(db):
    user = sign_up("old@example.com", None)
    assert user["free_grant_state"] == fg.DEFERRED and user["free_credits_remaining"] == 0
    assert user["trade_review_free_used"] == wal.TRADE_REVIEW_FREE_RUNS


@pytest.mark.parametrize("bad", ["", "android:", "android:short", "pc:1234567890abcdef",
                                 "android:" + "a" * 65, "android:12345678;drop"])
def test_a_malformed_install_id_counts_as_none(bad):
    assert fg.install_hash_for(FakeRequest(bad or None)) is None


def test_a_deferred_account_claims_its_runs_from_the_updated_app(db):
    sign_up("old@example.com", None)
    user = run(db.users.find_one({"email": "old@example.com"}))
    claimed = run(fg.claim_deferred(user, FakeRequest(PHONE_A)))
    assert claimed["free_grant_state"] == fg.GRANTED
    stored = run(db.users.find_one({"email": "old@example.com"}))
    assert stored["free_credits_remaining"] == wal.FREE_CREDITS_ON_SIGNUP
    assert stored["trade_review_free_used"] == 0 and stored["launch_free_eligible"] is True
    # Claimed once: a second call changes nothing and records no second grant.
    run(fg.claim_deferred(stored, FakeRequest(PHONE_A)))
    assert run(db.free_credit_grants.count_documents({})) == 1


def test_a_deferred_account_on_an_already_used_phone_gets_nothing(db):
    sign_up("first@example.com", PHONE_A)
    sign_up("old@example.com", None)
    user = run(db.users.find_one({"email": "old@example.com"}))
    out = run(fg.claim_deferred(user, FakeRequest(PHONE_A)))
    assert out["free_grant_state"] == fg.WITHHELD
    assert run(db.users.find_one({"email": "old@example.com"}))["free_credits_remaining"] == 0


def test_claiming_still_needs_the_phone_app(db):
    sign_up("old@example.com", None)
    user = run(db.users.find_one({"email": "old@example.com"}))
    assert run(fg.claim_deferred(user, FakeRequest(None)))["free_grant_state"] == fg.DEFERRED


def test_accounts_from_before_this_change_are_untouched(db):
    legacy = {"id": "old", "free_credits_remaining": 4}
    assert run(fg.claim_deferred(legacy, FakeRequest(PHONE_A))) is legacy
    assert fg.launch_free_allowed(legacy)


def test_anonymous_callers_get_no_launch_free_runs():
    assert not fg.launch_free_allowed(None)


# --- the allowances themselves ----------------------------------------------------

def test_a_refused_account_has_no_free_trade_reviews(db):
    sign_up("one@example.com", PHONE_A)
    sign_up("two@example.com", PHONE_A)
    refused = run(db.users.find_one({"email": "two@example.com"}))
    st = run(tr_routes.billing_status(refused))
    assert st["free_left"] == 0


def test_every_route_that_shows_or_spends_free_runs_claims_deferred_ones():
    import inspect
    from routes import analysis, payments
    assert "fg.claim_deferred(user, request)" in inspect.getsource(analysis.analyze)
    assert "fg.claim_deferred(user, request)" in inspect.getsource(payments.wallet_balance)
    assert "fg.claim_deferred(user, request)" in inspect.getsource(tr_routes.trade_review)
    assert "fg.claim_deferred(user, request)" in inspect.getsource(ar.auth_me)
    assert "fg.launch_free_allowed(user)" in inspect.getsource(analysis.analyze)


def test_every_sign_in_route_applies_the_phone_rule():
    import inspect
    src = inspect.getsource(ar)
    assert src.count("await fg.gate_new_account(granted, request)") == 3
    assert src.count("**fg.new_account_fields(granted, free_state)") == 3
