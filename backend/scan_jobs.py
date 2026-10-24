"""Keeps the scanners fresh: a background loop started with the API.

- Daily scans: once after each market's close (India ~15:45 IST, US ~16:30
  New York, crypto ~00:15 UTC — see scanner.MARKET_CLOSE), from Yahoo daily
  bars, completed sessions only. Cheap: one request per asset, spaced out.
- Live scans (India): during the NSE session, every LIVE_SECONDS from Upstox.
  If Upstox is unavailable, every YAHOO_LIVE_SECONDS from Yahoo instead, and
  the results are marked delayed so the app says so.

Results go to Mongo (`scan_results`, one document per scan), so every API
process serves the same lists and a restart doesn't empty them. A lease in
`scan_meta` makes sure only one process runs the loop at a time.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import scanner as sc
import upstox_feed
from core import db, logger
from market_data import fetch_daily_bars_sync, fetch_quote_sync

LIVE_SECONDS = 90
YAHOO_LIVE_SECONDS = 15 * 60
LEASE_SECONDS = 180
TICK_SECONDS = 30
STALE_TRADE_SECONDS = 20 * 60   # no trade this long "in session" = holiday / halted
PER_ASSET_PAUSE = 0.3           # be gentle with Yahoo

_owner = uuid.uuid4().hex
_state = {"last_live": 0.0, "last_live_source": None, "last_vix": 0.0, "last_vix_source": None}
VIX_YAHOO_SYMBOL = "^INDIAVIX"


def enabled() -> bool:
    return (os.environ.get("SCANNER_ENABLED", "true") or "").strip().lower() not in ("false", "0", "off")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def acquire_lease(now: datetime) -> bool:
    until = now + timedelta(seconds=LEASE_SECONDS)
    doc = await db.scan_meta.find_one_and_update(
        {"_id": "lease", "$or": [{"until": {"$lt": now.isoformat()}}, {"owner": _owner}]},
        {"$set": {"owner": _owner, "until": until.isoformat()}},
        return_document=ReturnDocument.AFTER,
    )
    if doc:
        return True
    try:
        await db.scan_meta.insert_one({"_id": "lease", "owner": _owner, "until": until.isoformat()})
        return True
    except DuplicateKeyError:
        return False  # another process holds it


async def save_results(group: str, results: dict[str, list], *, kind: str, source: str,
                       delayed: bool, as_of: str, notes: dict | None = None,
                       session_date: str | None = None, provisional: bool = False) -> None:
    for scan_id, items in results.items():
        await db.scan_results.update_one(
            {"_id": f"{group}.{scan_id}"},
            {"$set": {"items": items, "kind": kind, "source": source, "delayed": delayed,
                      "as_of": as_of, "note": (notes or {}).get(scan_id), "session_date": session_date,
                      "provisional": provisional}},
            upsert=True,
        )


async def refresh_daily_group(group: str, now: datetime) -> None:
    """Rebuild one market's daily scans from its completed sessions."""
    symbols = sc.universe()[group]
    tz = sc.MARKET_CLOSE[group][0]
    _, session = sc.last_close(group, now)
    rows = {}
    for sym in symbols:
        try:
            bars = await asyncio.to_thread(fetch_daily_bars_sync, sc.yahoo_symbol(group, sym))
            snap = sc.daily_snapshot(sc.completed_bars(group, bars, now), tz)
        except Exception as e:
            logger.warning(f"scanner: daily bars failed for {sym}: {e}")
            snap = None
        if snap:
            rows[sym] = snap
            await db.scan_snapshots.update_one(
                {"_id": f"{group}:{sym}"},
                {"$set": {"group": group, "symbol": sym, "snap": snap, "updated_at": now.isoformat()}},
                upsert=True,
            )
        await asyncio.sleep(PER_ASSET_PAUSE)
    if rows:
        results = sc.run_scans(sc.CLOSE_SCANS, rows)
        if group == "in" and sc.session_fraction(now) is not None:
            # Mid-session (a first deploy, or a restart before the close was
            # processed) the trend lists are live, recomputed from today's
            # price; yesterday's final values must not replace them. The
            # post-close refresh writes their final values.
            for s in sc.INTRADAY_TREND_SCANS:
                results.pop(s.id, None)
        await save_results(group, results, kind="daily", source="yahoo",
                           delayed=False, as_of=now.isoformat(), session_date=session.isoformat())
    if group == "in" and sc.session_fraction(now) is None:
        await refresh_vix(now, force=True)   # the closing value
    await acquire_lease(now_utc())  # a full refresh can outlast one lease period
    logger.info(f"scanner: daily {group} refreshed for the {session} close ({len(rows)}/{len(symbols)} assets)")


async def refresh_daily(now: datetime) -> None:
    for group in sc.universe():
        await refresh_daily_group(group, now)


async def refresh_due_daily(now: datetime) -> None:
    """Refresh each market whose latest close hasn't been processed yet —
    exactly once per close, whichever process holds the lease."""
    for group in sc.universe():
        close_at, _ = sc.last_close(group, now)
        meta = await db.scan_meta.find_one({"_id": f"daily:{group}"})
        if meta and meta.get("close") == close_at.isoformat():
            continue
        await refresh_daily_group(group, now)
        await db.scan_meta.update_one({"_id": f"daily:{group}"}, {"$set": {"close": close_at.isoformat()}},
                                      upsert=True)


