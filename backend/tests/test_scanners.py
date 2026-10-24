"""Stock scanners: the scan maths, the Upstox parsing and its Yahoo fallback,
the background refresh and the API — all offline, with fake bars, fake quotes
and an in-memory stand-in for Mongo. Nothing here calls Upstox or Yahoo."""
import asyncio
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scanner as sc  # noqa: E402
import scan_jobs  # noqa: E402
import upstox_feed  # noqa: E402
from routes import scans as scan_routes  # noqa: E402

IST = sc.IST
DAY0 = int(datetime(2025, 10, 1, 3, 45, tzinfo=timezone.utc).timestamp())  # 09:15 IST


def bars_from(closes, volume=1_000_000, last_volume=None, start=DAY0):
    out = []
    for i, c in enumerate(closes):
        v = last_volume if (last_volume is not None and i == len(closes) - 1) else volume
        out.append({"time": start + i * 86400, "open": c, "high": c * 1.005, "low": c * 0.995, "close": c, "volume": v})
    return out


def rising(n=260):
    return [100 * (1.003 ** i) for i in range(n)]


def falling(n=260):
    return [100 * (0.997 ** i) for i in range(n)]


# --- snapshot and daily scans ------------------------------------------------

def test_snapshot_needs_enough_history():
    assert sc.daily_snapshot(bars_from(rising(20))) is None


def test_steady_rise_is_near_its_52_week_high_and_overbought():
    snap = sc.daily_snapshot(bars_from(rising()))
    res = sc.run_scans(sc.DAILY_SCANS, {"UP": snap})
    assert [i["symbol"] for i in res["near_52w_high"]] == ["UP"]
    assert res["near_52w_low"] == []
    assert snap["rsi14"] > 70 and res["rsi_overbought"][0]["label"].startswith("RSI ")


def test_steady_fall_is_near_its_52_week_low_and_oversold():
    snap = sc.daily_snapshot(bars_from(falling()))
    res = sc.run_scans(sc.DAILY_SCANS, {"DOWN": snap})
    assert [i["symbol"] for i in res["near_52w_low"]] == ["DOWN"]
    assert [i["symbol"] for i in res["rsi_oversold"]] == ["DOWN"]


def test_unusual_volume_uses_the_average_before_the_last_session():
    snap = sc.daily_snapshot(bars_from(rising(), last_volume=3_000_000))
    assert snap["volume_ratio"] == pytest.approx(3.0)
    res = sc.run_scans(sc.DAILY_SCANS, {"V": snap})
    assert res["volume_spike"][0]["label"] == "3.0× avg volume"


def test_scans_sort_best_first_and_respect_the_limit():
    rows = {f"S{i}": sc.daily_snapshot(bars_from(falling(), last_volume=(2 + i) * 1_000_000)) for i in range(30)}
    res = sc.run_scans(sc.DAILY_SCANS, rows)["volume_spike"]
    assert len(res) == 25 and res[0]["symbol"] == "S29"


def test_items_carry_price_and_change():
    item = sc.run_scans(sc.DAILY_SCANS, {"UP": sc.daily_snapshot(bars_from(rising()))})["near_52w_high"][0]
    assert item["price"] > 0 and item["change_pct"] == pytest.approx(0.3, abs=0.01)


def test_catalog_ids_are_unique_and_titles_never_advise():
    cat = sc.scan_catalog()
    ids = [c["id"] for c in cat]
    assert len(ids) == len(set(ids))
    assert {"in.live_gainers", "in.rsi_oversold", "us.near_52w_high", "crypto.volume_spike"} <= set(ids)
    for c in cat:
        text = (c["title"] + " " + c["description"]).lower()
        assert not any(w in text for w in (" buy", "sell", "recommend", "should")), c


def test_universe_can_be_overridden(monkeypatch):
    monkeypatch.setenv("SCAN_UNIVERSE_IN", "reliance, TCS tcs")
    assert sc.universe()["in"] == ["RELIANCE", "TCS"]
    assert sc.yahoo_symbol("in", "RELIANCE") == "RELIANCE.NS" and sc.yahoo_symbol("us", "AAPL") == "AAPL"


