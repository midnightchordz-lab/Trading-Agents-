"""Stock scanners: ready-made lists of assets that match a factual condition.

Two kinds, because they need data of different freshness:

- DAILY scans (52-week high/low, RSI, MACD crossovers, unusual volume) are
  computed from daily closing bars. They describe the trend, so a refresh after
  each close is correct for them all next day.
- LIVE scans (India only: today's gainers/losers, volume surge, new 52-week
  high) use live quotes during the NSE session — Upstox when available, Yahoo
  (15-minute delayed, and labelled so) when it isn't.

Every scan is a filter on numbers, never a recommendation: titles say what is
true ("RSI below 30"), not what to do. Pure functions over plain dicts — no
network, no database — so all of it is unit-testable; scan_jobs.py feeds it.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import indicators as ind

IST = timezone(timedelta(hours=5, minutes=30))
NSE_OPEN = (9, 15)
NSE_CLOSE = (15, 30)

# When each market's daily candle is final, in its own time zone, plus a
# margin for the data to settle: NSE closes 15:30 IST, NYSE/Nasdaq 16:00 New
# York time, and a crypto day ends at 00:00 UTC. Daily scans refresh once
# after each of these, from completed sessions only.
MARKET_CLOSE = {
    "in": (IST, 15, 45, True),                              # weekdays only
    "us": (ZoneInfo("America/New_York"), 16, 30, True),
    "crypto": (timezone.utc, 0, 15, False),                 # every day
}


def last_close(group: str, now: datetime) -> tuple[datetime, date]:
    """The most recent moment `group`'s daily data became final, and the
    trading day it finalised. For crypto the 00:15 UTC close finalises the
    PREVIOUS day's candle. Exchange holidays aren't modelled: a refresh then
    simply finds the same last session again, which is harmless."""
    tz, hh, mm, weekdays_only = MARKET_CLOSE[group]
    local = now.astimezone(tz)
    day = local.date()
    for _ in range(8):
        candidate = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
        if candidate <= local and (not weekdays_only or day.weekday() < 5):
            session = day - timedelta(days=1) if group == "crypto" else day
            return candidate, session
        day -= timedelta(days=1)
    raise RuntimeError("no market close found in the last week")


def completed_bars(group: str, bars: list[dict], now: datetime) -> list[dict]:
    """Drop any bar for a session that hasn't closed yet. During market hours
    Yahoo's daily series ends with today's still-forming candle; treating it as
    a finished day made 'unusual volume' compare a few minutes of trading with
    a full day's average."""
    tz = MARKET_CLOSE[group][0]
    _, session = last_close(group, now)
    return [b for b in bars
            if b.get("time") is None or datetime.fromtimestamp(int(b["time"]), tz).date() <= session]

# Large, liquid names to start. Index membership changes twice a year, so this
# is a starting universe rather than a claim to be "the Nifty 50"; each list
# can be replaced from the environment without a release. A symbol that fails
# to resolve is skipped and logged, never fatal.
DEFAULT_IN = (
    "ADANIENT ADANIPORTS APOLLOHOSP ASIANPAINT AXISBANK BAJAJ-AUTO BAJFINANCE BAJAJFINSV BEL BHARTIARTL "
    "CIPLA COALINDIA DRREDDY EICHERMOT ETERNAL GRASIM HCLTECH HDFCBANK HDFCLIFE HEROMOTOCO "
    "HINDALCO HINDUNILVR ICICIBANK INDUSINDBK INFY ITC JIOFIN JSWSTEEL KOTAKBANK LT "
    "M&M MARUTI NESTLEIND NTPC ONGC POWERGRID RELIANCE SBILIFE SBIN SHRIRAMFIN "
    "SUNPHARMA TATACONSUM TATASTEEL TCS TECHM TITAN TRENT ULTRACEMCO WIPRO"
)
DEFAULT_US = (
    "AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO JPM V MA UNH XOM LLY JNJ "
    "WMT PG HD COST NFLX AMD ORCL CRM BAC KO PEP DIS INTC ADBE CSCO"
)
DEFAULT_CRYPTO = "BTC-USD ETH-USD SOL-USD BNB-USD XRP-USD"


