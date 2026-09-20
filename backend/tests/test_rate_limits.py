"""Rate limiting: that each limit fires, that it can't be bypassed, that it
can't be raced, and — the part that actually matters in production — that
normal use of this app never reaches one.

A limit a real user can hit is not a security control, it is an outage. So
alongside the "429 at N+1" checks, the frontend's real polling intervals are
replayed against the same buckets the app uses.
"""
import asyncio
import os
import sys
import time
import uuid

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import limits as lim  # noqa: E402
import rate_limit as rl  # noqa: E402
from tests.async_loop import run_async  # noqa: E402
from auth_helper import AUTH_HEADERS, USER_ID  # noqa: E402

BASE = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://localhost:8001").rstrip("/") + "/api"


def fresh_ip():
    """A distinct client per test, so one test's traffic never spends
    another's allowance (conftest does the same thing for every other
    module)."""
    n = uuid.uuid4().int
    return f"198.51.{n % 250}.{(n >> 8) % 250 + 1}"


def window_headroom(seconds_needed: float = 6.0):
    """Fixed windows reset on a wall-clock boundary, so a test that straddles
    one splits its requests across two buckets and no limit fires — which is
    correct behaviour and a broken test. Wait out the boundary first.

    Found the honest way: this test failed in a full-suite run because bogus
    ticker lookups took ~1.5s each and eleven of them crossed a minute."""
    now = time.time()
    remaining = 60 - (now % 60)
    if remaining < seconds_needed:
        time.sleep(remaining + 0.2)