# --- session and live scans --------------------------------------------------

def ist(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def test_session_fraction():
    assert sc.session_fraction(ist(2026, 10, 22, 9, 0)) is None          # before the open
    assert sc.session_fraction(ist(2026, 10, 22, 9, 15)) == 0
    assert sc.session_fraction(ist(2026, 10, 22, 15, 30)) == 1
    assert sc.session_fraction(ist(2026, 10, 22, 15, 31)) is None        # after the close
    assert sc.session_fraction(ist(2026, 10, 24, 11, 0)) is None         # Saturday
    assert sc.session_fraction(ist(2026, 10, 22, 12, 22)) == pytest.approx(0.5, abs=0.01)


def test_live_row_compares_with_the_year_before_today():
    closes = rising()
    snap = sc.daily_snapshot(bars_from(closes))
    last_day = snap["last_bar_date"]
    # Last bar is today's own session: the old high excludes it.
    row_today = sc.live_row({"price": closes[-1], "prev_close": closes[-2], "volume": 5}, snap, last_day)
    assert row_today["high_before_today"] == snap["prior_high_52w"]
    # Last bar is yesterday: the whole window is "before today".
    row_next = sc.live_row({"price": closes[-1], "prev_close": closes[-1], "volume": 5}, snap, "2099-01-01")
    assert row_next["high_before_today"] == snap["high_52w"]


def test_live_gainers_losers_and_new_highs():
    snap = sc.daily_snapshot(bars_from([100.0] * 260))
    quotes = {"A": (106, 100), "B": (97, 100), "C": (101, 100), "D": (99.5, 100)}
    rows = {s: sc.live_row({"price": p, "prev_close": pc, "volume": 1}, snap, "2099-01-01") for s, (p, pc) in quotes.items()}
    res = sc.run_scans(sc.live_scans(0.5), rows)
    assert [i["symbol"] for i in res["live_gainers"]] == ["A", "C"]
    assert [i["symbol"] for i in res["live_losers"]] == ["B", "D"]
    assert [i["symbol"] for i in res["live_52w_high"]] == ["A", "C"]  # old high was 100.5


def test_volume_surge_scales_by_time_of_day_and_waits_for_the_first_half_hour():
    snap = sc.daily_snapshot(bars_from([100.0] * 260, volume=1_000_000))
    row = sc.live_row({"price": 100, "prev_close": 100, "volume": 500_000}, snap, "2099-01-01")
    assert "live_volume_surge" not in {s.id for s in sc.live_scans(0.05)}
    # Half the normal day's volume by 20% of the session = 2.5x pace.
    res = sc.run_scans(sc.live_scans(0.2), {"X": row})
    assert res["live_volume_surge"][0]["label"] == "2.5× normal pace"


# --- Upstox parsing ----------------------------------------------------------

def test_parse_instruments_keeps_nse_equities_only():
    rows = [
        {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "RELIANCE", "instrument_key": "NSE_EQ|INE002A01018"},
        {"segment": "NSE_FO", "instrument_type": "FUT", "trading_symbol": "RELIANCE25OCTFUT", "instrument_key": "NSE_FO|1"},
        {"segment": "NSE_EQ", "instrument_type": "EQ", "tradingsymbol": "tcs", "instrument_key": "NSE_EQ|INE467B01029"},
        "junk",
    ]
    assert upstox_feed.parse_instruments(rows) == {"RELIANCE": "NSE_EQ|INE002A01018", "TCS": "NSE_EQ|INE467B01029"}


def test_parse_quotes_matches_by_instrument_token():
    payload = {"status": "success", "data": {
        "NSE_EQ:RELIANCE": {"instrument_token": "NSE_EQ|INE002A01018", "last_price": 1210.5, "volume": 3_500_000,
                            "ohlc": {"open": 1200, "high": 1215, "low": 1198, "close": 1200.0}, "net_change": 10.5,
                            "last_trade_time": "1761100000000"},
        "NSE_EQ:TCS": {"instrument_token": "NSE_EQ|INE467B01029", "last_price": 3000, "volume": 10,
                       "ohlc": {}, "net_change": -30},
    }}
    keys = {"NSE_EQ|INE002A01018": "RELIANCE", "NSE_EQ|INE467B01029": "TCS"}
    q = upstox_feed.parse_quotes(payload, keys)
    assert q["RELIANCE"] == {"price": 1210.5, "prev_close": 1200.0, "volume": 3_500_000, "last_trade_ms": 1761100000000}
    assert q["TCS"]["prev_close"] == 3030  # from net_change


def test_previous_close_comes_from_net_change_not_ohlc_close():
    # What the live API actually returned on 22 Oct 2026: ohlc.close equals
    # the last price. Using it would make every change 0%.
    payload = {"status": "success", "data": {"NSE_EQ:INFY": {
        "instrument_token": "NSE_EQ|INE009A01021", "last_price": 1153.35, "net_change": -12.65,
        "ohlc": {"open": 1165, "high": 1170, "low": 1150, "close": 1153.35}, "volume": 17007168}}}
    q = upstox_feed.parse_quotes(payload, {"NSE_EQ|INE009A01021": "INFY"})["INFY"]
    assert q["prev_close"] == pytest.approx(1166.0)
    # No net_change and ohlc.close == price: unknown, rather than a fake 0%.
    payload["data"]["NSE_EQ:INFY"].pop("net_change")
    assert upstox_feed.parse_quotes(payload, {"NSE_EQ|INE009A01021": "INFY"})["INFY"]["prev_close"] is None


def test_parse_quotes_rejects_an_error_response():
    with pytest.raises(upstox_feed.UpstoxUnavailable):
        upstox_feed.parse_quotes({"status": "error", "errors": [{"message": "Invalid token"}]}, {})


class _Resp:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def known_instruments(monkeypatch):
    monkeypatch.setitem(upstox_feed._instruments, "keys", {"RELIANCE": "NSE_EQ|INE002A01018"})
    monkeypatch.setitem(upstox_feed._instruments, "fetched", 1e18)


def test_no_token_means_unavailable(monkeypatch):
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN", raising=False)
    with pytest.raises(upstox_feed.UpstoxUnavailable):
        upstox_feed.fetch_quotes(["RELIANCE"])


def test_rejected_token_means_unavailable(monkeypatch, known_instruments):
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "t")
    monkeypatch.setattr(upstox_feed.requests, "get", lambda *a, **k: _Resp(401))
    with pytest.raises(upstox_feed.UpstoxUnavailable, match="expired or been revoked"):
        upstox_feed.fetch_quotes(["RELIANCE"])


