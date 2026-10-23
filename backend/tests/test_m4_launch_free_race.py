"""M4: the launch-free daily cap could be exceeded by tapping twice.

The old code read the wallet, computed today's count, decided, then wrote the
new count back with `$set`. Two requests arriving together both read the same
count, both decided they were under the cap, and both got a free analysis —
each one a real multi-agent LLM run, paid for by us. With 50 in parallel the
"10 per day" promotion could hand out 50.

The fix is three atomic database steps, so the database decides. These tests
drive the same code path the endpoint does and assert on the wallet document,
because that counter IS the promotion's budget.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wallet as wal  # noqa: E402
from core import db as motor_db  # noqa: E402
from core import now_iso  # noqa: E402
from tests.async_loop import run_async  # noqa: E402

db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]
TODAY = datetime.now(timezone.utc).date().isoformat()


async def claim_one(key: str, today: str = TODAY) -> bool:
    """The exact three steps the endpoint performs, in the same order.

    Kept as a copy rather than calling /analyze, because /analyze then runs a
    real LLM pipeline — fifty of those would cost real money and take minutes,
    and the thing under test is the claim, not the pipeline.
    """
    from pymongo.errors import DuplicateKeyError
    try:
        await motor_db.wallets.update_one(
            {"device_id": key},
            {"$setOnInsert": {"device_id": key, "created_at": now_iso()}},
            upsert=True,
        )
    except DuplicateKeyError:
        pass
    claim = await motor_db.wallets.update_one(
        {"device_id": key,
         "$or": [{"launch_free_daily_date": {"$ne": today}},
                 {"launch_free_daily_count": {"$lt": wal.LAUNCH_FREE_DAILY_CAP}}]},
        [{"$set": {
            "launch_free_daily_count": {
                "$cond": [
                    {"$eq": [{"$ifNull": ["$launch_free_daily_date", ""]}, today]},
                    {"$add": [{"$ifNull": ["$launch_free_daily_count", 0]}, 1]},
                    1,
                ]
            },
            "launch_free_daily_date": today,
            "updated_at": now_iso(),
        }}],
    )
    return claim.modified_count == 1


@pytest.fixture
def key():
    """A device WITH a wallet document, which is the state every real request
    is in: the app calls /api/wallet/balance on launch, and that creates it.
    See test_a_brand_new_device_can_still_duplicate_its_wallet for why that
    distinction matters."""
    k = f"device:m4-{uuid.uuid4()}"
    db.wallets.insert_one({"device_id": k, "created_at": datetime.now(timezone.utc).isoformat()})
    yield k
    db.wallets.delete_many({"device_id": k})


def wallet(key):
    return db.wallets.find_one({"device_id": key}) or {}


def test_fifty_concurrent_requests_grant_exactly_the_cap(key):
    """The race, reproduced. Before the fix this handed out one free analysis
    per request."""
    results = run_async(asyncio.gather(*[claim_one(key) for _ in range(50)]))
    assert sum(1 for ok in results if ok) == wal.LAUNCH_FREE_DAILY_CAP
    assert wallet(key)["launch_free_daily_count"] == wal.LAUNCH_FREE_DAILY_CAP


def test_sequential_claims_stop_at_the_cap(key):
    granted = [run_async(claim_one(key)) for _ in range(wal.LAUNCH_FREE_DAILY_CAP + 5)]
    assert granted == [True] * wal.LAUNCH_FREE_DAILY_CAP + [False] * 5
    assert wallet(key)["launch_free_daily_count"] == wal.LAUNCH_FREE_DAILY_CAP


def test_the_day_rolls_over_once_and_only_once(key):
    """Yesterday's exhausted counter must reset — but if N requests arrive at
    midnight, the reset must happen once, not once per request (which would
    hand out a fresh allowance each time)."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    db.wallets.update_one({"device_id": key},
                          {"$set": {"launch_free_daily_date": yesterday,
                                    "launch_free_daily_count": wal.LAUNCH_FREE_DAILY_CAP}})

    results = run_async(asyncio.gather(*[claim_one(key) for _ in range(20)]))
    assert sum(1 for ok in results if ok) == wal.LAUNCH_FREE_DAILY_CAP
    doc = wallet(key)
    assert doc["launch_free_daily_date"] == TODAY
    assert doc["launch_free_daily_count"] == wal.LAUNCH_FREE_DAILY_CAP


def test_a_refused_claim_does_not_move_the_counter(key):
    """Falling through to normal billing must not also charge the promotion —
    the old `$set` wrote the count back on every request, claimed or not."""
    for _ in range(wal.LAUNCH_FREE_DAILY_CAP):
        run_async(claim_one(key))
    before = wallet(key)["launch_free_daily_count"]
    assert run_async(claim_one(key)) is False
    assert wallet(key)["launch_free_daily_count"] == before


