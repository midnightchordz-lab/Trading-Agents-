"""Explicit consent recording + the /analyze consent gate + account deletion.

India's DPDP Act requires informed, specific, AFFIRMATIVE consent presented
with the data request itself — not implied by continued use — and a withdrawal
path as easy as giving it. Here withdrawal IS account deletion: the app cannot
operate without the baseline data consent covers (phone/email, wallet), so
there is no coherent partial-withdrawal state to represent.

The four properties worth pinning: a fresh account is genuinely blocked with a
SPECIFIC reason (not a generic error the app can't act on), `agreed: false` is
rejected rather than stored as a valid "no", an anonymous request is untouched
by the gate (it has given no personal data), and deletion really does kill the
old session token instead of leaving a dangling access path.
"""
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
from deps import CONSENT_VERSION  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("PUBLIC_BASE_URL")
    or "http://localhost:8001"
).rstrip("/")
JWT_SECRET = os.environ["JWT_SECRET"]
ADMIN_IDENTIFIERS = os.environ.get("ADMIN_IDENTIFIERS", "")

_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ["DB_NAME"]]


def mk_user(consent=None):
    """A signed-in account. `consent` is the stored consent sub-document, or
    None for a brand-new account that has never agreed to anything."""
    uid = f"TEST_consent-{uuid.uuid4()}"
    doc = {
        "id": uid,
        "email": f"{uid}@example.com",
        "free_credits_remaining": 5,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if consent is not None:
        doc["consent"] = consent
    _db.users.insert_one(doc)
    _db.wallets.insert_one({"device_id": f"user:{uid}", "balance": 50.0, "currency": "USD"})
    tok = au.create_session_token(uid, JWT_SECRET)
    return uid, {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}


def cleanup(uid):
    _db.users.delete_many({"id": uid})
    _db.wallets.delete_many({"device_id": f"user:{uid}"})


def me(headers):
    return requests.get(f"{BASE_URL}/api/auth/me", headers=headers, timeout=20)


def analyze(headers=None):
    return requests.post(
        f"{BASE_URL}/api/analyze",
        headers=headers or {"Content-Type": "application/json"},
        json={"symbol": "TESTCONSENT", "device_id": f"dev-{uuid.uuid4()}"},
        timeout=30,
    )


class TestFreshAccountIsBlocked:
    """Acceptance criterion 1."""

    def test_auth_me_reports_no_consent(self):
        uid, hdrs = mk_user()
        try:
            r = me(hdrs)
            assert r.status_code == 200, r.text
            assert r.json()["consent_given"] is False
            assert r.json()["consent_version_required"] == CONSENT_VERSION
        finally:
            cleanup(uid)

    def test_analyze_is_403_with_the_specific_consent_reason(self):
        uid, hdrs = mk_user()
        try:
            r = analyze(hdrs)
            assert r.status_code == 403, r.text
            # A specific, machine-readable reason — the app needs to know it
            # owes the consent screen, not merely that something failed.
            assert r.json()["detail"] == "consent_required"
        finally:
            cleanup(uid)

    def test_consent_against_an_older_version_does_not_count(self):
        """A user who agreed to a materially different earlier notice hasn't
        agreed to this one."""
        uid, hdrs = mk_user({"agreed": True, "agreed_at": "2020-01-01T00:00:00+00:00", "version": "0.9"})
        try:
            assert me(hdrs).json()["consent_given"] is False
            assert analyze(hdrs).status_code == 403
        finally:
            cleanup(uid)

    def test_an_agreed_false_record_does_not_count(self):
        uid, hdrs = mk_user({"agreed": False, "version": CONSENT_VERSION})
        try:
            assert me(hdrs).json()["consent_given"] is False
            assert analyze(hdrs).status_code == 403
        finally:
            cleanup(uid)


class TestRecordingConsent:
    """Acceptance criteria 2 and 3."""

    def test_agreed_false_is_rejected_and_nothing_is_stored(self):
        uid, hdrs = mk_user()
        try:
            r = requests.post(f"{BASE_URL}/api/consent", headers=hdrs, json={"agreed": False}, timeout=20)
            assert r.status_code == 400, r.text
            assert "withdraw" in r.json()["detail"].lower()
            # Specifically NOT stored as a valid negative consent.
            assert "consent" not in (_db.users.find_one({"id": uid}) or {})
            assert analyze(hdrs).status_code == 403
        finally:
            cleanup(uid)

    def test_a_missing_agreed_field_is_a_422_not_a_silent_yes(self):
        uid, hdrs = mk_user()
        try:
            r = requests.post(f"{BASE_URL}/api/consent", headers=hdrs, json={}, timeout=20)
            assert r.status_code == 422, r.text
            assert "consent" not in (_db.users.find_one({"id": uid}) or {})
        finally:
            cleanup(uid)

    def test_agreeing_records_it_and_unblocks_analyze(self):
        uid, hdrs = mk_user()
        try:
            r = requests.post(f"{BASE_URL}/api/consent", headers=hdrs, json={"agreed": True}, timeout=20)
            assert r.status_code == 200, r.text
            assert r.json() == {"consent_given": True, "consent_version": CONSENT_VERSION}

            stored = (_db.users.find_one({"id": uid}) or {})["consent"]
            assert stored["agreed"] is True
            assert stored["version"] == CONSENT_VERSION
            assert stored["agreed_at"], "consent must record WHEN it was given"

            assert me(hdrs).json()["consent_given"] is True
            # No longer gated. (Any non-403 proves the gate is open; the ticker
            # itself is fake so the pipeline result is irrelevant here.)
            assert analyze(hdrs).status_code != 403
        finally:
            cleanup(uid)

    def test_consent_requires_a_session(self):
        r = requests.post(f"{BASE_URL}/api/consent", json={"agreed": True}, timeout=20)
        assert r.status_code == 401

    def test_recording_twice_is_harmless(self):
        uid, hdrs = mk_user()
        try:
            for _ in range(2):
                assert requests.post(
                    f"{BASE_URL}/api/consent", headers=hdrs, json={"agreed": True}, timeout=20
                ).status_code == 200
            assert me(hdrs).json()["consent_given"] is True
        finally:
            cleanup(uid)


class TestAnonymousRequestsAreUnaffected:
    """Acceptance criterion 4 — consent concerns personal data, and an
    anonymous device session has given none."""

    def test_anonymous_analyze_is_never_consent_gated(self):
        r = analyze()
        assert r.status_code != 403, r.text
        if r.status_code == 401:
            # Expected while AUTH_REQUIRED_ENABLED is on: rejected for having
            # no session at all, which is a different gate entirely.
            assert "consent" not in r.text.lower()

    def test_anonymous_market_endpoints_still_work(self):
        # Nothing outside /analyze was touched.
        assert requests.get(f"{BASE_URL}/api/quote/AAPL", timeout=20).status_code == 200
        assert requests.get(f"{BASE_URL}/api/trending", timeout=25).status_code == 200


class TestAdminIsNotGated:
    """Reviewer/owner accounts aren't end-users whose DPDP rights are in play,
    and an App Store reviewer must not be stopped by this screen's backend gate
    if they're using the bypass account."""

    @pytest.mark.skipif(not ADMIN_IDENTIFIERS.strip(), reason="no admin identifier configured")
    def test_admin_account_without_consent_is_not_403(self):
        admin_id = ADMIN_IDENTIFIERS.split(",")[0].strip()
        existing = _db.users.find_one({"phone": admin_id})
        created = False
        if not existing:
            existing = {"id": f"TEST_consent-admin-{uuid.uuid4()}", "phone": admin_id,
                        "created_at": datetime.now(timezone.utc).isoformat()}
            _db.users.insert_one(existing)
            created = True
        tok = au.create_session_token(existing["id"], JWT_SECRET)
        hdrs = {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}
        try:
            assert analyze(hdrs).status_code != 403
        finally:
            if created:
                cleanup(existing["id"])


class TestAccountDeletion:
    """Acceptance criterion 5 — deletion is also the consent-withdrawal path,
    so it has to actually revoke access, not just hide the account."""

    def test_deletes_the_user_and_wallet_records(self):
        uid, hdrs = mk_user({"agreed": True, "agreed_at": "2026-01-01T00:00:00+00:00", "version": CONSENT_VERSION})
        try:
            r = requests.delete(f"{BASE_URL}/api/account", headers=hdrs, timeout=20)
            assert r.status_code == 200, r.text
            assert r.json() == {"deleted": True}
            assert _db.users.find_one({"id": uid}) is None
            assert _db.wallets.find_one({"device_id": f"user:{uid}"}) is None
        finally:
            cleanup(uid)

    def test_the_old_session_token_stops_working(self):
        uid, hdrs = mk_user({"agreed": True, "agreed_at": "2026-01-01T00:00:00+00:00", "version": CONSENT_VERSION})
        try:
            assert requests.delete(f"{BASE_URL}/api/account", headers=hdrs, timeout=20).status_code == 200
            # The token is still cryptographically valid — what matters is that
            # the account it points at is gone, so it grants nothing.
            assert me(hdrs).status_code == 401
            assert requests.get(f"{BASE_URL}/api/history", headers=hdrs, timeout=20).status_code == 401
            assert analyze(hdrs).status_code == 401
        finally:
            cleanup(uid)

    def test_deletion_requires_a_session(self):
        assert requests.delete(f"{BASE_URL}/api/account", timeout=20).status_code == 401


def teardown_module():
    _db.users.delete_many({"id": {"$regex": "^TEST_consent-"}})
    _db.wallets.delete_many({"device_id": {"$regex": "^user:TEST_consent-"}})
    _db.analyses.delete_many({"symbol": "TESTCONSENT"})
