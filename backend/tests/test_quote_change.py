"""The quote's 1-day change is measured against the previous session's close,
not chartPreviousClose (which, for the 1-month window the quote fetches, is
the close before the whole month — that showed a 1-month move as "1-day")."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import market_data as md  # noqa: E402

DAY = 86400
NY = -4 * 3600       # EDT
IST = 5 * 3600 + 1800


def _chart(closes, *, first_open_utc, offset, market_time, extra_meta=None, stamps=True):
    ts = [first_open_utc + i * DAY for i in range(len(closes))]
    meta = {"symbol": "X", "regularMarketPrice": [c for c in closes if c is not None][-1],
            "gmtoffset": offset, "regularMarketTime": market_time,
            # The month-old close that used to be shown as "previous close".
            "chartPreviousClose": 50.0, "currency": "USD"}
    meta.update(extra_meta or {})
    result = {"meta": meta, "indicators": {"quote": [{"close": closes}]}}
    if stamps:
        result["timestamp"] = ts
    return {"chart": {"result": [result]}}


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _quote(monkeypatch, data):
    monkeypatch.setattr(md, "_yf_get", lambda url, params: _Resp(data))
    return md.fetch_quote_sync("X")


# Bars open 09:30 New York (13:30 UTC); day 0 = 2026-09-01.
OPEN0 = 1788269400  # 2026-09-01 13:30 UTC


def test_during_the_session_compares_with_yesterdays_close(monkeypatch):
    closes = [100.0, 101.0, 102.0, 105.0]          # last bar = today, still live
    q = _quote(monkeypatch, _chart(closes, first_open_utc=OPEN0, offset=NY, market_time=OPEN0 + 3 * DAY + 3 * 3600))
    assert q["previousClose"] == 102.0
    assert q["change"] == 3.0
    assert q["changePercent"] == pytest.approx(2.94, abs=0.005)


def test_todays_bar_with_a_null_close_does_not_shift_back_a_day(monkeypatch):
    # Yahoo often has today's bar with close=null early on; price is in meta.
    data = _chart([100.0, 101.0, 102.0, None], first_open_utc=OPEN0, offset=NY,
                  market_time=OPEN0 + 3 * DAY + 600, extra_meta={"regularMarketPrice": 104.0})
    q = _quote(monkeypatch, data)
    assert q["previousClose"] == 102.0          # not 101 (what closes[-2] gave)
    assert q["change"] == 2.0


def test_pre_market_shows_the_last_sessions_move(monkeypatch):
    # Before today's open: latest price is yesterday's close, no bar for today yet.
    closes = [100.0, 101.0, 103.0]
    q = _quote(monkeypatch, _chart(closes, first_open_utc=OPEN0, offset=NY, market_time=OPEN0 + 2 * DAY + 6.5 * 3600))
    assert q["previousClose"] == 101.0
    assert q["change"] == 2.0


def test_nse_session_uses_ist_dates(monkeypatch):
    open_ist = OPEN0 - 13 * 3600 - 1800 + 3 * 3600 + 45 * 60   # 09:15 IST = 03:45 UTC
    closes = [2400.0, 2410.0, 2380.0]
    q = _quote(monkeypatch, _chart(closes, first_open_utc=open_ist, offset=IST, market_time=open_ist + 2 * DAY + 6 * 3600))
    assert q["previousClose"] == 2410.0
    assert q["changePercent"] == pytest.approx(-1.24, abs=0.005)


def test_crypto_daily_bars_at_utc_midnight(monkeypatch):
    midnight = OPEN0 - 13 * 3600 - 1800
    closes = [60000.0, 61000.0, 59780.0]
    q = _quote(monkeypatch, _chart(closes, first_open_utc=midnight, offset=0, market_time=midnight + 2 * DAY + 15 * 3600))
    assert q["previousClose"] == 61000.0
    assert q["changePercent"] == pytest.approx(-2.0)


def test_never_uses_the_month_old_chart_previous_close(monkeypatch):
    q = _quote(monkeypatch, _chart([100.0, 101.0], first_open_utc=OPEN0, offset=NY, market_time=OPEN0 + DAY + 3600))
    assert q["previousClose"] != 50.0 and q["changePercent"] < 5


def test_without_timestamps_backs_out_prev_from_yahoos_percent(monkeypatch):
    data = _chart([100.0, 105.0], first_open_utc=OPEN0, offset=NY, market_time=0, stamps=False,
                  extra_meta={"regularMarketChangePercent": 5.0})
    q = _quote(monkeypatch, data)
    assert q["previousClose"] == 100.0           # 105 / 1.05, not 105 - 5.25
    assert q["change"] == 5.0
    assert q["changePercent"] == 5.0


def test_no_data_gives_none_not_a_made_up_change(monkeypatch):
    data = _chart([105.0], first_open_utc=OPEN0, offset=NY, market_time=OPEN0 + 3600)
    q = _quote(monkeypatch, data)
    assert q["previousClose"] is None and q["change"] is None and q["changePercent"] is None