def test_falls_back_to_the_older_parameter_name_once(monkeypatch, known_instruments):
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "t")
    calls = []
    ok = {"status": "success", "data": {"x": {"instrument_token": "NSE_EQ|INE002A01018", "last_price": 5,
                                               "ohlc": {"close": 4}, "volume": 1}}}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(list(params))
        assert headers["Authorization"] == "Bearer t"
        return _Resp(400) if "instrument_key" in params else _Resp(200, ok)

    monkeypatch.setattr(upstox_feed.requests, "get", fake_get)
    assert upstox_feed.fetch_quotes(["RELIANCE"])["RELIANCE"]["price"] == 5
    assert calls == [["instrument_key"], ["symbol"]]


def test_newest_trade_age():
    q = {"A": {"last_trade_ms": 1_000_000}, "B": {"last_trade_ms": 4_000_000}, "C": {}}
    assert upstox_feed.newest_trade_age_seconds(q, now=4_600) == 600
    assert upstox_feed.newest_trade_age_seconds({"C": {}}) is None


# --- background job with a fake database ---------------------------------------

class _Coll:
    def __init__(self):
        self.docs = {}

    def find(self, query=None, projection=None):
        query = query or {}
        rows = [d for d in self.docs.values() if all(d.get(k) == v for k, v in query.items())]

        async def gen():
            for d in rows:
                yield d
        return gen()

    async def find_one(self, query):
        return self.docs.get(query.get("_id"))

    async def update_one(self, query, update, upsert=False):
        doc = self.docs.setdefault(query["_id"], {"_id": query["_id"]})
        doc.update(update.get("$set", {}))

    async def find_one_and_update(self, query, update, return_document=None):
        doc = self.docs.setdefault(query["_id"], {"_id": query["_id"]})  # lease: always granted here
        doc.update(update.get("$set", {}))
        return doc

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = dict(doc)


