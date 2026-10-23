"""Rate limiting. Mongo-backed fixed windows, no new dependency.

WHY MONGO AND NOT AN IN-PROCESS DICT: a counter in process memory is wrong
here for two reasons that both show up in production and never in testing —
it resets on every deploy and every restart (so an attacker just waits for one,
or triggers one), and it is per-instance (so N instances mean N times the
stated limit). The counter therefore lives where the rest of the state lives.
One atomic `$inc` upsert per check, a unique index on (key, window_start) so
two concurrent requests cannot both create the window, and a TTL index so the
collection cleans itself up instead of growing forever.

WHAT IS STORED: never a raw IP, email or phone. The bucket key is
HMAC-SHA256(HASH_SECRET, "<bucket>:<raw key>"), the same pattern used for
`owner_hash`, so the counter can be matched to a caller by this server and by
nobody reading the collection.

WHICH KEY: unauthenticated endpoints key on the trusted client IP;
authenticated ones key on the USER ID, deliberately not the IP — an office, a
school or a carrier NAT is one address shared by many real people, and keying
those on IP means the busiest user locks out everyone else. Nothing is ever
keyed on a client-supplied value (a `device_id`, a header), because that is the
same mistake as trusting the left-most x-forwarded-for entry: the caller picks
it, so the caller picks their own bucket.

FAILURE MODE: if the counter itself errors, OTP requests FAIL CLOSED (they
spend real money on SMS and land in strangers' inboxes, so an unmetered OTP
endpoint is worse than a temporarily unavailable one) and everything else FAILS
OPEN (a database blip must not take a working app down). Both log.
"""
import hashlib
import hmac
import os
import time
from typing import Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from core import db, logger
from deps import HASH_SECRET

# One place to switch the whole thing off if it ever misbehaves in production.
RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() == "true"

# Whether the indexes this limiter depends on actually got built.
#   True  - built.
#   False - the build FAILED, so the unique (key, window_start) index may not
#           exist and the counter is no longer atomic: two concurrent requests
#           can both create the window and both be allowed. The app used to
#           start anyway and look healthy.
#   None  - startup has not run (a direct caller, or a unit test). Treated
#           exactly like True, so nothing that works today changes.
INDEXES_OK: Optional[bool] = None

# A 429 must be a 429. Not a 502/503/504: the app auto-retries those, so
# answering a flood with one would double the very load being limited.
TOO_MANY = "Too many requests — please slow down and try again shortly."


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        logger.warning(f"{name} is not a number; using {default}")
        return default


def bucket_key(bucket: str, raw_key: str) -> str:
    return hmac.new(HASH_SECRET.encode(), f"ratelimit:{bucket}:{raw_key}".encode(), hashlib.sha256).hexdigest()


async def ensure_indexes() -> None:
    """Called from the app's startup alongside the other index setup."""
    global INDEXES_OK
    try:
        try:
            await db.rate_limits.create_index([("key", 1), ("window_start", 1)], unique=True)
        except DuplicateKeyError:
            # Counters created while the index was missing can contain
            # duplicate (key, window_start) pairs, which makes the unique build
            # fail forever.
            #
            # This used to clear the whole collection, which is a destructive
            # write on an automatic startup path — the deploy check blocks on
            # that, and rightly: "it's only ephemeral counters" is exactly the
            # reasoning that precedes deleting something that turns out not to
            # be. So only rows the TTL monitor was ALREADY going to delete are
            # removed, which is nearly all of them (the longest window is
            # minutes), and no live counter is touched. If duplicates remain
            # among still-live windows the build fails, this boot fails closed
            # as before, and the next boot finds them expired and succeeds.
            expired = await db.rate_limits.delete_many(
                {"expires_at": {"$lte": _now_utc()}})
            logger.warning(
                "rate_limits contained duplicate windows (built while the unique index was "
                f"missing) — dropped {expired.deleted_count} already-expired counters and "
                "retried the index build; live counters were left alone")
            await db.rate_limits.create_index([("key", 1), ("window_start", 1)], unique=True)
        # Mongo's TTL monitor deletes on this field, so the collection is
        # bounded by the longest window rather than by traffic.
        await db.rate_limits.create_index("expires_at", expireAfterSeconds=0)
    except Exception as e:
        INDEXES_OK = False
        logger.error(f"INDEX SETUP FAILED for rate_limits: {e} — the limiter is no longer atomic, so "
                     "the fail-closed buckets will refuse rather than under-count")
        return
    INDEXES_OK = True
    logger.info("rate limit indexes ready (rate_limits.key+window_start unique, expires_at TTL)")


