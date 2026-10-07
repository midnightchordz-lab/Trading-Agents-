"""Computed technicals (indicators.py): checked against published reference
values and an independent pandas implementation, so the numbers the agents
quote are the standard ones."""
import math
import os
import random
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import indicators as ind  # noqa: E402

# StockCharts' worked Wilder RSI(14) example (ChartSchool, "RSI"): the first
# value after 15 closes is 70.53, then 66.32, 66.55, 69.41, 66.36, 57.97.
STOCKCHARTS = [44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826,
               45.8931, 46.0328, 45.6140, 46.2820, 46.2820, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439]
EXPECTED_RSI = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97]


def test_rsi_matches_the_published_wilder_example():
    for i, want in enumerate(EXPECTED_RSI):
        got = ind.rsi(STOCKCHARTS[: 15 + i])
        assert got == pytest.approx(want, abs=0.005), (i, got, want)


def test_rsi_edges():
    assert ind.rsi([1, 2, 3]) is None
    assert ind.rsi([float(i) for i in range(1, 30)]) == 100.0   # only gains
    assert ind.rsi([5.0] * 30) == 50.0                          # flat


def _walk(n=260, seed=7, start=100.0):
    rng = random.Random(seed)
    out = [start]
    for _ in range(n - 1):
        out.append(out[-1] * math.exp(rng.gauss(0.0004, 0.015)))
    return out


def test_macd_matches_pandas_ewm_after_warmup():
    closes = _walk()
    got = ind.macd(closes)
    s = pd.Series(closes)
    line = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    sig = line.ewm(span=9, adjust=False).mean()
    # Seeding differs (SMA vs first value) but washes out over 260 bars.
    assert got["macd"] == pytest.approx(line.iloc[-1], rel=1e-3, abs=1e-3)
    assert got["signal"] == pytest.approx(sig.iloc[-1], rel=1e-3, abs=1e-3)
    assert got["histogram"] == pytest.approx(got["macd"] - got["signal"])


def test_macd_reports_fresh_crossovers_both_ways():
    wave = [100 + 10 * math.sin(i / 8) for i in range(80)]
    crosses = {ind.macd(wave[:k])["recent_cross"] for k in range(40, 80)}
    assert {"bullish", "bearish"} <= crosses


def test_atr_of_a_constant_range_is_that_range():
    closes = [100.0] * 40
    highs = [101.0] * 40
    lows = [99.0] * 40
    assert ind.atr(highs, lows, closes) == pytest.approx(2.0)


def test_volatility_matches_pandas_log_return_std():
    closes = _walk(seed=11)
    s = pd.Series(closes)
    want = s.apply(math.log).diff().tail(30).std() * math.sqrt(252)
    assert ind.annualized_volatility(closes, 30) == pytest.approx(want, rel=1e-9)
    assert ind.annualized_volatility([100.0] * 40, 30) == 0.0


def test_drawdowns():
    max_dd, cur = ind.drawdowns([100, 120, 90, 110, 60, 80])
    assert max_dd == pytest.approx(60 / 120 - 1)   # -50%
    assert cur == pytest.approx(80 / 120 - 1)


def test_historical_var_is_the_5th_percentile_daily_return():
    rets = [-0.05, -0.04, -0.03] + [0.001] * 97     # 100 daily returns
    closes = [100.0]
    for r in rets:
        closes.append(closes[-1] * (1 + r))
    # 5% of 100 = index 5 in the sorted list -> the first 0.001 after 3 losses
    assert ind.historical_var(closes) == pytest.approx(0.001, abs=1e-9)
    assert ind.historical_var(closes[:30]) is None


def test_bollinger_pct_b_bounds():
    flat_then_spike = [100.0] * 19 + [130.0]
    assert ind.bollinger_pct_b(flat_then_spike) > 0.9
    assert ind.bollinger_pct_b([100.0] * 20) == 0.5


def _bars(closes, volume=1_000_000):
    return [{"time": i, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": volume}
            for i, c in enumerate(closes)]


def test_compute_and_summarize_a_full_year():
    t = ind.compute(_bars(_walk()))
    assert t["bars"] == 260 and t["sma200"] is not None and t["return_1y"] is not None
    text = ind.summarize(t)
    for needle in ("RSI(14):", "MACD(12,26,9):", "Moving averages:", "Bollinger %B", "ATR(14):",
                   "Volatility (annualized)", "Drawdown:", "95% historical VaR", "Support/resistance"):
        assert needle in text, needle


def test_short_history_gives_nothing_rather_than_guesses():
    assert ind.compute(_bars(_walk(n=20))) is None
    assert ind.summarize(None) is None


def test_missing_volume_is_omitted_not_zeroed():
    t = ind.compute(_bars(_walk(), volume=0))
    assert t["volume_vs_20d_avg"] is None
    assert "Volume:" not in ind.summarize(t)


def test_crypto_annualizes_over_365_days():
    closes = _walk(seed=3)
    eq = ind.compute(_bars(closes))["volatility_30d"]
    cr = ind.compute(_bars(closes), crypto=True)["volatility_30d"]
    assert cr == pytest.approx(eq * math.sqrt(365 / 252))