def _universe(env: str, default: str) -> list[str]:
    raw = os.environ.get(env, "") or default
    seen, out = set(), []
    for s in raw.replace(",", " ").split():
        s = s.strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def universe() -> dict[str, list[str]]:
    """Market group -> NSE trading symbols (India) or Yahoo symbols (US, crypto)."""
    return {
        "in": _universe("SCAN_UNIVERSE_IN", DEFAULT_IN),
        "us": _universe("SCAN_UNIVERSE_US", DEFAULT_US),
        "crypto": _universe("SCAN_UNIVERSE_CRYPTO", DEFAULT_CRYPTO),
    }


def yahoo_symbol(group: str, symbol: str) -> str:
    """NSE trading symbol -> Yahoo ticker (RELIANCE -> RELIANCE.NS)."""
    return f"{symbol}.NS" if group == "in" else symbol


# ---------------------------------------------------------------------------
# Daily snapshot
# ---------------------------------------------------------------------------
def _date_of(ts: int, tz) -> str:
    return datetime.fromtimestamp(int(ts), tz).date().isoformat()


def daily_snapshot(bars: list[dict], tz=IST) -> Optional[dict]:
    """Everything the daily and live scans need from one asset's daily bars
    (oldest first). None when there isn't enough history to say anything.

    The `prior_*` fields exclude the last bar. A live scan compares today's
    price with the year BEFORE today, so it uses them when the last bar is
    today's own (still-forming) session, and the full-window fields otherwise.
    """
    bars = [b for b in bars if b.get("close")]
    if len(bars) < 30:
        return None
    closes = [float(b["close"]) for b in bars]
    highs = [float(b.get("high") or b["close"]) for b in bars]
    lows = [float(b.get("low") or b["close"]) for b in bars]
    vols = [float(b.get("volume") or 0) for b in bars]
    year = 252
    m = ind.macd(closes)
    avg20 = ind._sma(vols[:-1], 20)
    return {
        "price": closes[-1],
        "prev_close": closes[-2],
        "change_pct": (closes[-1] / closes[-2] - 1) * 100 if closes[-2] else None,
        "rsi14": ind.rsi(closes),
        "macd_cross": (m or {}).get("recent_cross"),
        "high_52w": max(highs[-year:]),
        "low_52w": min(lows[-year:]),
        "prior_high_52w": max(highs[-year - 1:-1]),
        "volume": vols[-1],
        "avg_volume_20": avg20,
        "avg_volume_20_incl": ind._sma(vols, 20),
        "volume_ratio": (vols[-1] / avg20) if avg20 and vols[-1] else None,
        "last_bar_date": _date_of(bars[-1]["time"], tz) if bars[-1].get("time") is not None else None,
        # Completed closes, so the trend scans can be recomputed during the
        # session with today's live price as a provisional close.
        "closes": [round(c, 4) for c in closes[-300:]],
    }


# ---------------------------------------------------------------------------
# Scan definitions
# ---------------------------------------------------------------------------
class Scan:
    def __init__(self, sid: str, title: str, description: str, kind: str,
                 match: Callable[[dict], bool], key: Callable[[dict], float],
                 label: Callable[[dict], str], descending: bool = True, limit: int = 25):
        self.id, self.title, self.description, self.kind = sid, title, description, kind
        self.match, self.key, self.label = match, key, label
        self.descending, self.limit = descending, limit


def _pct_from(price, ref):
    return (price / ref - 1) * 100 if price and ref else None