@pytest.fixture
def fake_db(monkeypatch):
    db = SimpleNamespace(scan_results=_Coll(), scan_snapshots=_Coll(), scan_meta=_Coll())
    monkeypatch.setattr(scan_jobs, "db", db)
    monkeypatch.setattr(scan_routes, "db", db)
    monkeypatch.setenv("SCAN_UNIVERSE_IN", "RELIANCE TCS")
    monkeypatch.setattr(scan_jobs, "PER_ASSET_PAUSE", 0)
    scan_jobs._state.update(last_daily=0.0, last_live=0.0, last_live_source=None, last_vix=0.0, last_vix_source=None)
    # No test reaches the network for India VIX unless it says so.
    monkeypatch.setattr(upstox_feed, "fetch_india_vix", lambda: {"price": 12.5, "prev_close": 12.0})
    snap = sc.daily_snapshot(bars_from([100.0] * 260))
    for s in ("RELIANCE", "TCS"):
        db.scan_snapshots.docs[f"in:{s}"] = {"_id": f"in:{s}", "group": "in", "symbol": s, "snap": snap}
    return db


NOW = ist(2026, 10, 22, 12, 0)


def test_live_refresh_uses_upstox_when_it_works(fake_db, monkeypatch):
    monkeypatch.setattr(upstox_feed, "fetch_quotes", lambda syms: {
        "RELIANCE": {"price": 103, "prev_close": 100, "volume": 9, "last_trade_ms": int(NOW.timestamp() * 1000)},
        "TCS": {"price": 98, "prev_close": 100, "volume": 9, "last_trade_ms": int(NOW.timestamp() * 1000)}})
    asyncio.run(scan_jobs.refresh_live(NOW, sc.session_fraction(NOW)))
    g = fake_db.scan_results.docs["in.live_gainers"]
    assert g["source"] == "upstox" and g["delayed"] is False
    assert [i["symbol"] for i in g["items"]] == ["RELIANCE"]
    assert [i["symbol"] for i in fake_db.scan_results.docs["in.live_losers"]["items"]] == ["TCS"]


def test_live_refresh_falls_back_to_yahoo_marked_delayed(fake_db, monkeypatch):
    def boom(syms):
        raise upstox_feed.UpstoxUnavailable("down")
    monkeypatch.setattr(upstox_feed, "fetch_quotes", boom)
    monkeypatch.setattr(scan_jobs, "fetch_quote_sync", lambda sym: {"price": 102, "previousClose": 100, "volume": 5})
    asyncio.run(scan_jobs.refresh_live(NOW, sc.session_fraction(NOW)))
    g = fake_db.scan_results.docs["in.live_gainers"]
    assert g["source"] == "yahoo" and g["delayed"] is True and len(g["items"]) == 2
    # The fallback holds itself to one pass per 15 minutes.
    calls = []
    monkeypatch.setattr(scan_jobs, "fetch_quote_sync", lambda sym: calls.append(sym) or {"price": 1})
    asyncio.run(scan_jobs.refresh_live(NOW + timedelta(minutes=5), 0.5))
    assert calls == []


def test_a_holiday_keeps_the_last_real_lists(fake_db, monkeypatch):
    old = int((NOW - timedelta(hours=20)).timestamp() * 1000)
    monkeypatch.setattr(upstox_feed, "fetch_quotes", lambda syms: {"RELIANCE": {"price": 1, "prev_close": 2, "last_trade_ms": old}})
    asyncio.run(scan_jobs.refresh_live(NOW, 0.5))
    assert "in.live_gainers" not in fake_db.scan_results.docs


