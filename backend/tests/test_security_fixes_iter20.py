"""Security audit (iteration 20) fixes.

SEC-001 — Apple's sandbox completes purchases for free, and a sandbox event is
indistinguishable from a paid one apart from `environment`. Crediting them
without limit is a free top-up button for anyone holding a sandbox tester
account. They can't simply be refused (App Store reviewers purchase in sandbox
and reject apps that don't deliver the content), so they credit a few times per
account and then stop, and every one is recorded with its environment.

SEC-002 — the wallet debit on /analyze was a read-then-write, so N requests
arriving together could each read the same balance, each pass the affordability
check, and each launch a paid LLM run off a single charge.
"""
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

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(BACKEND_DIR.parent / "frontend" / ".env")

import auth as au  # noqa: E402
import iap  # noqa: E402
import wallet as wal  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("PUBLIC_BASE_URL")
    or "http://localhost:8001"
).rstrip("/")
JWT_SECRET = os.environ["JWT_SECRET"]
IAP_SECRET = os.environ.get("REVENUECAT_WEBHOOK_AUTH", "")
WEBHOOK = f"{BASE_URL}/api/pay/iap/webhook"

_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ["DB_NAME"]]

needs_iap_secret = pytest.mark.skipif(not IAP_SECRET, reason="REVENUECAT_WEBHOOK_AUTH not configured")

# Accounts minted by THIS pytest process. Under xdist the module is split
# across workers, so a blanket ^TEST_sec20- delete in teardown would wipe
# another worker's account mid-test.
_CREATED = []