async def hit(bucket: str, raw_key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Count one request. Returns (allowed, seconds until the window resets).

    Fixed window, chosen over a sliding one because it is a single atomic
    operation: `$inc` on one document both counts and reads, so 50 requests
    arriving together produce 50 different counts and exactly `limit` of them
    are allowed. A sliding window needs a read of many timestamps and a
    decision made outside the database, which is precisely the race a limiter
    must not have.
    """
    now = time.time()
    window_start = int(now // window_seconds) * window_seconds
    reset_in = max(1, int(window_start + window_seconds - now))
    key = bucket_key(bucket, raw_key)
    for attempt in (1, 2):
        try:
            doc = await db.rate_limits.find_one_and_update(
                {"key": key, "window_start": window_start},
                {"$inc": {"count": 1},
                 "$setOnInsert": {"expires_at": _expiry(window_start, window_seconds)}},
                upsert=True,
                return_document=True,
            )
            return (doc or {}).get("count", 1) <= limit, reset_in
        except DuplicateKeyError:
            # Two requests raced to create the same window. The other one won;
            # the retry now just increments the document it created.
            if attempt == 2:
                raise
            continue
    return True, reset_in


def _now_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _expiry(window_start: int, window_seconds: int):
    from datetime import datetime, timedelta, timezone
    # A real BSON date, because a TTL index ignores anything else — an ISO
    # string here would silently never expire and the collection would grow
    # forever.
    return datetime.fromtimestamp(window_start, tz=timezone.utc) + timedelta(seconds=window_seconds * 2)


async def enforce(bucket: str, raw_key: Optional[str], limit: int, window_seconds: int,
                  fail_closed: bool = False) -> None:
    """Raise 429 if this caller is over the limit for this bucket."""
    if not RATE_LIMIT_ENABLED or not raw_key:
        return
    if INDEXES_OK is False and fail_closed:
        # Without the unique index the count can be wrong in the permissive
        # direction, and this bucket guards something that spends real money
        # (an SMS). Refusing is the lesser harm, and it is the same 429 this
        # already raises when the counter's database is unreachable.
        logger.warning(f"refusing {bucket}: the limiter's indexes failed to build, so it cannot count reliably")
        raise HTTPException(status_code=429, detail=TOO_MANY, headers={"Retry-After": "60"})
    try:
        allowed, reset_in = await hit(bucket, raw_key, limit, window_seconds)
    except Exception as e:
        logger.warning(f"rate limiter unavailable for {bucket} ({'fail closed' if fail_closed else 'fail open'}): {e}")
        if fail_closed:
            raise HTTPException(status_code=429, detail=TOO_MANY, headers={"Retry-After": "60"})
        return
    if not allowed:
        logger.info(f"rate limited: {bucket} (limit {limit}/{window_seconds}s)")
        raise HTTPException(status_code=429, detail=TOO_MANY, headers={"Retry-After": str(reset_in)})


async def global_enforce(bucket: str, limit: int, window_seconds: int, fail_closed: bool = False) -> None:
    """A whole-deployment ceiling. Keyed on a constant, since the point is that
    no per-caller rule stops a distributed source."""
    await enforce(bucket, "GLOBAL", limit, window_seconds, fail_closed=fail_closed)