DAILY_SCANS = [
    Scan("near_52w_high", "Near 52-week high", "Closed within 2% of its highest price in a year.", "daily",
         lambda s: s["high_52w"] and s["price"] >= 0.98 * s["high_52w"],
         lambda s: _pct_from(s["price"], s["high_52w"]),
         lambda s: f"{_pct_from(s['price'], s['high_52w']):+.1f}% from high"),
    Scan("near_52w_low", "Near 52-week low", "Closed within 2% of its lowest price in a year.", "daily",
         lambda s: s["low_52w"] and s["price"] <= 1.02 * s["low_52w"],
         lambda s: _pct_from(s["price"], s["low_52w"]),
         lambda s: f"{_pct_from(s['price'], s['low_52w']):+.1f}% from low", descending=False),
    Scan("rsi_oversold", "RSI below 30", "Fell sharply in a short time: 14-day RSI under 30.", "daily",
         lambda s: s["rsi14"] is not None and s["rsi14"] < 30,
         lambda s: s["rsi14"], lambda s: f"RSI {s['rsi14']:.1f}", descending=False),
    Scan("rsi_overbought", "RSI above 70", "Rose sharply in a short time: 14-day RSI over 70.", "daily",
         lambda s: s["rsi14"] is not None and s["rsi14"] > 70,
         lambda s: s["rsi14"], lambda s: f"RSI {s['rsi14']:.1f}"),
    Scan("macd_bullish", "Momentum turned up", "MACD crossed above its signal line in the last 3 sessions.", "daily",
         lambda s: s["macd_cross"] == "bullish",
         lambda s: s["change_pct"] or 0, lambda s: "MACD crossed up"),
    Scan("macd_bearish", "Momentum turned down", "MACD crossed below its signal line in the last 3 sessions.", "daily",
         lambda s: s["macd_cross"] == "bearish",
         lambda s: s["change_pct"] or 0, lambda s: "MACD crossed down", descending=False),
    Scan("volume_spike", "Unusual volume", "Last session traded at 2× or more its 20-day average volume.", "daily",
         lambda s: s["volume_ratio"] is not None and s["volume_ratio"] >= 2,
         lambda s: s["volume_ratio"], lambda s: f"{s['volume_ratio']:.1f}× avg volume"),
]


def _item(symbol: str, snap: dict, label: str) -> dict:
    return {
        "symbol": symbol,
        "label": label,
        "price": round(snap["price"], 2) if snap.get("price") is not None else None,
        "change_pct": round(snap["change_pct"], 2) if snap.get("change_pct") is not None else None,
    }


def run_scans(scans: list[Scan], rows: dict[str, dict]) -> dict[str, list[dict]]:
    """Apply each scan to {symbol: row}; returns {scan_id: items}, best first."""
    out = {}
    for sc in scans:
        hits = []
        for sym, row in rows.items():
            try:
                if row and sc.match(row):
                    hits.append((sc.key(row), sym, row))
            except (TypeError, ZeroDivisionError):
                continue  # a missing field just means "doesn't match"
        hits.sort(key=lambda h: (h[0] is None, -(h[0] or 0) if sc.descending else (h[0] or 0)))
        out[sc.id] = [_item(sym, row, sc.label(row)) for _, sym, row in hits[: sc.limit]]
    return out


# ---------------------------------------------------------------------------
# Live (NSE session) scans
# ---------------------------------------------------------------------------
def session_fraction(now: datetime) -> Optional[float]:
    """How far through today's NSE session `now` is (0..1), or None when the
    market is closed (weekend or outside 9:15-15:30 IST). Exchange holidays are
    caught separately, by the quotes' last-trade time going stale."""
    t = now.astimezone(IST)
    if t.weekday() >= 5:
        return None
    start = t.replace(hour=NSE_OPEN[0], minute=NSE_OPEN[1], second=0, microsecond=0)
    end = t.replace(hour=NSE_CLOSE[0], minute=NSE_CLOSE[1], second=0, microsecond=0)
    if not (start <= t <= end):
        return None
    return (t - start) / (end - start)


