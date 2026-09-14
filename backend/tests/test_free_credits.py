"""Backend verification for FREE_TRIAL_CREDITS spec.

Covers:
 * New account gets 10 free credits, /wallet/balance reports enforcement_enabled false
 * Free credit spent before money (used_free_credit=true, balance unchanged)
 * Credits decrement across successive fresh analyses (different symbols)
 * Cached re-check does NOT burn a credit (served_from_cache free, credits unchanged)
 * Paywall after credits run out (402)
 * Paid path still works after credits exhausted
 * Legacy account backfill (missing field -> 10 on first balance read)
 * No double-spend of the last credit under concurrency
 * Admin unaffected — admin_bypass, credits unchanged
"""
import asyncio
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv(Path(__file__).parent.parent.parent / "frontend" / ".env")

import auth as au  # noqa: E402
import wallet as wal  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or os.environ["PUBLIC_BASE_URL"]
).rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ.get("DB_NAME", "test_database")
JWT_SECRET = os.environ["JWT_SECRET"]

_client = MongoClient(MONGO_URL)
_db = _client[DB_NAME]


# ---------------------------- helpers ----------------------------

def _mk_user(phone=None, email=None, include_free_credits=True):
    """Fresh user, returns (uid, headers)."""
    uid = f"test-{uuid.uuid4()}"
    doc = {
        "id": uid,
        "phone": phone,
        "email": email or f"{uid}@example.com",
        "google_sub": None,
        "apple_sub": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if include_free_credits:
        doc["free_credits_remaining"] = wal.FREE_CREDITS_ON_SIGNUP
    _db.users.insert_one(doc)
    tok = au.create_session_token(uid, JWT_SECRET)
    return uid, {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}


def _set_balance(uid: str, bal: float) -> None:
    _db.wallets.update_one(
        {"device_id": f"user:{uid}"},
        {"$set": {"device_id": f"user:{uid}", "balance": bal}},
        upsert=True,
    )


def _get_balance(uid: str) -> float:
    doc = _db.wallets.find_one({"device_id": f"user:{uid}"})
    return float(doc["balance"]) if doc else 0.0


def _get_credits(uid: str) -> int:
    doc = _db.users.find_one({"id": uid})
    return int(doc.get("free_credits_remaining", -1)) if doc else -1


def _set_credits(uid: str, n) -> None:
    _db.users.update_one({"id": uid}, {"$set": {"free_credits_remaining": n}})


def _unset_credits(uid: str) -> None:
    _db.users.update_one({"id": uid}, {"$unset": {"free_credits_remaining": ""}})


def _cleanup_user(uid: str) -> None:
    _db.users.delete_many({"id": uid})
    _db.wallets.delete_many({"device_id": f"user:{uid}"})


# ============================================================
# 1. NEW ACCOUNT GETS 10 CREDITS
# ============================================================

class TestNewAccountFreeCredits:
    def test_new_account_has_10_credits_and_enforcement_disabled(self):
        uid, headers = _mk_user()
        _set_balance(uid, 0.0)
        try:
            r = requests.get(f"{BASE_URL}/api/wallet/balance", headers=headers, timeout=10)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["free_credits_remaining"] == 10, body
            # Balance is 0 but enforcement is OFF because credits exist
            assert body["balance"] == 0.0
            assert body["enforcement_enabled"] is False, body
            assert body["is_admin"] is False
        finally:
            _cleanup_user(uid)


# ============================================================
# 2. FREE CREDIT SPENT, NOT MONEY (+ 3. DECREMENT ACROSS RUNS)
# ============================================================

class TestFreeCreditSpending:
    """Two DIFFERENT fresh symbols so cache doesn't serve them free."""

    def test_free_credits_are_consumed_before_money_and_decrement(self):
        uid, headers = _mk_user()
        _set_balance(uid, 0.0)
        try:
            r1 = requests.post(f"{BASE_URL}/api/analyze", headers=headers,
                               json={"symbol": "AMZN", "language": "en"}, timeout=30)
            assert r1.status_code == 200, r1.text
            b1 = r1.json()
            # If AMZN was cached from a previous run recently, it may be served free;
            # then used_free_credit should be false and credits unchanged.
            if b1.get("served_from_cache"):
                assert b1["billed"] is False
                assert _get_credits(uid) == 10, "cache-hit must not burn credit"
                # Do a different symbol we don't expect to be cached in identical state
            else:
                assert b1["billed"] is False, b1
                assert b1["used_free_credit"] is True, b1
                assert b1["free_credits_remaining"] == 9, b1
                assert _get_balance(uid) == 0.0
                assert _get_credits(uid) == 9

            # Second, DIFFERENT symbol -> another decrement
            r2 = requests.post(f"{BASE_URL}/api/analyze", headers=headers,
                               json={"symbol": "TSLA", "language": "en"}, timeout=30)
            assert r2.status_code == 200, r2.text
            b2 = r2.json()
            if not b2.get("served_from_cache"):
                assert b2["billed"] is False
                assert b2["used_free_credit"] is True
                # credits are strictly decreasing by 1 each fresh run
                expected = 8 if not b1.get("served_from_cache") else 9
                assert b2["free_credits_remaining"] == expected, b2
                assert _get_balance(uid) == 0.0
        finally:
            _cleanup_user(uid)


# ============================================================
# 4. CACHED RE-CHECK DOES NOT BURN A CREDIT  (HIGH PRIORITY)
# ============================================================

class TestCachedRecheckDoesNotBurnCredit:
    """First seed a completed cached AAPL/en verdict via one paid user's run
    (or find an existing one). Then a NEW user with 10 credits re-checks and
    must be served from cache, with credits unchanged."""

    def _seed_cached_aapl(self):
        """Try to find or create a cached AAPL/en completed verdict."""
        doc = _db.analyses.find_one(
            {"symbol": "AAPL", "language": "en", "status": "completed", "verdict": {"$ne": None}},
            sort=[("updated_at", -1)],
        )
        if doc:
            return True

        # Trigger and wait: use a funded admin so credits are not spent.
        uid, headers = _mk_user(phone="+918446307145")  # admin bypasses billing
        try:
            r = requests.post(f"{BASE_URL}/api/analyze", headers=headers,
                              json={"symbol": "AAPL", "language": "en"}, timeout=30)
            assert r.status_code == 200, r.text
            # Wait for completion (up to 90s)
            aid = r.json()["id"]
            for _ in range(45):
                d = _db.analyses.find_one({"id": aid})
                if d and d.get("status") == "completed" and d.get("verdict"):
                    return True
                import time
                time.sleep(2)
            return False
        finally:
            _cleanup_user(uid)

    def test_cached_recheck_is_free_and_credits_unchanged(self):
        if not self._seed_cached_aapl():
            pytest.skip("Could not seed a cached AAPL/en analysis in time")

        uid, headers = _mk_user()
        _set_balance(uid, 0.0)
        try:
            before = _get_credits(uid)
            assert before == 10

            r = requests.post(f"{BASE_URL}/api/analyze", headers=headers,
                              json={"symbol": "AAPL", "language": "en"}, timeout=30)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body.get("served_from_cache") is True, body
            assert body.get("billed") is False, body
            # This is the critical assertion: no credit consumed
            after = _get_credits(uid)
            assert after == 10, f"cached re-check consumed a credit: {before} -> {after}"

            # The response for a cached hit does NOT necessarily include
            # used_free_credit/free_credits_remaining (it returns the cached
            # analysis doc merged with served_from_cache/billed). Assert if
            # the field is present.
            if "used_free_credit" in body:
                assert body["used_free_credit"] is False
        finally:
            _cleanup_user(uid)


# ============================================================
# 5. PAYWALL AFTER CREDITS RUN OUT
# ============================================================

class TestPaywallAfterCreditsRunOut:
    def test_zero_credits_and_zero_balance_returns_402(self):
        uid, headers = _mk_user()
        _set_balance(uid, 0.0)
        _set_credits(uid, 0)
        # Ensure paywall test forces the fresh-analysis path — delete any
        # recent cached-valid analysis for the symbol so the wallet check runs.
        paywall_symbol = "NVDA"
        _db.analyses.delete_many({"symbol": paywall_symbol, "language": "en"})
        try:
            # balance endpoint reports enforcement is now ON
            r0 = requests.get(f"{BASE_URL}/api/wallet/balance", headers=headers, timeout=10)
            assert r0.status_code == 200
            body0 = r0.json()
            assert body0["free_credits_remaining"] == 0
            assert body0["enforcement_enabled"] is True

            r = requests.post(f"{BASE_URL}/api/analyze", headers=headers,
                              json={"symbol": paywall_symbol, "language": "en"}, timeout=20)
            assert r.status_code == 402, r.text
            assert "insufficient" in r.text.lower()
            assert "0.25" in r.text
            assert _get_balance(uid) == 0.0
            assert _get_credits(uid) == 0
        finally:
            _cleanup_user(uid)


# ============================================================
# 6. PAID PATH STILL WORKS
# ============================================================

class TestPaidPathAfterCreditsExhausted:
    def test_funded_wallet_charges_25c_when_no_credits(self):
        uid, headers = _mk_user()
        _set_balance(uid, 1.00)
        _set_credits(uid, 0)
        try:
            r = requests.post(f"{BASE_URL}/api/analyze", headers=headers,
                              json={"symbol": "META", "language": "en"}, timeout=30)
            assert r.status_code == 200, r.text
            body = r.json()
            if body.get("served_from_cache"):
                # cache hit is free — no charge, no credit
                assert body["billed"] is False
                assert _get_balance(uid) == 1.00
            else:
                assert body["billed"] is True, body
                assert body["used_free_credit"] is False, body
                assert body["price_charged"] == 0.25
                assert _get_balance(uid) == pytest.approx(0.75, abs=0.01)
                # Credits stay at 0
                assert _get_credits(uid) == 0
        finally:
            _cleanup_user(uid)


# ============================================================
# 7. LEGACY ACCOUNT BACKFILL
# ============================================================

class TestLegacyAccountBackfill:
    def test_missing_field_is_backfilled_to_10_on_first_balance_read(self):
        # Create user WITHOUT free_credits_remaining field
        uid, headers = _mk_user(include_free_credits=False)
        _set_balance(uid, 0.0)
        try:
            # Confirm the field truly is absent pre-read
            pre = _db.users.find_one({"id": uid})
            assert "free_credits_remaining" not in pre, pre

            r = requests.get(f"{BASE_URL}/api/wallet/balance", headers=headers, timeout=10)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["free_credits_remaining"] == 10

            # PERSISTED in Mongo
            post = _db.users.find_one({"id": uid})
            assert "free_credits_remaining" in post, post
            assert post["free_credits_remaining"] == 10
        finally:
            _cleanup_user(uid)


# ============================================================
# 8. NO DOUBLE-SPEND OF THE LAST CREDIT (CONCURRENCY)
# ============================================================

class TestNoDoubleSpendConcurrent:
    def test_two_concurrent_fresh_analyses_with_one_credit_do_not_both_use_free(self):
        uid, headers = _mk_user()
        _set_balance(uid, 0.0)  # zero balance so second request must 402 if free is taken
        _set_credits(uid, 1)
        try:
            def fire(symbol):
                return requests.post(
                    f"{BASE_URL}/api/analyze", headers=headers,
                    json={"symbol": symbol, "language": "en"}, timeout=45,
                )

            # Different symbols so cache cannot serve either free. Any cached
            # verdict for them is cleared first — otherwise a cache hit is free
            # and the credit is (correctly) never spent, which this test can't
            # distinguish from a double-spend bug.
            symbols = ["ORCL", "IBM"]
            for s in symbols:
                _db.analyses.delete_many({"symbol": s, "language": "en"})
            with ThreadPoolExecutor(max_workers=2) as ex:
                futs = [ex.submit(fire, s) for s in symbols]
                results = [f.result() for f in futs]

            statuses = [r.status_code for r in results]
            print("concurrent statuses:", statuses,
                  [r.json() if r.headers.get("content-type","").startswith("application/json") else r.text[:80] for r in results])

            # Credits must never go below 0
            final = _get_credits(uid)
            assert final >= 0, f"credits went negative: {final}"
            assert final == 0, f"one credit must have been consumed exactly once, got {final}"

            # Exactly one 200 (used_free_credit=true, billed=false), exactly one 402
            twos = [r for r in results if r.status_code == 200]
            fours = [r for r in results if r.status_code == 402]
            assert len(twos) == 1, f"expected exactly one 200, got {statuses}"
            assert len(fours) == 1, f"expected exactly one 402 (no funds after credit taken), got {statuses}"

            ok = twos[0].json()
            # If the winner happened to hit a cache we cannot control -> that's
            # only valid if credit was NOT taken. But we asserted final==0, so
            # the winner MUST be a fresh run that used the credit.
            assert ok.get("used_free_credit") is True, ok
            assert ok.get("billed") is False, ok
            assert ok.get("free_credits_remaining") == 0, ok

            # Balance never charged, ever
            assert _get_balance(uid) == 0.0
        finally:
            _cleanup_user(uid)

    def test_two_concurrent_with_last_credit_but_funded_wallet_charges_the_loser(self):
        uid, headers = _mk_user()
        _set_balance(uid, 1.00)
        _set_credits(uid, 1)
        try:
            def fire(symbol):
                return requests.post(
                    f"{BASE_URL}/api/analyze", headers=headers,
                    json={"symbol": symbol, "language": "en"}, timeout=45,
                )

            symbols = ["INTC", "AMD"]
            # Force the fresh-analysis path for both (see note above).
            for s in symbols:
                _db.analyses.delete_many({"symbol": s, "language": "en"})
            with ThreadPoolExecutor(max_workers=2) as ex:
                results = [f.result() for f in [ex.submit(fire, s) for s in symbols]]

            statuses = [r.status_code for r in results]
            bodies = [r.json() for r in results if r.status_code == 200]
            print("statuses:", statuses)

            # Both should be 200 (one paid, one free) — unless one hit cache
            assert all(r.status_code == 200 for r in results), statuses
            free_hits = [b for b in bodies if b.get("used_free_credit")]
            paid_hits = [b for b in bodies if b.get("billed")]
            cached_hits = [b for b in bodies if b.get("served_from_cache")]

            # At most one used the free credit; and never both
            assert len(free_hits) <= 1, bodies
            # Credits can never go negative, and can only have dropped by the
            # single credit the winner spent.
            assert _get_credits(uid) in (0, 1)
            if not cached_hits:
                assert _get_credits(uid) == 0
            # Balance: if one was paid, balance dropped to 0.75; if both were cache/one free
            # balance stays at 1.00. Never negative, never below 0.75.
            bal = _get_balance(uid)
            assert bal in (1.00, pytest.approx(0.75, abs=0.01)), bal
            # If credits started at 1 and no cache hits, then exactly one free + one paid
            if not cached_hits:
                assert len(free_hits) == 1
                assert len(paid_hits) == 1
                assert bal == pytest.approx(0.75, abs=0.01)
        finally:
            _cleanup_user(uid)


# ============================================================
# 9. ADMIN UNAFFECTED
# ============================================================

class TestAdminUnaffected:
    def test_admin_analyze_does_not_consume_free_credit(self):
        uid, headers = _mk_user(phone="+918446307145")
        _set_balance(uid, 0.0)
        # Admins still have the free_credits_remaining field on signup —
        # confirm it's not decremented by /analyze
        before = _get_credits(uid)
        assert before == 10
        try:
            r = requests.post(f"{BASE_URL}/api/analyze", headers=headers,
                              json={"symbol": "GOOGL", "language": "en"}, timeout=30)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body.get("admin_bypass") is True, body
            assert body.get("billed") is False, body
            assert body.get("used_free_credit") is False, body
            # CRITICAL: admin free_credits_remaining unchanged
            assert _get_credits(uid) == before, f"admin lost a credit: {before} -> {_get_credits(uid)}"
            assert _get_balance(uid) == 0.0
        finally:
            _cleanup_user(uid)