def fresh_account():
    """Own account per test, so a limit this test spends is never a limit
    another test needed (the shared auth_helper user is used by many)."""
    import auth as au
    from pymongo import MongoClient
    from datetime import datetime, timezone

    db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]
    uid = f"rl-{uuid.uuid4()}"
    db.users.insert_one({
        "id": uid, "phone": None, "email": f"{uid}@example.com", "identity_type": "email",
        "free_credits_remaining": 0,
        "consent": {"agreed": True, "agreed_at": "2026-01-01T00:00:00+00:00", "version": "1.0"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    db.wallets.update_one({"device_id": f"user:{uid}"},
                          {"$set": {"device_id": f"user:{uid}", "balance": 50.0, "currency": "USD"}},
                          upsert=True)
    token = au.create_session_token(uid, os.environ["JWT_SECRET"])
    return uid, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def get(path, ip, **kw):
    return requests.get(f"{BASE}{path}", headers={"X-Forwarded-For": ip}, timeout=30, **kw)


# --- the counter itself ----------------------------------------------------

def test_the_window_allows_exactly_the_limit_then_refuses():
    key = f"unit-{uuid.uuid4().hex}"
    results = [run_async(rl.hit("test_bucket", key, limit=5, window_seconds=60)) for _ in range(7)]
    assert [allowed for allowed, _ in results] == [True] * 5 + [False] * 2


def test_the_window_resets():
    """A 1-second window rather than waiting a real minute — the window length
    is arithmetic, not behaviour."""
    key = f"unit-{uuid.uuid4().hex}"
    assert (run_async(rl.hit("test_bucket", key, 1, 1)))[0] is True
    assert (run_async(rl.hit("test_bucket", key, 1, 1)))[0] is False
    time.sleep(1.1)
    assert (run_async(rl.hit("test_bucket", key, 1, 1)))[0] is True


def test_fifty_concurrent_requests_never_exceed_the_limit():
    """The whole reason the counter is a single atomic `$inc` upsert and not a
    read-then-write: 50 requests arriving together must not each see the same
    count and each pass."""
    key = f"unit-{uuid.uuid4().hex}"
    results = run_async(asyncio.gather(*[rl.hit("test_bucket", key, 10, 60) for _ in range(50)]))
    assert sum(1 for allowed, _ in results if allowed) == 10


def test_two_callers_have_independent_buckets():
    a, b = f"unit-{uuid.uuid4().hex}", f"unit-{uuid.uuid4().hex}"
    for _ in range(3):
        run_async(rl.hit("test_bucket", a, 3, 60))
    assert (run_async(rl.hit("test_bucket", a, 3, 60)))[0] is False
    assert (run_async(rl.hit("test_bucket", b, 3, 60)))[0] is True


def test_no_raw_identifier_is_stored():
    """The collection is readable by anything with database access, so it must
    not become a log of who used the app from where."""
    ip = "203.0.113.42"
    run_async(rl.hit("test_bucket", ip, 5, 60))
    from core import db
    rows = run_async(db.rate_limits.find({}, {"_id": 0}).to_list(500))
    blob = str(rows)
    assert ip not in blob
    assert rl.bucket_key("test_bucket", ip) in blob


# --- failure modes --------------------------------------------------------

def test_otp_requests_fail_closed_when_the_limiter_is_broken():
    """An OTP request spends money (SMS) and reaches a stranger's phone, so an
    unmetered OTP endpoint is worse than a temporarily unavailable one."""
    real = rl.hit
    rl.hit = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("mongo down"))
    try:
        with pytest.raises(Exception) as caught:
            run_async(rl.enforce("otp_request", "1.2.3.4", 3, 60, fail_closed=True))
        assert caught.value.status_code == 429
        assert caught.value.headers["Retry-After"]
    finally:
        rl.hit = real


def test_everything_else_fails_open_when_the_limiter_is_broken():
    """A database blip must not take a working app down."""
    real = rl.hit
    rl.hit = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("mongo down"))
    try:
        run_async(rl.enforce("market", "1.2.3.4", 120, 60))  # must not raise
    finally:
        rl.hit = real


def test_the_otp_request_route_is_the_one_wired_to_fail_closed():
    assert lim.limit_otp_request.fail_closed is True
    for limiter in (lim.limit_market, lim.limit_portfolio, lim.limit_pay_config,
                    lim.limit_otp_verify, lim.limit_auth_exchange, lim.limit_pay_callback):
        assert limiter.fail_closed is False


# --- over HTTP, on the real routes ----------------------------------------

def test_market_group_shares_one_ceiling_and_answers_429_with_retry_after():
    """One bucket across search/quote/chart/trending, because they hit the
    same upstream quota — spending it all on one path must count."""
    ip = fresh_ip()
    window_headroom(10)
    last = None
    for i in range(lim.MARKET_PER_MIN + 2):
        last = get("/trending", ip)
        if last.status_code == 429:
            assert i + 1 == lim.MARKET_PER_MIN + 1, f"limited at {i + 1}, expected {lim.MARKET_PER_MIN + 1}"
            break
    assert last.status_code == 429
    assert last.headers.get("Retry-After"), "a 429 without Retry-After leaves the client guessing"
    assert int(last.headers["Retry-After"]) <= 60
    # The app reads `detail`; a limit must never surface as 502/503/504, which
    # the app auto-retries — that would double the load being limited.
    assert last.json()["detail"]
    assert last.status_code not in (502, 503, 504)


def test_a_forged_x_forwarded_for_cannot_dodge_the_limit():
    """The bypass this whole design exists to close: a new claimed address on
    every request. Each one arrives with the trusted hops appended, so all of
    them resolve to the same client."""
    real = fresh_ip()
    window_headroom(20)
    codes = []
    for i in range(lim.PORTFOLIO_PER_MIN + 2):
        res = requests.post(
            f"{BASE}/portfolio/optimize",
            # Pretending to be a different client each time, exactly as a
            # script would. `real` sits where our own edge would have put it.
            headers={"X-Forwarded-For": f"203.0.113.{i % 250},{real},104.22.64.124,34.160.159.238"},
            json={"holdings": [{"symbol": "AAPL", "quantity": 1, "avg_price": 1},
                               {"symbol": "MSFT", "quantity": 1, "avg_price": 1}],
                  "objective": "hrp", "use_agent_views": False, "cash": 0},
            timeout=60,
        )
        codes.append(res.status_code)
        if res.status_code == 429:
            break
    assert 429 in codes, "forged addresses bypassed the limit"
    assert codes.index(429) == lim.PORTFOLIO_PER_MIN


def test_authenticated_limits_key_on_the_user_not_the_address():
    """A shared office, school or carrier NAT is one address for many real
    people. Keying those on IP means the busiest person locks everyone else
    out, so the bucket must follow the ACCOUNT — proven by sending the same
    token from three unrelated addresses and finding all three counted in the
    account's single bucket, with nothing counted against the addresses."""
    from core import db

    uid, headers = fresh_account()
    addresses = ["203.0.113.11", "198.51.100.22", "192.0.2.33"]
    for ip in addresses:
        res = requests.get(f"{BASE}/wallet/balance", headers={**headers, "X-Forwarded-For": ip}, timeout=30)
        assert res.status_code == 200, res.text

    user_rows = run_async(db.rate_limits.find({"key": rl.bucket_key("poll", f"user:{uid}")}).to_list(10))
    assert sum(r["count"] for r in user_rows) == len(addresses)
    for ip in addresses:
        ip_rows = run_async(db.rate_limits.find({"key": rl.bucket_key("poll", ip)}).to_list(10))
        assert not ip_rows, f"{ip} got its own bucket — a NAT would lock real users out"


def test_health_and_both_webhooks_are_never_limited():
    """A 429 on the readiness probe takes the deployment down; a 429 on a
    webhook is a customer who paid and wasn't credited."""
    root = BASE.rsplit("/api", 1)[0]
    for _ in range(lim.MARKET_PER_MIN + 20):
        res = requests.get(f"{root}/health", timeout=15)
        assert res.status_code == 200
    for path in ("/pay/webhook", "/pay/iap/webhook"):
        for _ in range(80):
            res = requests.post(f"{BASE}{path}", json={"event": "noise"}, timeout=15)
            assert res.status_code != 429, f"{path} was rate limited"


# --- normal use must never be limited -------------------------------------

def test_the_apps_real_polling_is_nowhere_near_the_limit():
    """Replays the measured intervals from the frontend against the same
    bucket the app uses:
      analysis/[id].tsx:77  poll every 1.5s   -> 40/min
      compare.tsx:215       two of those      -> 80/min
      analysis/[id].tsx:130 quote every 30s   ->  2/min
      WalletCard.tsx        12 status polls    -> ~24/min
    The busiest minute the product can produce is the Compare screen, so all
    of these together are the honest worst case."""
    key = f"user:sim-{uuid.uuid4().hex}"
    busiest_minute = 80 + 2 + 24
    results = [run_async(rl.hit("poll", key, lim.POLL_PER_MIN, 60)) for _ in range(busiest_minute)]
    assert all(allowed for allowed, _ in results), "the app's own polling would be rate limited"
    # The spec's own sizing rule: at least 3x the measured peak.
    assert lim.POLL_PER_MIN >= 3 * 80


def test_three_minutes_of_analysis_polling_is_fine():
    """A single analysis run polls for minutes; each fresh window starts the
    count again, which is the point of a fixed window here."""
    key = f"user:sim-{uuid.uuid4().hex}"
    for _ in range(3):
        results = [run_async(rl.hit("poll", key, lim.POLL_PER_MIN, 60)) for _ in range(40)]
        assert all(allowed for allowed, _ in results)


def test_a_normal_analyze_burst_is_allowed():
    """Six runs a minute is the limit; a person tapping Execute a few times,
    or the Compare screen starting two at once, must pass."""
    assert lim.ANALYZE_PER_MIN >= 6
    assert lim.ANALYZE_MAX_CONCURRENT >= 3


def test_every_limit_is_env_overridable():
    """So a limit can be retuned in production without a code change."""
    for name in ("RL_OTP_REQUEST_PER_MIN", "RL_OTP_VERIFY_PER_10MIN", "RL_AUTH_EXCHANGE_PER_MIN",
                 "RL_ANALYZE_PER_MIN", "RL_ANALYZE_MAX_CONCURRENT", "RL_POLL_PER_MIN",
                 "RL_PAY_ORDER_PER_MIN", "RL_PAY_CALLBACK_PER_MIN", "RL_PAY_CONFIG_PER_MIN",
                 "RL_NEWS_LLM_PER_MIN", "RL_NEWS_LLM_GLOBAL_PER_MIN", "RL_MARKET_PER_MIN",
                 "RL_PORTFOLIO_PER_MIN"):
        assert name in open(os.path.join(os.path.dirname(__file__), "..", "limits.py")).read()


def test_otp_verify_answers_429_at_the_limit():
    """30 per 10 minutes per address. Deliberately NOT a per-identifier
    lockout: someone spamming YOUR email or number must not be able to lock
    YOU out of signing in, so the existing 5-attempts-per-code stays as the
    only identifier-scoped limit."""
    ip = fresh_ip()
    codes = []
    for _ in range(lim.OTP_VERIFY_PER_10MIN + 2):
        res = requests.post(f"{BASE}/auth/otp/verify",
                            headers={"X-Forwarded-For": ip},
                            json={"identifier": f"rl{uuid.uuid4().hex[:8]}@gmail.com", "code": "000000"},
                            timeout=30)
        codes.append(res.status_code)
        if res.status_code == 429:
            break
    assert codes.index(429) == lim.OTP_VERIFY_PER_10MIN
    assert 200 not in codes


def test_pay_health_is_limited_but_still_diagnosable():
    ip = fresh_ip()
    window_headroom(10)
    codes = [get("/pay/health", ip).status_code for _ in range(lim.PAY_CONFIG_PER_MIN + 1)]
    assert codes.count(200) == lim.PAY_CONFIG_PER_MIN
    assert codes[-1] == 429


def test_news_counts_only_the_expensive_misses():
    """The limit is on cache MISSES, which are what cost a Yahoo fetch and an
    LLM classification call — a cache hit is a dict read and refusing one would
    punish readers while saving nothing."""
    ip = fresh_ip()
    # Each bogus lookup still costs a Yahoo round trip, so fill the allowance
    # through the counter and spend only the last one over HTTP — eleven real
    # lookups took long enough to cross a window boundary, which split the
    # count and made the test lie.
    for _ in range(lim.NEWS_LLM_PER_MIN):
        assert run_async(rl.hit("news_llm", ip, lim.NEWS_LLM_PER_MIN, 60))[0] is True
    assert get("/news/ZQX1", ip).status_code == 429

    # ...and a cache HIT is never limited: AAPL is cached by the first call,
    # so a hundred more from an address that has already exhausted its miss
    # allowance still succeed.
    warm = get("/news/AAPL", fresh_ip())
    assert warm.status_code == 200
    assert all(get("/news/AAPL", ip).status_code == 200 for _ in range(5))


def test_concurrent_analyses_are_capped_per_account():
    """Six starts a minute still leaves six long LLM runs executing at once,
    so what is in flight is capped too."""
    from datetime import datetime, timedelta, timezone

    from deps import owner_hash_for
    from pymongo import MongoClient

    db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]
    uid, headers = fresh_account()
    owner = owner_hash_for({"id": uid})
    now = datetime.now(timezone.utc)
    ids = []
    for i in range(lim.ANALYZE_MAX_CONCURRENT):
        aid = str(uuid.uuid4())
        ids.append(aid)
        db.analyses.insert_one({"id": aid, "symbol": "AAPL", "status": "running",
                                "owner_hash": owner, "created_at": now.isoformat(),
                                "updated_at": now.isoformat()})
    try:
        res = requests.post(f"{BASE}/analyze", headers=headers,
                            json={"symbol": "AAPL", "device_id": f"dev-{uuid.uuid4().hex[:8]}"},
                            timeout=60)
        assert res.status_code == 429, res.text
        assert "running" in res.json()["detail"]
        assert res.headers.get("Retry-After")
    finally:
        db.analyses.delete_many({"id": {"$in": ids}})


