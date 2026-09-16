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