def mk_user(balance=0.0, currency="USD", free_credits=0):
    uid = f"TEST_sec20-{uuid.uuid4()}"
    _CREATED.append(uid)
    _db.users.insert_one({
        "id": uid,
        "email": f"{uid}@example.com",
        "free_credits_remaining": free_credits,
        "consent": {"agreed": True, "agreed_at": "2026-01-01T00:00:00+00:00", "version": "1.0"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _db.wallets.insert_one({"device_id": f"user:{uid}", "balance": balance, "currency": currency})
    tok = au.create_session_token(uid, JWT_SECRET)
    return uid, {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}


def cleanup(uid):
    key = f"user:{uid}"
    _db.users.delete_many({"id": uid})
    _db.wallets.delete_many({"device_id": key})
    _db.wallet_ledger.delete_many({"wallet_key": key})


def balance_of(uid):
    return float((_db.wallets.find_one({"device_id": f"user:{uid}"}) or {}).get("balance") or 0.0)


def iap_event(wallet_key, product_id="credits_5", environment="PRODUCTION", include_env=True):
    event = {
        "id": f"TEST_sec20-evt-{uuid.uuid4()}",
        "type": "NON_RENEWING_PURCHASE",
        "store": "APP_STORE",
        "product_id": product_id,
        "app_user_id": wallet_key,
        "transaction_id": f"TEST_sec20-txn-{uuid.uuid4()}",
    }
    if include_env:
        event["environment"] = environment
    return {"api_version": "1.0", "event": event}


def post_event(payload):
    return requests.post(WEBHOOK, json=payload, headers={"Authorization": IAP_SECRET}, timeout=25)


@needs_iap_secret
class TestSEC001SandboxPurchasesAreCapped:
    def test_a_sandbox_purchase_still_credits_so_app_review_passes(self):
        """Apple reviewers buy in sandbox and reject apps that take a purchase
        without delivering — so the first sandbox purchases must work."""
        uid, _ = mk_user()
        try:
            r = post_event(iap_event(f"user:{uid}", environment="SANDBOX"))
            assert r.status_code == 200, r.text
            assert r.json()["credited"] is True
            assert balance_of(uid) == 5.0
        finally:
            cleanup(uid)

    def test_sandbox_credits_stop_at_the_limit(self):
        uid, _ = mk_user()
        key = f"user:{uid}"
        try:
            for i in range(iap.SANDBOX_CREDIT_LIMIT):
                r = post_event(iap_event(key, environment="SANDBOX"))
                assert r.status_code == 200, r.text
                assert r.json()["credited"] is True, f"sandbox purchase {i + 1} should still credit"
            at_limit = balance_of(uid)
            assert at_limit == 5.0 * iap.SANDBOX_CREDIT_LIMIT

            # One past the cap: answered 200 (a 4xx would make RevenueCat retry
            # it forever) but nothing is credited.
            r = post_event(iap_event(key, environment="SANDBOX"))
            assert r.status_code == 200, r.text
            assert "ignored" in r.json()
            assert balance_of(uid) == at_limit, "sandbox credit granted past the cap"
        finally:
            cleanup(uid)

    def test_an_unlabelled_environment_is_treated_as_sandbox(self):
        """A purchase with no environment field must fail toward the capped
        path, not the unlimited one."""
        uid, _ = mk_user()
        key = f"user:{uid}"
        try:
            r = post_event(iap_event(key, include_env=False))
            assert r.status_code == 200, r.text
            row = _db.wallet_ledger.find_one({"wallet_key": key, "source": "apple_iap"})
            assert row["environment"] == "UNKNOWN"
            assert row["environment"] != iap.PRODUCTION_ENVIRONMENT
        finally:
            cleanup(uid)

    def test_production_purchases_are_never_capped(self):
        uid, _ = mk_user()
        key = f"user:{uid}"
        try:
            for _ in range(iap.SANDBOX_CREDIT_LIMIT + 3):
                r = post_event(iap_event(key, environment="PRODUCTION"))
                assert r.status_code == 200, r.text
                assert r.json()["credited"] is True
            assert balance_of(uid) == 5.0 * (iap.SANDBOX_CREDIT_LIMIT + 3)
        finally:
            cleanup(uid)

    def test_production_credits_do_not_consume_the_sandbox_allowance(self):
        """A paying customer must not lose their sandbox headroom, and a
        sandbox abuser must not gain any by paying once."""
        uid, _ = mk_user()
        key = f"user:{uid}"
        try:
            for _ in range(6):
                assert post_event(iap_event(key, environment="PRODUCTION")).status_code == 200
            r = post_event(iap_event(key, environment="SANDBOX"))
            assert r.json()["credited"] is True
        finally:
            cleanup(uid)

    def test_the_ledger_records_the_environment_of_every_credit(self):
        uid, _ = mk_user()
        key = f"user:{uid}"
        try:
            post_event(iap_event(key, environment="SANDBOX"))
            post_event(iap_event(key, environment="PRODUCTION"))
            envs = sorted(row["environment"] for row in _db.wallet_ledger.find({"wallet_key": key}))
            # Sandbox-funded balance stays auditable and reversible.
            assert envs == ["PRODUCTION", "SANDBOX"]
        finally:
            cleanup(uid)


class TestSEC002WalletDebitIsAtomic:
    """One balance must fund exactly the number of runs it can pay for, no
    matter how many requests arrive at once."""

    def fire(self, headers, n):
        def run(i):
            return requests.post(
                f"{BASE_URL}/api/analyze",
                headers=headers,
                json={"symbol": f"TESTRACE{i}"},
                timeout=60,
            )
        with ThreadPoolExecutor(max_workers=n) as pool:
            return list(pool.map(run, range(n)))

    def test_one_runs_worth_of_balance_funds_exactly_one_run(self):
        price = wal.PRICES["full_analysis"]
        uid, hdrs = mk_user(balance=price)
        try:
            results = self.fire(hdrs, 4)
            codes = [r.status_code for r in results]
            assert codes.count(200) == 1, f"balance funded {codes.count(200)} runs, expected 1: {codes}"
            assert codes.count(402) == 3, codes
            assert balance_of(uid) == pytest.approx(0.0, abs=1e-6)
        finally:
            cleanup(uid)
            _db.analyses.delete_many({"symbol": {"$regex": "^TESTRACE"}})

    def test_two_runs_worth_of_balance_funds_exactly_two(self):
        price = wal.PRICES["full_analysis"]
        uid, hdrs = mk_user(balance=price * 2)
        try:
            codes = [r.status_code for r in self.fire(hdrs, 5)]
            assert codes.count(200) == 2, f"expected exactly 2 paid runs, got {codes}"
            assert balance_of(uid) == pytest.approx(0.0, abs=1e-6)
        finally:
            cleanup(uid)
            _db.analyses.delete_many({"symbol": {"$regex": "^TESTRACE"}})

    def test_a_balance_can_never_be_driven_negative(self):
        price = wal.PRICES["full_analysis"]
        uid, hdrs = mk_user(balance=price)
        try:
            self.fire(hdrs, 6)
            assert balance_of(uid) >= 0.0
        finally:
            cleanup(uid)
            _db.analyses.delete_many({"symbol": {"$regex": "^TESTRACE"}})

    def test_the_402_still_reports_the_right_currency(self):
        uid, hdrs = mk_user(balance=0.0, currency="INR")
        try:
            r = requests.post(f"{BASE_URL}/api/analyze", headers=hdrs,
                              json={"symbol": "TESTRACEX"}, timeout=30)
            assert r.status_code == 402, r.text
            detail = r.json()["detail"]
            assert "\u20b920.00" in detail, detail
            assert "\u20b90.00" in detail, detail
        finally:
            cleanup(uid)
            _db.analyses.delete_many({"symbol": "TESTRACEX"})


def teardown_module():
    for uid in _CREATED:
        cleanup(uid)
    _db.wallet_ledger.delete_many({"payment_id": {"$regex": "^apple:TEST_sec20-txn-"}})
    _db.webhook_events.delete_many({"event_id": {"$regex": "^rc:TEST_sec20-evt-"}})
    _db.analyses.delete_many({"symbol": {"$regex": "^TESTRACE"}})
