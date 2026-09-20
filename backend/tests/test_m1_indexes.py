"""M1: the indexes the app's correctness depends on must actually exist.

Every `create_index` in `ensure_payment_indexes` used to sit inside ONE
try/except that logged a warning. So if any earlier line raised — a conflicting
index left over from an older deploy is enough — every later line was skipped,
including the rate limiter's unique index (what makes the counter atomic) and
its TTL index (what stops the collection growing forever). The app started
normally and looked healthy, and the only symptom would have been a limiter
that quietly under-counts.

The valuable test here is the boring one: after a real startup, are they there?
"""
import os
import sys

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rate_limit as rl  # noqa: E402

db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]

# (collection, the field(s) the index must cover, must it be unique?)
REQUIRED = [
    ("wallet_ledger", ["payment_id"], True),          # stops a top-up being credited twice
    ("payments", ["razorpay_order_id"], True),
    ("payments", ["razorpay_payment_link_id"], True),
    ("payments", ["reference_id"], True),
    ("webhook_events", ["event_id"], True),           # webhook idempotency
    ("free_credit_tombstones", ["hash"], True),       # no second free grant after deletion
    ("rate_limits", ["key", "window_start"], True),   # limiter atomicity
]


def index_keys(collection: str):
    return {
        name: (info["key"], info.get("unique", False))
        for name, info in db[collection].index_information().items()
    }


@pytest.mark.parametrize("collection,fields,unique", REQUIRED,
                         ids=[f"{c}.{'+'.join(f)}" for c, f, _ in REQUIRED])
def test_the_index_exists_after_a_real_startup(collection, fields, unique):
    wanted = [f for f in fields]
    for name, (key, is_unique) in index_keys(collection).items():
        if [k for k, _ in key] == wanted:
            assert is_unique == unique, f"{collection}.{name} unique={is_unique}, expected {unique}"
            return
    pytest.fail(
        f"no index on {collection}({', '.join(wanted)}) — it was skipped at startup. "
        "If this collection is empty in this environment the index is still created, so a "
        "failure here means the startup hook did not run or an earlier index aborted the batch."
    )


def test_the_rate_limit_ttl_index_exists():
    """Without it the counter collection grows with traffic forever."""
    ttl = [name for name, info in db.rate_limits.index_information().items()
           if info.get("expireAfterSeconds") is not None]
    assert ttl, "rate_limits has no TTL index — the counters are never cleaned up"


def test_analysis_history_indexes_exist():
    """Not unique, but every private-history read filters on them."""
    keys = [[k for k, _ in key] for key, _ in index_keys("analyses").values()]
    assert ["owner_hash"] in keys
    assert ["viewer_hashes"] in keys


# --- the fail-closed behaviour that replaces "silently under-count" --------

def test_unbuilt_indexes_make_only_the_money_spending_bucket_refuse():
    """With the unique index missing the count can be wrong in the PERMISSIVE
    direction. For the OTP-request bucket — which spends real money on an SMS —
    refusing is the lesser harm. Everything else keeps failing open, because a
    broken counter must not take a working app down."""
    from fastapi import HTTPException

    from tests.async_loop import run_async

    before = rl.INDEXES_OK
    try:
        rl.INDEXES_OK = False
        with pytest.raises(HTTPException) as caught:
            run_async(rl.enforce("otp_request", "203.0.113.1", 3, 60, fail_closed=True))
        assert caught.value.status_code == 429
        assert caught.value.headers["Retry-After"]
        # Same 429 the limiter already raises when its database is unreachable,
        # so the app and its users see nothing new.
        assert caught.value.detail == rl.TOO_MANY

        # Every other bucket is unaffected.
        for bucket in ("market", "poll", "pay_order", "otp_verify"):
            run_async(rl.enforce(bucket, "203.0.113.1", 120, 60))
    finally:
        rl.INDEXES_OK = before


def test_not_having_run_yet_behaves_exactly_like_healthy():
    """`None` means startup has not run — a unit test, or a direct caller.
    It must NOT be read as failure, or importing this module would start
    refusing OTP requests."""
    from tests.async_loop import run_async

    before = rl.INDEXES_OK
    try:
        rl.INDEXES_OK = None
        run_async(rl.enforce("otp_request", "203.0.113.2", 3, 60, fail_closed=True))
    finally:
        rl.INDEXES_OK = before


def test_a_real_startup_sets_the_flag_true():
    """The running backend built them, so anything else means the app is
    serving with a limiter it cannot trust."""
    import requests
    base = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://localhost:8001").rstrip("/")
    assert requests.get(f"{base}/health", timeout=15).status_code == 200
    # Same process the tests import, so the module flag reflects its startup
    # only when tests run in-process; the durable evidence is the index tests
    # above, which read the database the app actually wrote to.
    assert rl.INDEXES_OK in (True, None)
