"""M1, the other half: a failing index must not skip the ones after it.

`ensure_payment_indexes` is the real startup hook, so this drives it directly
with a database object that fails on a chosen collection — the situation that
actually happens (a leftover conflicting index from an older deploy) without
having to create one.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rate_limit as rl  # noqa: E402
import server  # noqa: E402
from tests.async_loop import run_async  # noqa: E402


class FakeCollection:
    def __init__(self, name, record, fail_on):
        self.name, self.record, self.fail_on = name, record, fail_on

    async def create_index(self, keys, **kwargs):
        if self.name == self.fail_on:
            raise RuntimeError(f"simulated index conflict on {self.name}")
        self.record.append((self.name, keys))

    async def index_information(self):
        return {}

    async def drop_index(self, name):
        return None

    async def delete_many(self, *a, **k):
        return None


class FakeDb:
    def __init__(self, fail_on):
        self.record, self.fail_on = [], fail_on

    def __getattr__(self, name):
        return FakeCollection(name, self.record, self.fail_on)

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture
def fake_db(monkeypatch):
    def install(fail_on):
        db = FakeDb(fail_on)
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setattr(rl, "db", db)
        return db
    before = rl.INDEXES_OK
    yield install
    rl.INDEXES_OK = before


def built(record, collection, field):
    for name, keys in record:
        if name != collection:
            continue
        flat = [keys] if isinstance(keys, str) else [k for k, _ in keys]
        if field in flat:
            return True
    return False


def test_an_early_index_failure_no_longer_skips_the_limiter(fake_db):
    """The exact regression: `payments` failing used to abandon everything
    after it — including the two indexes the rate limiter needs."""
    db = fake_db(fail_on="payments")
    run_async(server.ensure_payment_indexes())

    assert not built(db.record, "payments", "reference_id"), "the simulated failure didn't happen"
    # Everything downstream still got built.
    assert built(db.record, "wallet_ledger", "payment_id")
    assert built(db.record, "webhook_events", "event_id")
    assert built(db.record, "free_credit_tombstones", "hash")
    assert built(db.record, "rate_limits", "key")
    assert built(db.record, "rate_limits", "expires_at")
    assert rl.INDEXES_OK is True


def test_a_failure_in_the_middle_does_not_stop_the_tail(fake_db):
    db = fake_db(fail_on="otp_requests")
    run_async(server.ensure_payment_indexes())
    assert built(db.record, "rate_limits", "key")
    assert built(db.record, "free_credit_tombstones", "hash")


def test_the_limiter_marks_itself_broken_when_its_own_index_fails(fake_db):
    """The flag is the whole point: it is what turns a silently non-atomic
    limiter into a refusal on the bucket that spends money."""
    fake_db(fail_on="rate_limits")
    run_async(server.ensure_payment_indexes())
    assert rl.INDEXES_OK is False


def test_duplicate_counters_are_cleared_so_the_unique_index_can_build(monkeypatch):
    """Counters written while the unique index was missing can contain
    duplicate windows, which makes the build fail forever. They are ephemeral
    counts worth nothing, so they are dropped and the build retried once."""
    from pymongo.errors import DuplicateKeyError

    state = {"attempts": 0, "deleted": False}

    class Coll:
        async def create_index(self, keys, **kwargs):
            if keys == [("key", 1), ("window_start", 1)]:
                state["attempts"] += 1
                if state["attempts"] == 1:
                    raise DuplicateKeyError("duplicate window")
            return None

        async def delete_many(self, *a, **k):
            state["deleted"] = True

    class Db:
        rate_limits = Coll()

    before = rl.INDEXES_OK
    monkeypatch.setattr(rl, "db", Db())
    try:
        run_async(rl.ensure_indexes())
        assert state["attempts"] == 2, "the build was not retried"
        assert state["deleted"] is True, "the duplicate counters were not cleared"
        assert rl.INDEXES_OK is True
    finally:
        rl.INDEXES_OK = before


def test_no_response_gained_a_new_field(fake_db):
    """The spec forbids exposing this state on /api/pay/health: a test pins
    that response's keys, and it is already more information than a public
    endpoint should give."""
    import inspect

    from routes import payments
    source = inspect.getsource(payments.pay_health)
    for leak in ("INDEXES_OK", "indexes_ok", "rate_limit"):
        assert leak not in source, f"/api/pay/health now exposes {leak}"
