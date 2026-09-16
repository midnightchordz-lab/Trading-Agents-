"""Launch-free month: an auto-expiring, date-based billing bypass.

The whole point of the date-based design is that nobody has to remember to
flip a flag a month after launch — so these tests pin both directions: a
configured future date makes a drained, non-admin wallet run free, a past date
resumes billing on its own, and no configuration at all leaves billing exactly
as it was.

The pure-function tests are unit tests. The end-to-end ones restart the
backend with LAUNCH_FREE_UNTIL set, so they're marked slow-ish but they're the
only way to prove the wiring, not just the helper.
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv(Path(__file__).parent.parent.parent / "frontend" / ".env")

import wallet as wal  # noqa: E402
import auth as au  # noqa: E402
from auth_helper import AUTH_HEADERS, USER_ID  # noqa: E402
from pymongo import MongoClient  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or os.environ["PUBLIC_BASE_URL"]
).rstrip("/")
_db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]
ENV_PATH = Path(__file__).parent.parent / ".env"


class TestIsLaunchFreePeriod:
    NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)

    def test_unset_means_no_free_period(self):
        assert wal.is_launch_free_period("", self.NOW) is False
        assert wal.is_launch_free_period(None, self.NOW) is False

    def test_future_date_is_free(self):
        assert wal.is_launch_free_period("2026-10-16", self.NOW) is True

    def test_past_date_is_not_free(self):
        assert wal.is_launch_free_period("2026-09-15", self.NOW) is False

    def test_cutoff_is_exclusive_at_midnight(self):
        # The window ends AT the date, not through it.
        assert wal.is_launch_free_period("2026-09-16", self.NOW) is False
        assert wal.is_launch_free_period(
            "2026-09-16", datetime(2026, 9, 15, 23, 59, tzinfo=timezone.utc)
        ) is True

    def test_garbage_never_makes_things_free(self):
        for value in ("soon", "16-10-2026", "2026-13-45", "true", "0"):
            assert wal.is_launch_free_period(value, self.NOW) is False

    def test_naive_datetimes_are_treated_as_utc(self):
        assert wal.is_launch_free_period("2026-10-16", datetime(2026, 9, 16, 12, 0)) is True
        assert wal.is_launch_free_period("2026-09-01", datetime(2026, 9, 16, 12, 0)) is False

    def test_iso_datetime_cutoff_works_too(self):
        assert wal.is_launch_free_period("2026-09-16T18:00:00+00:00", self.NOW) is True
        assert wal.is_launch_free_period("2026-09-16T06:00:00+00:00", self.NOW) is False


class TestLaunchFreeDailyHelpers:
    TODAY = "2026-09-16"

    def test_a_stored_earlier_day_rolls_over_to_zero(self):
        assert wal.launch_free_daily_state("2026-09-15", 10, self.TODAY) == (self.TODAY, 0)

    def test_todays_count_is_kept(self):
        assert wal.launch_free_daily_state(self.TODAY, 4, self.TODAY) == (self.TODAY, 4)

    def test_missing_or_nonsense_counts_read_as_zero(self):
        for value in (None, "", "seven", -3, {}):
            assert wal.launch_free_daily_state(self.TODAY, value, self.TODAY) == (self.TODAY, 0)

    def test_quota_runs_out_exactly_at_the_cap(self):
        assert wal.has_launch_free_daily_quota(0) is True
        assert wal.has_launch_free_daily_quota(wal.LAUNCH_FREE_DAILY_CAP - 1) is True
        assert wal.has_launch_free_daily_quota(wal.LAUNCH_FREE_DAILY_CAP) is False
        assert wal.has_launch_free_daily_quota(wal.LAUNCH_FREE_DAILY_CAP + 99) is False


def _set_launch_free(value: str | None) -> None:
    """Rewrites LAUNCH_FREE_UNTIL in backend/.env and restarts the backend."""
    lines = [l for l in ENV_PATH.read_text().splitlines() if not l.startswith("LAUNCH_FREE_UNTIL=")]
    if value is not None:
        lines.append(f"LAUNCH_FREE_UNTIL={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    subprocess.run(["sudo", "supervisorctl", "restart", "backend"], check=True, capture_output=True)
    for _ in range(40):
        try:
            if requests.get(f"{BASE_URL}/api/wallet/balance", headers=AUTH_HEADERS, timeout=5).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("backend did not come back up")


def _drain_wallet():
    _db.wallets.update_one({"device_id": f"user:{USER_ID}"}, {"$set": {"balance": 0.0}}, upsert=True)
    _db.users.update_one({"id": USER_ID}, {"$set": {"free_credits_remaining": 0}})


def _wallet_doc() -> dict:
    return _db.wallets.find_one({"device_id": f"user:{USER_ID}"}) or {}


def _set_daily(count: int, day: str | None = None):
    """Seeds the per-day launch-free counter, so the cap can be exercised
    without actually running ten analyses."""
    _db.wallets.update_one(
        {"device_id": f"user:{USER_ID}"},
        {"$set": {
            "launch_free_daily_date": day or datetime.now(timezone.utc).date().isoformat(),
            "launch_free_daily_count": count,
        }},
        upsert=True,
    )


ADMIN_UID = "admin-+918446307145"
ADMIN_HEADERS = {
    "Authorization": f"Bearer {au.create_session_token(ADMIN_UID, os.environ['JWT_SECRET'])}",
    "Content-Type": "application/json",
}


@pytest.fixture(scope="class")
def restore_env():
    original = ENV_PATH.read_text()
    wallet_before = _db.wallets.find_one({"device_id": f"user:{USER_ID}"}) or {}
    user_before = _db.users.find_one({"id": USER_ID}) or {}
    yield
    ENV_PATH.write_text(original)
    # These tests drain the SHARED test account — put it back, or every later
    # test in the suite that runs an analysis gets a 402.
    _db.wallets.update_one(
        {"device_id": f"user:{USER_ID}"},
        {"$set": {"balance": wallet_before.get("balance", 100.0)}},
        upsert=True,
    )
    _db.users.update_one(
        {"id": USER_ID},
        {"$set": {"free_credits_remaining": user_before.get("free_credits_remaining", 10)}},
    )
    subprocess.run(["sudo", "supervisorctl", "restart", "backend"], check=True, capture_output=True)
    for _ in range(40):
        try:
            if requests.get(f"{BASE_URL}/api/wallet/balance", headers=AUTH_HEADERS, timeout=5).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)


@pytest.mark.usefixtures("restore_env")
@pytest.mark.skipif(
    os.environ.get("RUN_LAUNCH_FREE_E2E") != "1",
    reason=(
        "Flips LAUNCH_FREE_UNTIL in backend/.env and restarts the backend, which "
        "would disturb the other xdist workers. Run serially and explicitly:\n"
        "  RUN_LAUNCH_FREE_E2E=1 python -m pytest tests/test_launch_free.py -o addopts='' -q"
    ),
)
class TestLaunchFreeEndToEnd:
    def test_billing_is_untouched_with_no_window_configured(self):
        _set_launch_free(None)
        _drain_wallet()
        body = requests.get(f"{BASE_URL}/api/wallet/balance", headers=AUTH_HEADERS, timeout=15).json()
        assert body["launch_free_active"] is False
        assert body["enforcement_enabled"] is True, body
        # A drained, non-admin wallet is still blocked — exactly as before.
        _db.analyses.delete_many({"symbol": "KO", "language": "en"})
        r = requests.post(f"{BASE_URL}/api/analyze", headers=AUTH_HEADERS,
                          json={"symbol": "KO"}, timeout=60)
        assert r.status_code == 402, r.text

    def test_drained_regular_account_runs_free_during_the_window(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        _set_launch_free(future)
        _drain_wallet()
        body = requests.get(f"{BASE_URL}/api/wallet/balance", headers=AUTH_HEADERS, timeout=15).json()
        assert body["launch_free_active"] is True
        # No balance gate, so the app can't grey out the analyze button.
        assert body["enforcement_enabled"] is False, body

        _db.analyses.delete_many({"symbol": "KO", "language": "en"})
        r = requests.post(f"{BASE_URL}/api/analyze", headers=AUTH_HEADERS,
                          json={"symbol": "KO"}, timeout=90)
        assert r.status_code == 200, r.text
        started = r.json()
        assert started["launch_free_active"] is True
        assert started["billed"] is False
        assert started["price_charged"] is None
        assert started["used_free_credit"] is False
        # Nothing was taken from the wallet or the free credits.
        assert _db.wallets.find_one({"device_id": f"user:{USER_ID}"})["balance"] == 0.0
        assert _db.users.find_one({"id": USER_ID})["free_credits_remaining"] == 0

    def test_daily_counter_increments_and_is_reported(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        _set_launch_free(future)
        _drain_wallet()
        _set_daily(0)
        body = requests.get(f"{BASE_URL}/api/wallet/balance", headers=AUTH_HEADERS, timeout=15).json()
        assert body["launch_free_daily_cap"] == wal.LAUNCH_FREE_DAILY_CAP
        assert body["launch_free_daily_remaining"] == wal.LAUNCH_FREE_DAILY_CAP

        _db.analyses.delete_many({"symbol": "KO", "language": "en"})
        r = requests.post(f"{BASE_URL}/api/analyze", headers=AUTH_HEADERS,
                          json={"symbol": "KO"}, timeout=90)
        assert r.status_code == 200, r.text
        assert r.json()["launch_free_active"] is True
        assert _wallet_doc()["launch_free_daily_count"] == 1
        after = requests.get(f"{BASE_URL}/api/wallet/balance", headers=AUTH_HEADERS, timeout=15).json()
        assert after["launch_free_daily_remaining"] == wal.LAUNCH_FREE_DAILY_CAP - 1

    def test_eleventh_run_of_the_day_falls_through_to_normal_billing(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        _set_launch_free(future)
        _drain_wallet()
        # The day's allowance is already spent.
        _set_daily(wal.LAUNCH_FREE_DAILY_CAP)
        body = requests.get(f"{BASE_URL}/api/wallet/balance", headers=AUTH_HEADERS, timeout=15).json()
        assert body["launch_free_active"] is True
        assert body["launch_free_daily_remaining"] == 0
        # Allowance spent + empty wallet => the balance gate is back on.
        assert body["enforcement_enabled"] is True, body

        _db.analyses.delete_many({"symbol": "KO", "language": "en"})
        r = requests.post(f"{BASE_URL}/api/analyze", headers=AUTH_HEADERS,
                          json={"symbol": "KO"}, timeout=60)
        # Not a hard block — it falls through to billing, which has nothing to
        # charge, so it's the ordinary insufficient-balance 402.
        assert r.status_code == 402, r.text
        assert "insufficient balance" in r.json()["detail"].lower()
        # A blocked attempt must not have consumed allowance it never got.
        assert _wallet_doc()["launch_free_daily_count"] == wal.LAUNCH_FREE_DAILY_CAP

    def test_a_funded_account_can_keep_going_after_the_daily_allowance(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        _set_launch_free(future)
        _drain_wallet()
        _set_daily(wal.LAUNCH_FREE_DAILY_CAP)
        _db.wallets.update_one({"device_id": f"user:{USER_ID}"}, {"$set": {"balance": 5.0}})
        _db.analyses.delete_many({"symbol": "KO", "language": "en"})
        r = requests.post(f"{BASE_URL}/api/analyze", headers=AUTH_HEADERS,
                          json={"symbol": "KO"}, timeout=90)
        assert r.status_code == 200, r.text
        started = r.json()
        assert started["launch_free_active"] is False
        assert started["billed"] is True
        # Paid for, not free.
        assert _db.wallets.find_one({"device_id": f"user:{USER_ID}"})["balance"] < 5.0

    def test_day_rollover_grants_a_fresh_allowance(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        _set_launch_free(future)
        _drain_wallet()
        # Yesterday's fully-spent allowance, simulating a real rollover.
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        _set_daily(wal.LAUNCH_FREE_DAILY_CAP, day=yesterday)
        body = requests.get(f"{BASE_URL}/api/wallet/balance", headers=AUTH_HEADERS, timeout=15).json()
        assert body["launch_free_daily_remaining"] == wal.LAUNCH_FREE_DAILY_CAP, body

        _db.analyses.delete_many({"symbol": "KO", "language": "en"})
        r = requests.post(f"{BASE_URL}/api/analyze", headers=AUTH_HEADERS,
                          json={"symbol": "KO"}, timeout=90)
        assert r.status_code == 200, r.text
        assert r.json()["launch_free_active"] is True
        doc = _wallet_doc()
        assert doc["launch_free_daily_date"] == datetime.now(timezone.utc).date().isoformat()
        assert doc["launch_free_daily_count"] == 1
        after = requests.get(f"{BASE_URL}/api/wallet/balance", headers=AUTH_HEADERS, timeout=15).json()
        assert after["launch_free_daily_remaining"] == wal.LAUNCH_FREE_DAILY_CAP - 1

    def test_admin_stays_unlimited_regardless_of_the_daily_cap(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        _set_launch_free(future)
        admin_key = f"user:{ADMIN_UID}"
        _db.wallets.update_one(
            {"device_id": admin_key},
            {"$set": {"balance": 0.0, "launch_free_daily_date": datetime.now(timezone.utc).date().isoformat(),
                      "launch_free_daily_count": wal.LAUNCH_FREE_DAILY_CAP + 50}},
            upsert=True,
        )
        _db.analyses.delete_many({"symbol": "PEP", "language": "en"})
        r = requests.post(f"{BASE_URL}/api/analyze", headers=ADMIN_HEADERS,
                          json={"symbol": "PEP"}, timeout=90)
        assert r.status_code == 200, r.text
        started = r.json()
        assert started["admin_bypass"] is True
        assert started["billed"] is False
        # The admin bypass is independent of the launch-free allowance.
        assert started["launch_free_active"] is False

    def test_billing_resumes_by_itself_once_the_date_passes(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        _set_launch_free(past)
        _drain_wallet()
        body = requests.get(f"{BASE_URL}/api/wallet/balance", headers=AUTH_HEADERS, timeout=15).json()
        assert body["launch_free_active"] is False
        assert body["enforcement_enabled"] is True, body

        _db.analyses.delete_many({"symbol": "KO", "language": "en"})
        r = requests.post(f"{BASE_URL}/api/analyze", headers=AUTH_HEADERS,
                          json={"symbol": "KO"}, timeout=60)
        assert r.status_code == 402, r.text