def _yahoo_live(symbols: list[str]) -> dict[str, dict]:
    out = {}
    for sym in symbols:
        try:
            q = fetch_quote_sync(sc.yahoo_symbol("in", sym))
            if q.get("price"):
                out[sym] = {"price": q["price"], "prev_close": q.get("previousClose"), "volume": q.get("volume")}
        except Exception as e:
            logger.warning(f"scanner: yahoo quote failed for {sym}: {e}")
        time.sleep(PER_ASSET_PAUSE)
    return out


async def refresh_live(now: datetime, fraction: float) -> None:
    symbols = sc.universe()["in"]
    source, delayed = "upstox", False
    try:
        quotes = await asyncio.to_thread(upstox_feed.fetch_quotes, symbols)
        age = upstox_feed.newest_trade_age_seconds(quotes, now.timestamp())
        if age is not None and age > STALE_TRADE_SECONDS:
            # Inside session hours but nothing has traded for 20 minutes: an
            # exchange holiday or a halt. Keep the last real lists.
            _state["last_live"] = now.timestamp()
            return
    except upstox_feed.UpstoxUnavailable as e:
        if upstox_feed.configured():
            logger.warning(f"scanner: Upstox unavailable, using Yahoo (delayed): {e}")
        if _state["last_live_source"] == "yahoo" and now.timestamp() - _state["last_live"] < YAHOO_LIVE_SECONDS:
            return
        quotes = await asyncio.to_thread(_yahoo_live, symbols)
        source, delayed = "yahoo", True
    if not quotes:
        return
    snaps = {d["symbol"]: d["snap"] async for d in db.scan_snapshots.find({"group": "in"})}
    today = now.astimezone(sc.IST).date().isoformat()
    rows = {s: sc.live_row(q, snaps.get(s), today) for s, q in quotes.items()}
    scans = sc.live_scans(fraction)
    results = sc.run_scans(scans, rows)
    notes = {}
    if not any(s.id == "live_volume_surge" for s in scans):
        results["live_volume_surge"] = []
        notes["live_volume_surge"] = "Available from about 30 minutes after the open."
    await save_results("in", results, kind="live", source=source, delayed=delayed,
                       as_of=now.isoformat(), notes=notes)
    # The trend scans (RSI, momentum, 52-week high/low) recomputed with the
    # live price as today's provisional close. Marked provisional; the daily
    # refresh after the close replaces them with the final values.
    trend_rows = {s: sc.intraday_trend_row(snaps.get(s), q.get("price")) for s, q in quotes.items()}
    await save_results("in", sc.run_scans(sc.INTRADAY_TREND_SCANS, trend_rows), kind="daily",
                       source=source, delayed=delayed, as_of=now.isoformat(), provisional=True)
    _state.update(last_live=now.timestamp(), last_live_source=source)


def _vix_quote() -> tuple[dict, str, bool]:
    """India VIX from Upstox, else Yahoo's delayed quote."""
    try:
        q = upstox_feed.fetch_india_vix()
        return {"price": q["price"], "prev_close": q.get("prev_close")}, "upstox", False
    except upstox_feed.UpstoxUnavailable as e:
        if upstox_feed.configured():
            logger.warning(f"scanner: India VIX from Upstox failed, using Yahoo (delayed): {e}")
    q = fetch_quote_sync(VIX_YAHOO_SYMBOL)
    return {"price": q.get("price"), "prev_close": q.get("previousClose")}, "yahoo", True


async def refresh_vix(now: datetime, *, force: bool = False) -> None:
    """Store India VIX in scan_meta for the Scans screen's tile. In session
    it follows the live cadence (Yahoo at most every 15 minutes); `force`
    takes the closing value after the close. A failure keeps the last value."""
    gap = YAHOO_LIVE_SECONDS if _state["last_vix_source"] == "yahoo" else LIVE_SECONDS
    if not force and now.timestamp() - _state["last_vix"] < gap:
        return
    _state["last_vix"] = now.timestamp()
    try:
        q, source, delayed = await asyncio.to_thread(_vix_quote)
    except Exception as e:
        logger.warning(f"scanner: India VIX unavailable: {e}")
        return
    price, prev = q.get("price"), q.get("prev_close")
    if not price:
        return
    _state["last_vix_source"] = source
    await db.scan_meta.update_one(
        {"_id": "india_vix"},
        {"$set": {"value": round(float(price), 2),
                  "change_pct": round((float(price) / float(prev) - 1) * 100, 2) if prev else None,
                  "as_of": now.isoformat(), "source": source, "delayed": delayed}},
        upsert=True,
    )


async def tick(now: datetime) -> None:
    # Live first: a process waking up mid-session must not hold live data
    # back behind a 2-3 minute daily rebuild.
    fraction = sc.session_fraction(now)
    if fraction is not None:
        # Upstox is retried every LIVE_SECONDS; while it is down, refresh_live
        # itself holds the Yahoo fallback to one pass per YAHOO_LIVE_SECONDS.
        if now.timestamp() - _state["last_live"] >= LIVE_SECONDS:
            await refresh_live(now, fraction)
        await refresh_vix(now)
    await refresh_due_daily(now)


async def run_forever() -> None:
    """Started once per process; only the lease holder does any work."""
    while True:
        try:
            now = now_utc()
            if await acquire_lease(now):
                await tick(now)
        except Exception as e:
            logger.error(f"scanner loop error: {e}")
        await asyncio.sleep(TICK_SECONDS)