def live_row(quote: dict, snap: Optional[dict], today: str) -> Optional[dict]:
    """Combine a live quote {price, prev_close, volume} with the daily snapshot
    into one row the live scans can test."""
    price, prev = quote.get("price"), quote.get("prev_close")
    if not price:
        return None
    today_bar = bool(snap) and snap.get("last_bar_date") == today
    high_before_today = None
    avg_vol = None
    if snap:
        high_before_today = snap["prior_high_52w"] if today_bar else snap["high_52w"]
        avg_vol = snap["avg_volume_20"] if today_bar else snap["avg_volume_20_incl"]
    return {
        "price": float(price),
        "change_pct": (float(price) / float(prev) - 1) * 100 if prev else None,
        "volume": quote.get("volume"),
        "avg_volume_20": avg_vol,
        "high_before_today": high_before_today,
    }


# The daily scans that still mean something mid-session when today's live
# price stands in for today's close: RSI, MACD momentum, 52-week high/low.
# Unusual volume is left on completed sessions; live volume has its own scan.
INTRADAY_TREND_SCANS = [sc for sc in DAILY_SCANS if sc.id != "volume_spike"]


def intraday_trend_row(snap: Optional[dict], price: Optional[float]) -> Optional[dict]:
    """A daily-scan row recomputed with the live price appended as today's
    provisional close. The values move during the day and settle at the
    close, when the daily refresh replaces them with the final ones."""
    if not snap or not price or not snap.get("closes"):
        return None
    price = float(price)
    closes = list(snap["closes"]) + [price]
    m = ind.macd(closes)
    prev = snap["price"]  # the last completed close = today's previous close
    return {
        "price": price,
        "change_pct": (price / prev - 1) * 100 if prev else None,
        "rsi14": ind.rsi(closes),
        "macd_cross": (m or {}).get("recent_cross"),
        "high_52w": max(snap["high_52w"], price),
        "low_52w": min(snap["low_52w"], price),
        "volume_ratio": None,
    }


def live_scans(fraction: float) -> list[Scan]:
    """The live scans, given how far into the session we are. Volume pace needs
    the first half hour to mean anything, so it is left out before that."""
    scans = [
        Scan("live_gainers", "Top gainers today", "Biggest rise since yesterday's close.", "live",
             lambda r: r["change_pct"] is not None and r["change_pct"] > 0,
             lambda r: r["change_pct"], lambda r: f"{r['change_pct']:+.2f}% today", limit=10),
        Scan("live_losers", "Top losers today", "Biggest fall since yesterday's close.", "live",
             lambda r: r["change_pct"] is not None and r["change_pct"] < 0,
             lambda r: r["change_pct"], lambda r: f"{r['change_pct']:+.2f}% today", descending=False, limit=10),
        Scan("live_52w_high", "New 52-week high today", "Trading above its highest price of the past year.", "live",
             lambda r: r["high_before_today"] and r["price"] > r["high_before_today"],
             lambda r: _pct_from(r["price"], r["high_before_today"]),
             lambda r: f"{_pct_from(r['price'], r['high_before_today']):+.1f}% above old high"),
    ]
    if fraction >= 0.08:  # ~30 minutes into a 6h15m session
        scans.append(
            Scan("live_volume_surge", "Volume surge today",
                 "Trading at 2× or more its normal volume for this time of day.", "live",
                 lambda r: _pace(r, fraction) is not None and _pace(r, fraction) >= 2,
                 lambda r: _pace(r, fraction), lambda r: f"{_pace(r, fraction):.1f}× normal pace"))
    return scans


def _pace(row: dict, fraction: float) -> Optional[float]:
    vol, avg = row.get("volume"), row.get("avg_volume_20")
    if not vol or not avg or fraction <= 0:
        return None
    return float(vol) / (float(avg) * fraction)


def scan_catalog() -> list[dict]:
    """Static descriptions of every scan, for the list screen."""
    groups = {"in": "India", "us": "US", "crypto": "Crypto"}
    cat = []
    for sc in live_scans(1.0):
        cat.append({"id": f"in.{sc.id}", "group": "in", "market": groups["in"], "kind": "live",
                    "title": sc.title, "description": sc.description})
    for g, name in groups.items():
        for sc in DAILY_SCANS:
            cat.append({"id": f"{g}.{sc.id}", "group": g, "market": name, "kind": "daily",
                        "title": sc.title, "description": sc.description})
    return cat