def test_an_existing_wallet_keeps_its_balance_and_currency(key):
    """Step 1 upserts the wallet. `$setOnInsert` only, so a real wallet with
    money in it must come out untouched."""
    db.wallets.update_one({"device_id": key},
                          {"$set": {"balance": 12.5, "currency": "INR",
                                    "currency_locked": True, "free_credits": 3}})
    assert run_async(claim_one(key)) is True
    doc = wallet(key)
    assert doc["balance"] == 12.5
    assert doc["currency"] == "INR"
    assert doc["currency_locked"] is True
    assert doc["free_credits"] == 3


def test_a_brand_new_device_can_still_duplicate_its_wallet():
    """An honest limit, measured and left in place deliberately.

    `wallets.device_id` has NO unique index (adding one was explicitly
    excluded: it would turn a rare silent race in the device-wallet merge into
    a visible error). So when a device with no wallet yet fires many requests
    at the same instant, the concurrent upserts can occasionally create TWO
    wallet documents, and each carries its own daily counter — measured at 1
    or 2 documents out of 50 concurrent upserts.

    This is pre-existing and unchanged by the atomicity fix: the old code
    upserted the same way. It needs a unique index plus a merge, which is
    separate work. What this fix does close is the case that actually happens
    — a device whose wallet already exists (every real request, since the app
    creates it on launch) — where the cap is now exact.
    """
    key = f"device:m4-{uuid.uuid4()}"
    try:
        run_async(asyncio.gather(*[claim_one(key) for _ in range(50)]))
        docs = list(db.wallets.find({"device_id": key}))
        counts = [d.get("launch_free_daily_count", 0) for d in docs]
        # Per wallet document the cap still holds exactly — the leak is the
        # extra document, not the counter.
        assert all(c <= wal.LAUNCH_FREE_DAILY_CAP for c in counts), counts
        assert len(docs) <= 2, f"{len(docs)} wallet documents for one device"
    finally:
        db.wallets.delete_many({"device_id": key})


def test_two_devices_have_separate_allowances(key):
    other = f"device:m4-{uuid.uuid4()}"
    try:
        for _ in range(wal.LAUNCH_FREE_DAILY_CAP):
            run_async(claim_one(key))
        assert run_async(claim_one(key)) is False
        assert run_async(claim_one(other)) is True
    finally:
        db.wallets.delete_one({"device_id": other})


def test_the_read_only_helpers_are_unchanged():
    """The spec required these to stay identical — other code and tests read
    them for the response fields."""
    assert wal.has_launch_free_daily_quota(0) is True
    assert wal.has_launch_free_daily_quota(wal.LAUNCH_FREE_DAILY_CAP) is False
    assert wal.launch_free_daily_state("2020-01-01", 7, TODAY) == (TODAY, 0)
    assert wal.launch_free_daily_state(TODAY, 7, TODAY) == (TODAY, 7)
    # The read-only remaining-allowance helper lives in deps and reads the
    # same two fields; /api/wallet/balance renders from it.
    from deps import launch_free_daily_remaining
    assert callable(launch_free_daily_remaining)


def test_with_the_promotion_off_the_block_is_never_entered():
    """`LAUNCH_FREE_UNTIL` unset must behave byte-identically to before, which
    is the whole reason the change sits inside the existing `if`."""
    import inspect

    from routes import analysis
    source = inspect.getsource(analysis.analyze)
    guard = source.index("if launch_free_now:")
    claim = source.index("launch_free_daily_count")
    assert guard < claim, "the claim moved outside the promotion guard"
    assert wal.is_launch_free_period("", datetime.now(timezone.utc)) is False
    assert wal.is_launch_free_period("2020-01-01", datetime.now(timezone.utc)) is False


def test_no_unique_index_was_added_to_wallets():
    """Explicitly excluded by the spec: it would turn a rare silent race in the
    device-wallet merge into a visible error."""
    for name, info in db.wallets.index_information().items():
        if [k for k, _ in info["key"]] == ["device_id"]:
            assert not info.get("unique"), "a unique index on wallets.device_id was added"


def test_the_endpoint_still_claims_the_way_this_test_does():
    """`claim_one` above is a copy of the endpoint's claim (calling /analyze
    would run a real LLM pipeline 50 times). A copy can drift, so the clauses
    that make it correct are checked against the real source: a
    pipeline-style update, the cap in the FILTER, and no read-then-write."""
    import inspect

    from routes import analysis
    source = inspect.getsource(analysis.analyze)
    block = source[source.index("if launch_free_now:"):source.index("admin_bypass =")]
    assert '"$or": [{"launch_free_daily_date": {"$ne": today_str}}' in block
    assert '"launch_free_daily_count": {"$lt": wal.LAUNCH_FREE_DAILY_CAP}' in block
    assert '"$cond"' in block and '"$ifNull"' in block
    assert "claim.modified_count == 1" in block
    # The old read-compute-write must not come back.
    assert "find_one({\"device_id\": launch_key})" not in block
    assert "reset_count + 1" not in block
