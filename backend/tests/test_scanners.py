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
    scan_jobs._state.update(last_daily=0.0, last_live=0.0, last_live_source=None)
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
    asyncio.run(scan_jobs.refresh_daily(NOW))
    assert asked == ["RELIANCE.NS", "TCS.NS", "AAPL", "BTC-USD"]
    assert [i["symbol"] for i in fake_db.scan_results.docs["us.near_52w_high"]["items"]] == ["AAPL"]
    assert fake_db.scan_results.docs["in.near_52w_high"]["kind"] == "daily"


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
