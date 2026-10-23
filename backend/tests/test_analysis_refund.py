"""A paid analysis that fails gives back exactly what it took — wallet charge,
free signup credit or launch-free slot — once, and never for a run that
completed. Offline, with the database replaced."""
import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from routes import analysis as an  # noqa: E402


class Coll:
    def __init__(self, key):
        self.key, self.docs = key, {}

    @staticmethod
    def _ok(doc, flt):
        for k, v in flt.items():
            have = doc.get(k)
            if isinstance(v, dict):
                for op, arg in v.items():
                    if op == "$ne" and have == arg:
                        return False
                    if op == "$gt" and not (have is not None and have > arg):
                        return False
            elif have != v:
                return False
        return True

    async def update_one(self, flt, upd):
        doc = self.docs.get(flt.get(self.key))
        if not doc or not self._ok(doc, flt):
            return SimpleNamespace(modified_count=0)
        for k, v in upd.get("$inc", {}).items():
            doc[k] = doc.get(k, 0) + v
        doc.update(upd.get("$set", {}))
        return SimpleNamespace(modified_count=1)


@pytest.fixture
def db(monkeypatch):
    d = SimpleNamespace(analyses=Coll("id"), wallets=Coll("device_id"), users=Coll("id"))
    d.analyses.docs["a1"] = {"id": "a1", "status": "error"}
    d.wallets.docs["user:u1"] = {"device_id": "user:u1", "balance": 80.0,
                                 "launch_free_daily_date": "2026-10-23", "launch_free_daily_count": 3}
    d.users.docs["u1"] = {"id": "u1", "free_credits_remaining": 4}
    monkeypatch.setattr(an, "db", d)
    return d


WALLET = {"kind": "wallet", "wallet_key": "user:u1", "amount": 20.0, "currency": "INR"}


def test_wallet_charge_is_returned_once(db):
    assert asyncio.run(an.refund_if_failed("a1", WALLET)) is True
    assert db.wallets.docs["user:u1"]["balance"] == 100.0
    assert db.analyses.docs["a1"]["refund"] == {"kind": "wallet", "amount": 20.0, "currency": "INR"}
    assert asyncio.run(an.refund_if_failed("a1", WALLET)) is False        # a second call pays nothing
    assert db.wallets.docs["user:u1"]["balance"] == 100.0


def test_completed_or_running_runs_are_not_refunded(db):
    for status in ("completed", "running"):
        db.analyses.docs["a1"]["status"] = status
        assert asyncio.run(an.refund_if_failed("a1", WALLET)) is False
    assert db.wallets.docs["user:u1"]["balance"] == 80.0


def test_free_credit_is_given_back(db):
    assert asyncio.run(an.refund_if_failed("a1", {"kind": "free_credit", "user_id": "u1"}))
    assert db.users.docs["u1"]["free_credits_remaining"] == 5


def test_launch_free_slot_only_on_the_same_day(db):
    asyncio.run(an.refund_if_failed("a1", {"kind": "launch_free", "wallet_key": "user:u1", "day": "2026-10-23"}))
    assert db.wallets.docs["user:u1"]["launch_free_daily_count"] == 2
    db.analyses.docs["a2"] = {"id": "a2", "status": "error"}
    asyncio.run(an.refund_if_failed("a2", {"kind": "launch_free", "wallet_key": "user:u1", "day": "2026-10-22"}))
    assert db.wallets.docs["user:u1"]["launch_free_daily_count"] == 2      # yesterday's allowance has rolled over


def test_the_wrapper_refunds_after_a_failed_run_and_not_after_a_good_one(db, monkeypatch):
    async def failing(analysis_id, *a, **k):
        db.analyses.docs[analysis_id]["status"] = "error"

    async def working(analysis_id, *a, **k):
        db.analyses.docs[analysis_id]["status"] = "completed"

    monkeypatch.setattr(an, "run_analysis", failing)
    asyncio.run(an._run_and_refund("a1", "RELIANCE.NS", "en", None, WALLET))
    assert db.wallets.docs["user:u1"]["balance"] == 100.0

    db.analyses.docs["a3"] = {"id": "a3", "status": "running"}
    monkeypatch.setattr(an, "run_analysis", working)
    asyncio.run(an._run_and_refund("a3", "TCS.NS", "en", None, WALLET))
    assert db.wallets.docs["user:u1"]["balance"] == 100.0 and "refunded" not in db.analyses.docs["a3"]


def test_free_runs_have_nothing_to_refund(db, monkeypatch):
    async def failing(analysis_id, *a, **k):
        db.analyses.docs[analysis_id]["status"] = "error"
    monkeypatch.setattr(an, "run_analysis", failing)
    asyncio.run(an._run_and_refund("a1", "RELIANCE.NS", "en", None, None))   # admin / billing off
    assert "refunded" not in db.analyses.docs["a1"]