def test_early_session_notes_when_volume_surge_starts(fake_db, monkeypatch):
    early = ist(2026, 10, 22, 9, 20)
    monkeypatch.setattr(upstox_feed, "fetch_quotes", lambda syms: {
        "RELIANCE": {"price": 101, "prev_close": 100, "volume": 1, "last_trade_ms": int(early.timestamp() * 1000)}})
    asyncio.run(scan_jobs.refresh_live(early, sc.session_fraction(early)))
    doc = fake_db.scan_results.docs["in.live_volume_surge"]
    assert doc["items"] == [] and "30 minutes" in doc["note"]


def test_daily_refresh_builds_every_group(fake_db, monkeypatch):
    monkeypatch.setenv("SCAN_UNIVERSE_US", "AAPL")
    monkeypatch.setenv("SCAN_UNIVERSE_CRYPTO", "BTC-USD")
    asked = []
    monkeypatch.setattr(scan_jobs, "fetch_daily_bars_sync", lambda sym: asked.append(sym) or bars_from(rising()))
    asyncio.run(scan_jobs.refresh_daily(ist(2026, 10, 22, 16, 0)))  # after the NSE close
    assert asked == ["RELIANCE.NS", "TCS.NS", "AAPL", "BTC-USD"]
    assert [i["symbol"] for i in fake_db.scan_results.docs["us.near_52w_high"]["items"]] == ["AAPL"]
    assert fake_db.scan_results.docs["in.near_52w_high"]["kind"] == "daily"
    # Chart patterns are computed in the same pass, from the same closes.
    assert "in.pat_hammer" in fake_db.scan_results.docs and "us.pat_sar_up" in fake_db.scan_results.docs


# --- API -----------------------------------------------------------------------

def test_api_lists_every_scan_with_freshness(fake_db, monkeypatch):
    fake_db.scan_results.docs["in.live_gainers"] = {"_id": "in.live_gainers", "items": [{"symbol": "RELIANCE"}],
                                                    "as_of": NOW.isoformat(), "source": "yahoo", "delayed": True}
    out = asyncio.run(scan_routes.list_scans())
    by_id = {s["id"]: s for s in out["scans"]}
    assert by_id["in.live_gainers"]["count"] == 1 and by_id["in.live_gainers"]["delayed"] is True
    assert by_id["us.rsi_oversold"]["count"] == 0 and by_id["us.rsi_oversold"]["as_of"] is None
    assert isinstance(out["india_market_open"], bool)


def test_api_scan_detail_and_unknown_ids(fake_db):
    fake_db.scan_results.docs["in.rsi_oversold"] = {"_id": "in.rsi_oversold", "items": [
        {"symbol": "TCS", "label": "RSI 27.0", "price": 3000, "change_pct": -1}], "as_of": NOW.isoformat(),
        "source": "yahoo", "delayed": False}
    out = asyncio.run(scan_routes.get_scan("in.rsi_oversold"))
    assert out["items"][0]["analyze_symbol"] == "TCS.NS" and out["title"] == "RSI below 30"
    with pytest.raises(HTTPException) as e:
        asyncio.run(scan_routes.get_scan("in.$where"))
    assert e.value.status_code == 404


# --- daily refresh timing: once after each market's close, completed sessions only ---

def test_last_close_per_market():
    fri_918 = ist(2026, 10, 23, 9, 18)                     # Friday morning, NSE open
    at, session = sc.last_close("in", fri_918)
    assert session.isoformat() == "2026-10-22" and at.astimezone(IST).hour == 15
    at, session = sc.last_close("in", ist(2026, 10, 23, 16, 0))
    assert session.isoformat() == "2026-10-23"           # after Friday's close
    _, session = sc.last_close("in", ist(2026, 10, 25, 12, 0))
    assert session.isoformat() == "2026-10-23"           # Sunday -> Friday's close
    _, session = sc.last_close("us", fri_918)             # still Thursday night in New York
    assert session.isoformat() == "2026-10-22"
    at, session = sc.last_close("crypto", fri_918)         # 00:15 UTC Friday finalised Thursday
    assert session.isoformat() == "2026-10-22" and at.astimezone(timezone.utc).hour == 0