def test_a_crashed_run_cannot_lock_an_account_out_forever():
    """A run whose process died stays `status: running` for good. Without the
    10-minute window, three of those would permanently deny the account any
    analysis — a self-inflicted outage the user could never clear."""
    from datetime import datetime, timedelta, timezone

    from deps import owner_hash_for
    from pymongo import MongoClient

    db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]
    uid, headers = fresh_account()
    owner = owner_hash_for({"id": uid})
    stale = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    ids = []
    for _ in range(lim.ANALYZE_MAX_CONCURRENT + 2):
        aid = str(uuid.uuid4())
        ids.append(aid)
        db.analyses.insert_one({"id": aid, "symbol": "AAPL", "status": "running",
                                "owner_hash": owner, "created_at": stale, "updated_at": stale})
    try:
        res = requests.post(f"{BASE}/analyze", headers=headers,
                            json={"symbol": "AAPL", "device_id": f"dev-{uuid.uuid4().hex[:8]}"},
                            timeout=90)
        assert res.status_code != 429, res.text
        started = res.json().get("id")
        if started:
            ids.append(started)
            db.analyses.update_one({"id": started}, {"$set": {"status": "error"}})
    finally:
        db.analyses.delete_many({"id": {"$in": ids}})


def test_every_declared_limit_refuses_at_n_plus_one():
    """Table-driven over the limits themselves, so a new limiter added later
    without a test still gets this one."""
    declared = [
        ("otp_request", lim.OTP_REQUEST_PER_MIN, 60),
        ("otp_verify", lim.OTP_VERIFY_PER_10MIN, 600),
        ("auth_exchange", lim.AUTH_EXCHANGE_PER_MIN, 60),
        ("analyze", lim.ANALYZE_PER_MIN, 60),
        ("poll", lim.POLL_PER_MIN, 60),
        ("pay_order", lim.PAY_ORDER_PER_MIN, 60),
        ("pay_callback", lim.PAY_CALLBACK_PER_MIN, 60),
        ("pay_config", lim.PAY_CONFIG_PER_MIN, 60),
        ("news_llm", lim.NEWS_LLM_PER_MIN, 60),
        ("market", lim.MARKET_PER_MIN, 60),
        ("portfolio", lim.PORTFOLIO_PER_MIN, 60),
    ]
    for bucket, limit, window in declared:
        key = f"table-{uuid.uuid4().hex}"
        allowed = [run_async(rl.hit(bucket, key, limit, window))[0] for _ in range(limit)]
        assert all(allowed), bucket
        assert run_async(rl.hit(bucket, key, limit, window))[0] is False, bucket
