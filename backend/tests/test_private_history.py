"""Private per-account history.

History used to be a shared list: any signed-in account could read, open and
delete every analysis anyone had ever run. It is now scoped to the caller —
their own runs, plus runs where an unchanged cached verdict was served to them
(a free re-check reuses someone else's document, and from the caller's side
that was still their own re-check, so it has to remain visible and openable).

The two properties that matter: you can never see or delete another account's
analysis, and deleting a row that only *appeared* in your history via the
shared cache must not destroy the original owner's record.
"""
import hashlib
import hmac
import os
import sys
import uuid
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
JWT_SECRET = os.environ["JWT_SECRET"]

_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ["DB_NAME"]]


def owner_hash(user_id: str) -> str:
    return hmac.new(JWT_SECRET.encode(), f"analysis-owner:{user_id}".encode(), hashlib.sha256).hexdigest()


def mint_user():
    uid = f"TEST_hist-{uuid.uuid4()}"
    _db.users.insert_one({"id": uid, "email": f"{uid}@example.com",
                          "created_at": datetime.now(timezone.utc).isoformat()})
    tok = au.create_session_token(uid, JWT_SECRET)
    return uid, {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}


def seed_analysis(owner_id=None, viewers=()):
    aid = f"TEST_hist-an-{uuid.uuid4()}"
    _db.analyses.insert_one({
        "id": aid,
        "symbol": "TESTPRIV",
        "name": "TESTPRIV",
        "language": "en",
        "status": "completed",
        "messages": [],
        "verdict": {"decision": "HOLD"},
        "owner_hash": owner_hash(owner_id) if owner_id else None,
        "viewer_hashes": [owner_hash(v) for v in viewers],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return aid


@pytest.fixture(scope="module")
def alice():
    uid, hdrs = mint_user()
    yield uid, hdrs
    _db.users.delete_one({"id": uid})


@pytest.fixture(scope="module")
def bob():
    uid, hdrs = mint_user()
    yield uid, hdrs
    _db.users.delete_one({"id": uid})


class TestHistoryIsScopedToTheCaller:
    def test_own_run_is_listed(self, alice):
        uid, hdrs = alice
        aid = seed_analysis(owner_id=uid)
        r = requests.get(f"{BASE_URL}/api/history", headers=hdrs, timeout=20)
        assert r.status_code == 200, r.text
        assert aid in [row["id"] for row in r.json()["results"]]

    def test_another_accounts_run_is_not_listed(self, alice, bob):
        bob_uid, _ = bob
        _, alice_hdrs = alice
        aid = seed_analysis(owner_id=bob_uid)
        r = requests.get(f"{BASE_URL}/api/history", headers=alice_hdrs, timeout=20)
        assert r.status_code == 200, r.text
        assert aid not in [row["id"] for row in r.json()["results"]]

    def test_a_cache_served_run_is_listed(self, alice, bob):
        """Bob ran it, Alice's unchanged re-check was served the same document."""
        bob_uid, _ = bob
        alice_uid, alice_hdrs = alice
        aid = seed_analysis(owner_id=bob_uid, viewers=(alice_uid,))
        r = requests.get(f"{BASE_URL}/api/history", headers=alice_hdrs, timeout=20)
        assert aid in [row["id"] for row in r.json()["results"]]

    def test_internal_fields_are_never_returned(self, alice):
        uid, hdrs = alice
        seed_analysis(owner_id=uid)
        r = requests.get(f"{BASE_URL}/api/history", headers=hdrs, timeout=20)
        for row in r.json()["results"]:
            assert "owner_hash" not in row
            assert "viewer_hashes" not in row
            assert "messages" not in row


class TestAnalysisDetailIsScopedToTheCaller:
    def test_own_run_opens(self, alice):
        uid, hdrs = alice
        aid = seed_analysis(owner_id=uid)
        r = requests.get(f"{BASE_URL}/api/analysis/{aid}", headers=hdrs, timeout=20)
        assert r.status_code == 200, r.text
        assert "owner_hash" not in r.json()
        assert "viewer_hashes" not in r.json()

    def test_another_accounts_run_is_indistinguishable_from_missing(self, alice, bob):
        bob_uid, _ = bob
        _, alice_hdrs = alice
        aid = seed_analysis(owner_id=bob_uid)
        r = requests.get(f"{BASE_URL}/api/analysis/{aid}", headers=alice_hdrs, timeout=20)
        # 404, not 403: a real id must not be distinguishable from a made-up one.
        assert r.status_code == 404, r.text

    def test_a_cache_served_run_opens(self, alice, bob):
        bob_uid, _ = bob
        alice_uid, alice_hdrs = alice
        aid = seed_analysis(owner_id=bob_uid, viewers=(alice_uid,))
        r = requests.get(f"{BASE_URL}/api/analysis/{aid}", headers=alice_hdrs, timeout=20)
        assert r.status_code == 200, r.text

    def test_still_401_without_a_session(self, alice):
        uid, _ = alice
        aid = seed_analysis(owner_id=uid)
        r = requests.get(f"{BASE_URL}/api/analysis/{aid}", timeout=20)
        assert r.status_code == 401


class TestDelete:
    def test_own_run_is_deleted(self, alice):
        uid, hdrs = alice
        aid = seed_analysis(owner_id=uid)
        r = requests.delete(f"{BASE_URL}/api/analysis/{aid}", headers=hdrs, timeout=20)
        assert r.status_code == 200, r.text
        assert _db.analyses.find_one({"id": aid}) is None

    def test_another_accounts_run_cannot_be_deleted(self, alice, bob):
        bob_uid, _ = bob
        _, alice_hdrs = alice
        aid = seed_analysis(owner_id=bob_uid)
        r = requests.delete(f"{BASE_URL}/api/analysis/{aid}", headers=alice_hdrs, timeout=20)
        assert r.status_code == 404, r.text
        assert _db.analyses.find_one({"id": aid}) is not None, "another account's record was destroyed"

    def test_deleting_a_cache_served_row_only_removes_it_from_my_history(self, alice, bob):
        bob_uid, bob_hdrs = bob
        alice_uid, alice_hdrs = alice
        aid = seed_analysis(owner_id=bob_uid, viewers=(alice_uid,))
        r = requests.delete(f"{BASE_URL}/api/analysis/{aid}", headers=alice_hdrs, timeout=20)
        assert r.status_code == 200, r.text
        # Gone for Alice...
        assert requests.get(f"{BASE_URL}/api/analysis/{aid}", headers=alice_hdrs, timeout=20).status_code == 404
        # ...but the owner still has it, along with the shared re-check cache.
        assert requests.get(f"{BASE_URL}/api/analysis/{aid}", headers=bob_hdrs, timeout=20).status_code == 200


def teardown_module():
    _db.analyses.delete_many({"id": {"$regex": "^TEST_hist-an-"}})
    _db.users.delete_many({"id": {"$regex": "^TEST_hist-"}})