def test_todays_unfinished_candle_is_dropped():
    fri_918 = ist(2026, 10, 23, 9, 18)
    today = int(ist(2026, 10, 23, 9, 15).timestamp())
    bars = bars_from([100.0] * 40, start=today - 39 * 86400)  # last bar is today's
    kept = sc.completed_bars("in", bars, fri_918)
    assert len(kept) == 39
    assert len(sc.completed_bars("in", bars, ist(2026, 10, 23, 16, 0))) == 40   # after the close


def test_daily_refresh_runs_once_per_close(fake_db, monkeypatch):
    done = []

    async def fake_group(group, now):
        done.append(group)
    monkeypatch.setattr(scan_jobs, "refresh_daily_group", fake_group)
    t1 = ist(2026, 10, 23, 9, 18)
    asyncio.run(scan_jobs.refresh_due_daily(t1))
    asyncio.run(scan_jobs.refresh_due_daily(t1 + timedelta(minutes=30)))
    assert sorted(done) == ["crypto", "in", "us"]          # first run, then nothing new
    asyncio.run(scan_jobs.refresh_due_daily(ist(2026, 10, 23, 15, 50)))
    assert done[3:] == ["in"]                              # only India closed since


def test_live_runs_before_the_daily_rebuild(fake_db, monkeypatch):
    order = []

    async def fake_live(now, fraction):
        order.append("live")

    async def fake_daily(now):
        order.append("daily")
    monkeypatch.setattr(scan_jobs, "refresh_live", fake_live)
    monkeypatch.setattr(scan_jobs, "refresh_due_daily", fake_daily)
    asyncio.run(scan_jobs.tick(ist(2026, 10, 23, 9, 18)))
    assert order == ["live", "daily"]


def test_daily_results_name_their_session(fake_db, monkeypatch):
    monkeypatch.setenv("SCAN_UNIVERSE_IN", "RELIANCE")
    monkeypatch.setattr(scan_jobs, "fetch_daily_bars_sync", lambda sym: bars_from(rising()))
    asyncio.run(scan_jobs.refresh_daily_group("in", ist(2026, 10, 23, 8, 30)))  # before the open
    assert fake_db.scan_results.docs["in.near_52w_high"]["session_date"] == "2026-10-22"
    out = asyncio.run(scan_routes.get_scan("in.near_52w_high"))
    assert out["session_date"] == "2026-10-22"


# --- trend scans recomputed live with today's price ------------------------------

def test_live_price_moves_rsi_and_52_week_extremes():
    snap = sc.daily_snapshot(bars_from(falling()))           # oversold at the last close
    assert snap["rsi14"] < 30
    crash = sc.intraday_trend_row(snap, snap["price"] * 0.97)
    rebound = sc.intraday_trend_row(snap, snap["price"] * 1.08)
    assert crash["rsi14"] <= snap["rsi14"] < rebound["rsi14"]  # a steady fall sits at RSI 0
    assert crash["low_52w"] == pytest.approx(snap["price"] * 0.97)   # a new low counts today
    assert crash["change_pct"] == pytest.approx(-3.0)
    res = sc.run_scans(sc.INTRADAY_TREND_SCANS, {"X": crash})
    assert [i["symbol"] for i in res["rsi_oversold"]] == ["X"]
    assert "volume_spike" not in res                           # stays on completed sessions


def test_no_history_means_no_live_trend_row():
    assert sc.intraday_trend_row(None, 100) is None
    assert sc.intraday_trend_row({"closes": []}, 100) is None


