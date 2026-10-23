"""Live NSE quotes from Upstox, for the live scans only.

The ONLY module that talks to Upstox. It uses the account's read-only
Analytics Token (UPSTOX_ANALYTICS_TOKEN, a deployment secret, valid one year;
it cannot place or change orders). Users never sign in to Upstox and never see
the token: every call is made here, server-side.

Anything going wrong — no token, an expired one, Upstox down, a changed
response — raises UpstoxUnavailable, and the caller falls back to Yahoo's
delayed quotes. Nothing else in the app depends on this file.
"""
from __future__ import annotations

import gzip
import json
import os
import time
from typing import Optional

import requests

from core import logger

QUOTES_URL = "https://api.upstox.com/v2/market-quote/quotes"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
MAX_PER_REQUEST = 500          # Upstox: full quotes for up to 500 instruments per call
INSTRUMENTS_TTL = 24 * 3600    # the instrument list changes rarely; refetch daily

_instruments: dict = {"keys": {}, "fetched": 0.0}


class UpstoxUnavailable(Exception):
    pass


def token() -> str:
    return (os.environ.get("UPSTOX_ANALYTICS_TOKEN", "") or "").strip()


def configured() -> bool:
    return bool(token())


def parse_instruments(rows: list) -> dict[str, str]:
    """NSE trading symbol -> Upstox instrument key ("NSE_EQ|<ISIN>"), equities only."""
    keys = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if r.get("segment") != "NSE_EQ" or (r.get("instrument_type") or "EQ") != "EQ":
            continue
        sym = (r.get("trading_symbol") or r.get("tradingsymbol") or "").upper()
        key = r.get("instrument_key")
        if sym and key:
            keys[sym] = key
    return keys


def instrument_keys(symbols: list[str]) -> dict[str, str]:
    """{symbol: instrument_key} for the symbols Upstox knows; unknown ones are
    left out (and logged once per refresh)."""
    if not _instruments["keys"] or time.time() - _instruments["fetched"] > INSTRUMENTS_TTL:
        try:
            r = requests.get(INSTRUMENTS_URL, timeout=30)
            r.raise_for_status()
            rows = json.loads(gzip.decompress(r.content))
            parsed = parse_instruments(rows)
            if not parsed:
                raise ValueError("instrument list parsed empty")
            _instruments.update(keys=parsed, fetched=time.time())
        except Exception as e:
            if not _instruments["keys"]:
                raise UpstoxUnavailable(f"instrument list unavailable: {e}") from e
            logger.warning(f"upstox instrument refresh failed, keeping the old list: {e}")
    found = {s: _instruments["keys"][s] for s in symbols if s in _instruments["keys"]}
    missing = [s for s in symbols if s not in found]
    if missing:
        logger.warning(f"upstox: no NSE instrument for {', '.join(missing)} — skipped")
    return found


def parse_quotes(payload: dict, key_to_symbol: dict[str, str]) -> dict[str, dict]:
    """Upstox full-quote response -> {symbol: {price, prev_close, volume, last_trade_ms}}.

    Matched back by `instrument_token` (the key we asked for), not by the
    response's dict keys, whose format ("NSE_EQ:RELIANCE") is not the request's.

    The previous close is last_price - net_change. NOT ohlc.close: the SDK
    documents it as the previous session's close, but the live API returns
    today's price there (verified 22 Oct 2026: close == last_price on every
    symbol), which would make every change 0% and empty the gainers/losers.
    ohlc.close is used only when net_change is missing, and is then ignored if
    it simply equals the last price.
    """
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise UpstoxUnavailable(f"unexpected quote response: {str(payload)[:200]}")
    out = {}
    for q in (payload.get("data") or {}).values():
        if not isinstance(q, dict):
            continue
        sym = key_to_symbol.get(q.get("instrument_token") or "")
        price = q.get("last_price")
        if not sym or not price:
            continue
        prev = None
        if q.get("net_change") is not None:
            prev = float(price) - float(q["net_change"])
        else:
            close = (q.get("ohlc") or {}).get("close")
            if close and float(close) != float(price):
                prev = float(close)
        ltt = q.get("last_trade_time")
        out[sym] = {
            "price": float(price),
            "prev_close": float(prev) if prev else None,
            "volume": q.get("volume"),
            "last_trade_ms": int(ltt) if ltt not in (None, "") and str(ltt).isdigit() else None,
        }
    return out


def fetch_quotes(symbols: list[str]) -> dict[str, dict]:
    """Live quotes for NSE symbols. Raises UpstoxUnavailable on any failure."""
    tok = token()
    if not tok:
        raise UpstoxUnavailable("UPSTOX_ANALYTICS_TOKEN is not set")
    keys = instrument_keys(symbols)
    if not keys:
        raise UpstoxUnavailable("none of the symbols resolved to an Upstox instrument")
    key_to_symbol = {v: k for k, v in keys.items()}
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    out: dict[str, dict] = {}
    items = list(keys.values())
    for i in range(0, len(items), MAX_PER_REQUEST):
        batch = ",".join(items[i:i + MAX_PER_REQUEST])
        try:
            r = requests.get(QUOTES_URL, params={"instrument_key": batch}, headers=headers, timeout=15)
            if r.status_code == 400:
                # Older API revisions named the parameter `symbol` (the
                # official SDK still sends that); try it once before giving up.
                r = requests.get(QUOTES_URL, params={"symbol": batch}, headers=headers, timeout=15)
            if r.status_code in (401, 403):
                raise UpstoxUnavailable(f"token rejected ({r.status_code}) — it may have expired or been revoked")
            r.raise_for_status()
            out.update(parse_quotes(r.json(), key_to_symbol))
        except UpstoxUnavailable:
            raise
        except Exception as e:
            raise UpstoxUnavailable(f"quote request failed: {e}") from e
    if not out:
        raise UpstoxUnavailable("quote response had no usable prices")
    return out


def newest_trade_age_seconds(quotes: dict[str, dict], now: Optional[float] = None) -> Optional[float]:
    """Seconds since the most recent trade across the quotes — a holiday (or a
    halted feed) shows up as this growing large during 'market hours'."""
    stamps = [q["last_trade_ms"] for q in quotes.values() if q.get("last_trade_ms")]
    if not stamps:
        return None
    return (now if now is not None else time.time()) - max(stamps) / 1000