def test_live_refresh_writes_provisional_trend_lists(fake_db, monkeypatch):
    snap = sc.daily_snapshot(bars_from(falling()))
    fake_db.scan_snapshots.docs["in:RELIANCE"]["snap"] = snap
    fake_db.scan_snapshots.docs["in:TCS"]["snap"] = snap
    ts = int(NOW.timestamp() * 1000)
    monkeypatch.setattr(upstox_feed, "fetch_quotes", lambda syms: {
        "RELIANCE": {"price": snap["price"] * 0.98, "prev_close": snap["price"], "volume": 1, "last_trade_ms": ts},
        "TCS": {"price": snap["price"] * 1.10, "prev_close": snap["price"], "volume": 1, "last_trade_ms": ts}})
    asyncio.run(scan_jobs.refresh_live(NOW, 0.5))
    doc = fake_db.scan_results.docs["in.rsi_oversold"]
    assert doc["provisional"] is True and doc["source"] == "upstox" and doc["kind"] == "daily"
    assert [i["symbol"] for i in doc["items"]] == ["RELIANCE"]   # TCS bounced out of oversold today
    assert "in.volume_spike" not in fake_db.scan_results.docs
    # After the close, the daily rebuild replaces it with final values.
    monkeypatch.setenv("SCAN_UNIVERSE_IN", "RELIANCE")
    monkeypatch.setattr(scan_jobs, "fetch_daily_bars_sync", lambda sym: bars_from(falling()))
    asyncio.run(scan_jobs.refresh_daily_group("in", ist(2026, 10, 22, 16, 0)))
    final = fake_db.scan_results.docs["in.rsi_oversold"]
    assert final["provisional"] is False and final["session_date"] == "2026-10-22"


def test_a_daily_rebuild_during_the_session_leaves_the_live_trend_lists_alone(fake_db, monkeypatch):
    fake_db.scan_results.docs["in.rsi_oversold"] = {"_id": "in.rsi_oversold", "items": [{"symbol": "LIVE"}],
                                                    "provisional": True, "source": "upstox"}
    monkeypatch.setenv("SCAN_UNIVERSE_IN", "RELIANCE")
    monkeypatch.setattr(scan_jobs, "fetch_daily_bars_sync", lambda sym: bars_from(falling(), last_volume=3_000_000))
    asyncio.run(scan_jobs.refresh_daily_group("in", ist(2026, 10, 23, 9, 30)))     # NSE open
    assert fake_db.scan_results.docs["in.rsi_oversold"]["provisional"] is True      # untouched
    assert fake_db.scan_results.docs["in.volume_spike"]["session_date"] == "2026-10-22"
    asyncio.run(scan_jobs.refresh_daily_group("in", ist(2026, 10, 23, 15, 50)))    # after the close
    assert fake_db.scan_results.docs["in.rsi_oversold"]["provisional"] is False


# --- chart patterns ----------------------------------------------------------------

def _ohlc_bars(rows, start=DAY0):
    return [{"time": start + i * 86400, "open": o, "high": h, "low": l, "close": c, "volume": 1_000_000}
            for i, (o, h, l, c) in enumerate(rows)]


def test_snapshot_carries_the_patterns_and_a_hammer_is_listed():
    closes = [100 + 0.1 * i for i in range(250)] + [125 - 2 * i for i in range(9)]
    rows = [(c, c * 1.003, c * 0.997, c) for c in closes]
    last = closes[-1] - 1
    rows.append((last, last + 0.1, last - 4, last + 0.05))          # small body, long lower shadow, after a fall
    snap = sc.daily_snapshot(_ohlc_bars(rows))
    assert snap["patterns"]["candle"] == "hammer"
    res = sc.run_scans(sc.PATTERN_SCANS, {"X": snap})
    assert [i["symbol"] for i in res["pat_hammer"]] == ["X"] and res["pat_shooting_star"] == []


def test_pattern_scans_skip_rows_without_patterns():
    res = sc.run_scans(sc.PATTERN_SCANS, {"OLD": {"price": 1, "change_pct": 0}})
    assert all(v == [] for v in res.values())


def test_catalog_lists_patterns_for_every_market_and_never_advises():
    cat = [c for c in sc.scan_catalog() if c["kind"] == "pattern"]
    assert len(cat) == 3 * len(sc.PATTERN_SCANS)
    for c in cat:
        text = (c["title"] + " " + c["description"]).lower()
        assert not any(w in text for w in ("buy", "sell", "recommend", "should", "bullish", "bearish", "target")), c


def test_patterns_are_not_recomputed_live():
    # Intraday, only the trend scans move with the live price; a candle is
    # only a candle once the session has closed.
    assert not {s.id for s in sc.PATTERN_SCANS} & {s.id for s in sc.INTRADAY_TREND_SCANS}


# --- India VIX -------------------------------------------------------------------

def test_vix_comes_from_upstox_and_shows_in_the_api(fake_db):
    asyncio.run(scan_jobs.refresh_vix(NOW))
    doc = fake_db.scan_meta.docs["india_vix"]
    assert doc["value"] == 12.5 and doc["change_pct"] == pytest.approx(4.17) and doc["source"] == "upstox"
    out = asyncio.run(scan_routes.list_scans())
    assert out["india_vix"]["value"] == 12.5 and "_id" not in out["india_vix"]


def test_vix_falls_back_to_yahoo_and_throttles_itself(fake_db, monkeypatch):
    def boom():
        raise upstox_feed.UpstoxUnavailable("down")
    monkeypatch.setattr(upstox_feed, "fetch_india_vix", boom)
    asked = []
    monkeypatch.setattr(scan_jobs, "fetch_quote_sync", lambda sym: asked.append(sym) or {"price": 14.0, "previousClose": 14.0})
    asyncio.run(scan_jobs.refresh_vix(NOW))
    doc = fake_db.scan_meta.docs["india_vix"]
    assert asked == ["^INDIAVIX"] and doc["source"] == "yahoo" and doc["delayed"] is True and doc["change_pct"] == 0
    asyncio.run(scan_jobs.refresh_vix(NOW + timedelta(minutes=5)))
    assert asked == ["^INDIAVIX"]                                 # Yahoo at most every 15 minutes
    asyncio.run(scan_jobs.refresh_vix(NOW + timedelta(minutes=5), force=True))
    assert len(asked) == 2


def test_a_vix_failure_keeps_the_last_value(fake_db, monkeypatch):
    fake_db.scan_meta.docs["india_vix"] = {"_id": "india_vix", "value": 11.0}

    def boom(*a):
        raise RuntimeError("network")
    monkeypatch.setattr(upstox_feed, "fetch_india_vix", lambda: (_ for _ in ()).throw(upstox_feed.UpstoxUnavailable("x")))
    monkeypatch.setattr(scan_jobs, "fetch_quote_sync", boom)
    asyncio.run(scan_jobs.refresh_vix(NOW))
    assert fake_db.scan_meta.docs["india_vix"]["value"] == 11.0


def test_the_india_close_takes_the_closing_vix(fake_db, monkeypatch):
    monkeypatch.setenv("SCAN_UNIVERSE_IN", "RELIANCE")
    monkeypatch.setattr(scan_jobs, "fetch_daily_bars_sync", lambda sym: bars_from(rising()))
    asyncio.run(scan_jobs.refresh_daily_group("in", ist(2026, 10, 23, 15, 50)))
    assert fake_db.scan_meta.docs["india_vix"]["as_of"] == ist(2026, 10, 23, 15, 50).isoformat()


def test_vix_index_quote_is_parsed_by_instrument_token():
    payload = {"status": "success", "data": {"NSE_INDEX:India VIX": {
        "instrument_token": upstox_feed.INDIA_VIX_KEY, "last_price": 13.2, "net_change": -0.4}}}
    q = upstox_feed.parse_quotes(payload, {upstox_feed.INDIA_VIX_KEY: "INDIA VIX"})["INDIA VIX"]
    assert q["price"] == 13.2 and q["prev_close"] == pytest.approx(13.6)
