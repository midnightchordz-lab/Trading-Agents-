# COMPUTED_TECHNICALS.md

Give the analysis agents **real** technical and risk numbers. Today the Technical Analyst is asked for MACD/RSI/support/volume, and the Risk Manager for volatility/downside, but **neither receives those numbers**, so every analysis contains invented indicator readings.

> **Source of truth:** branch **`feature/computed-indicators`**, commit **`adcf540`**, in `midnightchordz-lab/Trading-Agents-`. It is built on `fix/phone-and-inr` @ `d0f16aa`; apply that one first (PHONE_COUNTRY_INR_FIX.md).
> **Preferred:** `git fetch origin feature/computed-indicators && git merge origin/feature/computed-indicators`, then go to §4.
> **Only if you can't merge:** apply §1–§3 exactly.

## Ground rules

- **Backend only.** No app changes and no new packages; it's pure Python.
- **Change only what is listed.** No force-push. Never print secrets in chat, commit messages or files.
- **Do not "improve" the formulas** (Wilder RSI/ATR, 12/26/9 MACD, 20/2 Bollinger, log-return volatility, historical VaR). The tests pin them to published reference values.
- **Keep the prompt rule.** The technical, trader and risk agents must quote indicator values **only** from the COMPUTED TECHNICALS block and say "unavailable" otherwise. Don't loosen it.

---

## 1. New files (exact content)

### `backend/indicators.py`
```python
"""Technical and risk numbers computed from daily price bars.

Why this exists: the Technical Analyst was asked to read "MACD/RSI, support/
resistance and volume" and the Risk Manager to "stress-test volatility and
downside", but neither was ever given those numbers — so the desk wrote
plausible-sounding indicator readings that were invented. Everything here is
deterministic arithmetic on the daily OHLCV bars, so the agents quote real
values and the stop-loss can be sized from measured volatility.

Pure functions over plain lists: no network, no pandas, unit-testable. Standard
definitions throughout (Wilder RSI/ATR, 12/26/9 MACD, 20/2 Bollinger,
close-to-close log-return volatility, peak-to-trough drawdown, historical VaR).
"""
from __future__ import annotations

import math
from typing import Optional

MIN_BARS = 30  # below this nothing is meaningful enough to show


def _sma(values: list[float], n: int) -> Optional[float]:
    return sum(values[-n:]) / n if len(values) >= n else None


def _ema_series(values: list[float], n: int) -> list[float]:
    """EMA seeded with the SMA of the first n values; one output per input from
    index n-1 on."""
    if len(values) < n:
        return []
    k = 2 / (n + 1)
    out = [sum(values[:n]) / n]
    for v in values[n:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes: list[float], n: int = 14) -> Optional[float]:
    """Wilder's RSI."""
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for a, b in zip(closes[:-1], closes[1:]):
        ch = b - a
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        avg_g = (avg_g * (n - 1) + g) / n
        avg_l = (avg_l * (n - 1) + l) / n
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
    """MACD line, signal line, histogram, and whether a crossover happened in
    the last 3 sessions."""
    if len(closes) < slow + signal:
        return None
    ema_fast = _ema_series(closes, fast)
    ema_slow = _ema_series(closes, slow)
    # Align: ema_fast starts at index fast-1, ema_slow at slow-1.
    offset = slow - fast
    line = [f - s for f, s in zip(ema_fast[offset:], ema_slow)]
    sig = _ema_series(line, signal)
    if not sig:
        return None
    line_aligned = line[signal - 1:]
    hist = [m - s for m, s in zip(line_aligned, sig)]
    cross = None
    for i in range(max(1, len(hist) - 3), len(hist)):
        if hist[i - 1] <= 0 < hist[i]:
            cross = "bullish"
        elif hist[i - 1] >= 0 > hist[i]:
            cross = "bearish"
    return {"macd": line_aligned[-1], "signal": sig[-1], "histogram": hist[-1], "recent_cross": cross}


def atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> Optional[float]:
    """Wilder's Average True Range."""
    if len(closes) < n + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    value = sum(trs[:n]) / n
    for tr in trs[n:]:
        value = (value * (n - 1) + tr) / n
    return value


def bollinger_pct_b(closes: list[float], n: int = 20, k: float = 2.0) -> Optional[float]:
    """Where the last close sits in the 20-day Bollinger band: 0 = lower band,
    1 = upper band (can go outside 0..1)."""
    if len(closes) < n:
        return None
    window = closes[-n:]
    mid = sum(window) / n
    sd = math.sqrt(sum((c - mid) ** 2 for c in window) / n)
    if sd == 0:
        return 0.5
    upper, lower = mid + k * sd, mid - k * sd
    return (closes[-1] - lower) / (upper - lower)


def _log_returns(closes: list[float]) -> list[float]:
    return [math.log(b / a) for a, b in zip(closes[:-1], closes[1:]) if a > 0 and b > 0]


def annualized_volatility(closes: list[float], days: int, periods_per_year: int = 252) -> Optional[float]:
    rets = _log_returns(closes[-(days + 1):])
    if len(rets) < max(10, days // 2):
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(periods_per_year)


def drawdowns(closes: list[float]) -> tuple[Optional[float], Optional[float]]:
    """(max drawdown over the window, current drawdown from the window's peak),
    both as negative fractions (-0.18 = 18% below the peak)."""
    if len(closes) < 2:
        return None, None
    peak, worst = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        worst = min(worst, c / peak - 1)
    return worst, closes[-1] / max(closes) - 1


def historical_var(closes: list[float], confidence: float = 0.95) -> Optional[float]:
    """One-day historical Value at Risk: the daily loss exceeded on only
    (1 - confidence) of days in the window, as a negative fraction."""
    rets = [b / a - 1 for a, b in zip(closes[:-1], closes[1:]) if a > 0]
    if len(rets) < 60:
        return None
    rets.sort()
    return rets[min(len(rets) - 1, int(math.floor((1 - confidence) * len(rets))))]


def compute(bars: list[dict], crypto: bool = False) -> Optional[dict]:
    """All indicators from daily bars [{time, open, high, low, close, volume}],
    oldest first. None if there isn't enough history."""
    bars = [b for b in bars if b.get("close")]
    if len(bars) < MIN_BARS:
        return None
    closes = [float(b["close"]) for b in bars]
    highs = [float(b.get("high") or b["close"]) for b in bars]
    lows = [float(b.get("low") or b["close"]) for b in bars]
    vols = [float(b.get("volume") or 0) for b in bars]
    price = closes[-1]
    per_year = 365 if crypto else 252

    def ret(days: int) -> Optional[float]:
        return price / closes[-(days + 1)] - 1 if len(closes) > days else None

    a = atr(highs, lows, closes)
    max_dd, cur_dd = drawdowns(closes)
    vol_avg20 = _sma(vols, 20)
    out = {
        "price": price,
        "bars": len(closes),
        "rsi14": rsi(closes),
        "macd": macd(closes),
        "sma20": _sma(closes, 20),
        "sma50": _sma(closes, 50),
        "sma200": _sma(closes, 200),
        "bollinger_pct_b": bollinger_pct_b(closes),
        "atr14": a,
        "atr_pct": (a / price) if a and price else None,
        "volatility_30d": annualized_volatility(closes, 30, per_year),
        "volatility_90d": annualized_volatility(closes, 90, per_year),
        "max_drawdown": max_dd,
        "drawdown_from_high": cur_dd,
        "var95_1d": historical_var(closes),
        "return_1w": ret(5),
        "return_1m": ret(21),
        "return_3m": ret(63),
        "return_6m": ret(126),
        "return_1y": ret(len(closes) - 1) if len(closes) >= 240 else None,
        "support_20d": min(lows[-20:]),
        "resistance_20d": max(highs[-20:]),
        "support_60d": min(lows[-60:]) if len(lows) >= 60 else None,
        "resistance_60d": max(highs[-60:]) if len(highs) >= 60 else None,
        "volume_vs_20d_avg": (vols[-1] / vol_avg20) if vol_avg20 and vols[-1] else None,
    }
    return out


def _pct(x: Optional[float], signed: bool = True) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def _num(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    return f"{x:,.2f}" if abs(x) < 1e5 else f"{x:,.0f}"


def summarize(t: Optional[dict]) -> Optional[str]:
    """The block appended to the agents' context. Every value is computed; n/a
    means not enough history, and the agents are told not to estimate it."""
    if not t:
        return None
    lines = []
    if t["rsi14"] is not None:
        zone = "overbought" if t["rsi14"] >= 70 else "oversold" if t["rsi14"] <= 30 else "neutral"
        lines.append(f"RSI(14): {t['rsi14']:.1f} ({zone})")
    m = t["macd"]
    if m:
        state = "above" if m["histogram"] > 0 else "below"
        cross = f"; {m['recent_cross']} crossover in the last 3 sessions" if m["recent_cross"] else ""
        lines.append(f"MACD(12,26,9): line {_num(m['macd'])}, signal {_num(m['signal'])} — MACD {state} signal{cross}")
    mas = []
    for key, label in (("sma20", "20d"), ("sma50", "50d"), ("sma200", "200d")):
        if t[key]:
            mas.append(f"{label} {_num(t[key])} (price {'above' if t['price'] > t[key] else 'below'})")
    if mas:
        lines.append("Moving averages: " + ", ".join(mas))
    if t["sma50"] and t["sma200"]:
        lines.append(f"50d vs 200d: {'50d above 200d (golden-cross regime)' if t['sma50'] > t['sma200'] else '50d below 200d (death-cross regime)'}")
    if t["bollinger_pct_b"] is not None:
        lines.append(f"Bollinger %B(20,2): {t['bollinger_pct_b']:.2f} (0 = lower band, 1 = upper band)")
    lines.append(f"Support/resistance (20d): {_num(t['support_20d'])} / {_num(t['resistance_20d'])}"
                 + (f"; (60d): {_num(t['support_60d'])} / {_num(t['resistance_60d'])}" if t["support_60d"] else ""))
    if t["volume_vs_20d_avg"] is not None:
        lines.append(f"Volume: latest session {t['volume_vs_20d_avg']:.2f}x its 20-day average")
    lines.append("Returns: " + ", ".join(f"{k} {_pct(t[f'return_{k}'])}" for k in ("1w", "1m", "3m", "6m", "1y")))
    lines.append(f"Volatility (annualized): 30d {_pct(t['volatility_30d'], False)}, 90d {_pct(t['volatility_90d'], False)}")
    if t["atr14"]:
        lines.append(f"ATR(14): {_num(t['atr14'])} ({_pct(t['atr_pct'], False)} of price) — a 2x ATR stop is "
                     f"~{_num(2 * t['atr14'])} from entry")
    lines.append(f"Drawdown: max {_pct(t['max_drawdown'])} over the last {t['bars']} sessions; now {_pct(t['drawdown_from_high'])} from the period high")
    if t["var95_1d"] is not None:
        lines.append(f"1-day 95% historical VaR: {_pct(t['var95_1d'])} (a daily loss this large or worse on ~1 day in 20)")
    return "\n".join(lines)
```

### `backend/tests/test_indicators.py`
```python
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
```

### `backend/tests/test_pipeline_technicals.py`
```python
"""The agents' shared context carries the computed technicals, and the agents
that use them are told never to invent indicator values."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pipeline as P  # noqa: E402

QUOTE = {"name": "Reliance Industries", "price": 2450.0, "currency": "INR", "changePercent": 0.8,
         "fiftyTwoWeekLow": 2200, "fiftyTwoWeekHigh": 3200, "sparkline": [2400, 2450], "exchange": "NSE"}


def test_context_includes_the_computed_block():
    ctx = P.build_context("RELIANCE.NS", QUOTE, "P/E: 24", "RSI(14): 61.2 (neutral)")
    assert "COMPUTED TECHNICALS (calculated from the last year of daily prices" in ctx
    assert "RSI(14): 61.2 (neutral)" in ctx


def test_context_says_unavailable_instead_of_leaving_room_to_guess():
    ctx = P.build_context("RELIANCE.NS", QUOTE, None, None)
    assert "COMPUTED TECHNICALS: unavailable for this asset — do not estimate indicator values." in ctx


def test_old_three_argument_calls_still_work():
    assert "TICKER: AAPL" in P.build_context("AAPL", QUOTE, None)


def test_number_hungry_agents_are_told_not_to_invent():
    for prompt in (P.TECH_SYS, P.TRADER_SYS, P.RISK_SYS):
        assert P.NO_INVENTED_NUMBERS in prompt
        assert prompt.endswith(P.STYLE)
    assert "ATR" in P.TRADER_SYS and "VaR" in P.RISK_SYS
```

## 2. Edits (apply this diff exactly)

What it does:
- **`market_data.py`:**
  - The bar parsing moves into `_bars_from_chart`. `fetch_ohlc_sync`'s output is unchanged, and this was verified.
  - New `fetch_daily_bars_sync`: one year of **daily** bars. The chart's 1Y range is weekly.
- **`pipeline.py`:**
  - `build_context` gains an optional `technicals_summary` and always adds a COMPUTED TECHNICALS block, or an explicit "unavailable — do not estimate" line.
  - `run_analysis` fetches the daily bars, computes, adds the block to the context and stores `technicals` on the analysis document. A failure only logs a warning; the analysis still runs.
  - The TECH, TRADER and RISK prompts get the no-invented-numbers rule. The Trader sizes stops from ATR and support/resistance; Risk uses volatility, ATR, drawdown and VaR.

```diff
diff --git a/backend/market_data.py b/backend/market_data.py
index 0a67b64..fbac6cb 100644
--- a/backend/market_data.py
+++ b/backend/market_data.py
@@ -326,12 +326,7 @@ async def tag_news_sentiment(symbol: str, items: list) -> list:
     return items
 
 # --- OHLC candles (additive; used by the in-app fallback chart for NSE/BSE) ---
-def fetch_ohlc_sync(symbol: str, rng: str) -> dict:
-    range_, interval = RANGE_MAP.get(rng, ("1mo", "1d"))
-    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
-    r = _yf_get(url, {"range": range_, "interval": interval})
-    r.raise_for_status()
-    result = r.json()["chart"]["result"][0]
+def _bars_from_chart(result: dict) -> list:
     ts = result.get("timestamp") or []
     q = (result.get("indicators", {}).get("quote") or [{}])[0]
     o, h, l, c, v = (q.get(k) or [] for k in ("open", "high", "low", "close", "volume"))
@@ -350,6 +345,16 @@ def fetch_ohlc_sync(symbol: str, rng: str) -> dict:
             })
         except (IndexError, TypeError, ValueError):
             continue
+    return bars
+
+
+def fetch_ohlc_sync(symbol: str, rng: str) -> dict:
+    range_, interval = RANGE_MAP.get(rng, ("1mo", "1d"))
+    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
+    r = _yf_get(url, {"range": range_, "interval": interval})
+    r.raise_for_status()
+    result = r.json()["chart"]["result"][0]
+    bars = _bars_from_chart(result)
     meta = result.get("meta", {})
     return {
         "symbol": meta.get("symbol", symbol),
@@ -360,6 +365,15 @@ def fetch_ohlc_sync(symbol: str, rng: str) -> dict:
     }
 
 
+def fetch_daily_bars_sync(symbol: str) -> list:
+    """One year of DAILY bars for the computed technicals (indicators.py). The
+    chart's "1Y" range is weekly, which is too coarse for RSI/ATR/volatility."""
+    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
+    r = _yf_get(url, {"range": "1y", "interval": "1d"})
+    r.raise_for_status()
+    return _bars_from_chart(r.json()["chart"]["result"][0])
+
+
 
 async def get_market(category: str) -> list:
     now = time.time()
diff --git a/backend/pipeline.py b/backend/pipeline.py
index 3f84705..deac70f 100644
--- a/backend/pipeline.py
+++ b/backend/pipeline.py
@@ -15,8 +15,9 @@ from typing import Optional
 from emergentintegrations.llm.chat import LlmChat, UserMessage
 
 import fundamentals as fund
+import indicators as ind
 from core import EMERGENT_LLM_KEY, MODEL_NAME, MODEL_PROVIDER, db, logger, now_iso
-from market_data import fetch_fundamentals_sync, fetch_quote_sync
+from market_data import fetch_daily_bars_sync, fetch_fundamentals_sync, fetch_quote_sync
 
 
 # ----------------------------------------------------------------------------
@@ -28,15 +29,34 @@ STYLE = (
     "End with a final line formatted exactly as 'SIGNAL: BULLISH' or 'SIGNAL: BEARISH' or 'SIGNAL: NEUTRAL'."
 )
 
-TECH_SYS = "You are a veteran Technical Analyst at a hedge fund. You read price action, trend, momentum (MACD/RSI), support/resistance and volume." + STYLE
+# The technical and risk agents used to be asked for indicators they were
+# never given, and wrote invented readings. Now they get real ones — and are
+# told plainly not to make up any that are missing.
+NO_INVENTED_NUMBERS = (
+    "Quote indicator values ONLY from the COMPUTED TECHNICALS block; if a figure is missing or n/a, say it is "
+    "unavailable — never estimate or invent an indicator value."
+)
+
+TECH_SYS = (
+    "You are a veteran Technical Analyst at a hedge fund. You read price action, trend, momentum (MACD/RSI), support/resistance and volume. "
+    + NO_INVENTED_NUMBERS
+) + STYLE
 FUND_SYS = "You are a Fundamentals Analyst. You judge valuation, growth, margins, balance sheet strength and competitive moat." + STYLE
 SENT_SYS = "You are a Sentiment Analyst. You gauge crowd mood from social chatter, retail flow and options positioning for short-term bias." + STYLE
 NEWS_SYS = "You are a Macro & News Analyst. You weigh recent headlines, catalysts, sector rotation and macro conditions." + STYLE
 BULL_SYS = "You are the Bull Researcher. You build the strongest possible case to BUY, using the analyst reports. Be persuasive but grounded; rebut the bear directly when given." + STYLE
 BEAR_SYS = "You are the Bear Researcher. You build the strongest possible case to SELL/AVOID, using the analyst reports. Be persuasive but grounded; rebut the bull directly when given." + STYLE
 RM_SYS = "You are the Research Manager judging the bull vs bear debate. Declare which side won and the recommended stance. Be decisive." + STYLE
-TRADER_SYS = "You are the Trader. Turn the research into a concrete plan: action (buy/sell/hold), entry zone, target, stop-loss and position sizing rationale." + STYLE
-RISK_SYS = "You are the Risk Manager. Stress-test the trade for volatility, liquidity, downside and sizing. Approve, adjust or reject with reasoning." + STYLE
+TRADER_SYS = (
+    "You are the Trader. Turn the research into a concrete plan: action (buy/sell/hold), entry zone, target, stop-loss and position sizing rationale. "
+    "Size the stop-loss from the measured volatility (ATR) and the support/resistance levels in COMPUTED TECHNICALS when they are given. "
+    + NO_INVENTED_NUMBERS
+) + STYLE
+RISK_SYS = (
+    "You are the Risk Manager. Stress-test the trade for volatility, liquidity, downside and sizing. Approve, adjust or reject with reasoning. "
+    "Use the volatility, ATR, drawdown and VaR figures in COMPUTED TECHNICALS when they are given. "
+    + NO_INVENTED_NUMBERS
+) + STYLE
 PM_SYS = (
     "You are the Portfolio Manager making the FINAL call after reviewing the entire desk. "
     "Output ONLY a raw JSON object (no markdown fences, no prose) with EXACTLY these keys: "
@@ -351,7 +371,8 @@ def fallback_timeframes(verdict: dict) -> dict:
     }
 
 
-def build_context(symbol: str, quote: Optional[dict], fundamentals_summary: Optional[str] = None) -> str:
+def build_context(symbol: str, quote: Optional[dict], fundamentals_summary: Optional[str] = None,
+                  technicals_summary: Optional[str] = None) -> str:
     if symbol.endswith("=F"):
         asset_class = "Commodity / futures contract"
     elif symbol.endswith("-USD") or symbol.endswith("=X"):
@@ -378,6 +399,12 @@ def build_context(symbol: str, quote: Optional[dict], fundamentals_summary: Opti
         lines.append("")
         lines.append("FUNDAMENTALS (source: latest available data, may lag real-time filings):")
         lines.append(fundamentals_summary)
+    lines.append("")
+    if technicals_summary:
+        lines.append("COMPUTED TECHNICALS (calculated from the last year of daily prices; these are exact, quote them as given):")
+        lines.append(technicals_summary)
+    else:
+        lines.append("COMPUTED TECHNICALS: unavailable for this asset — do not estimate indicator values.")
     return "\n".join(lines)
 
 
@@ -425,7 +452,20 @@ async def run_analysis(analysis_id: str, symbol: str, language: str = "en"):
         except Exception as e:
             logger.warning(f"fundamentals unavailable for {symbol}: {e}")
 
-        ctx = build_context(symbol, quote, fundamentals_summary)
+        technicals_summary = None
+        try:
+            bars = await asyncio.to_thread(fetch_daily_bars_sync, symbol)
+            technicals = ind.compute(bars, crypto=symbol.endswith("-USD"))
+            technicals_summary = ind.summarize(technicals)
+            if technicals:
+                await db.analyses.update_one(
+                    {"id": analysis_id},
+                    {"$set": {"technicals": technicals, "updated_at": now_iso()}},
+                )
+        except Exception as e:
+            logger.warning(f"computed technicals unavailable for {symbol}: {e}")
+
+        ctx = build_context(symbol, quote, fundamentals_summary, technicals_summary)
         step = 0
         lang_directive = language_directive(language if language in SUPPORTED_LANGUAGES else "en")
```

## 3. Append to `memory/PRD.md`
```markdown
## Computed technicals for the agents (2026-10-07) — branch `feature/computed-indicators`
- **Problem**: the Technical Analyst was asked to read "MACD/RSI, support/resistance and volume" and the Risk Manager to "stress-test volatility and downside", but `build_context` only gave price, 1-day change, 52-week range and an UP/DOWN trend from two prices — so those readings were invented.
- **Now**: `backend/indicators.py` (pure functions, standard definitions) computes from one year of DAILY bars (`market_data.fetch_daily_bars_sync`; the chart's 1Y range is weekly): Wilder RSI(14), MACD(12,26,9) with recent-crossover flag, SMA 20/50/200 and the 50/200 regime, Bollinger %B(20,2), 20/60-day support/resistance, volume vs 20-day average, 1w/1m/3m/6m/1y returns, annualized 30/90-day volatility (365-day basis for crypto), Wilder ATR(14) with a 2xATR stop distance, max and current drawdown, 1-day 95% historical VaR. `pipeline.run_analysis` adds them to the shared context as a COMPUTED TECHNICALS block (or an explicit "unavailable — do not estimate" line) and stores them on the analysis as `technicals`. TECH/TRADER/RISK prompts: quote indicator values only from that block, never invent; the Trader sizes the stop from ATR and support/resistance; the Risk Manager uses volatility/ATR/drawdown/VaR. `fetch_ohlc_sync` refactored to a shared `_bars_from_chart` (output unchanged, verified).
- **Verified**: `tests/test_indicators.py` 13 passed — RSI matches the published StockCharts Wilder example exactly (70.53, 66.32, 66.55, 69.41, 66.36, 57.97), MACD and volatility match an independent pandas implementation; `tests/test_pipeline_technicals.py` 4 passed. Not verified here: live Yahoo data (blocked in this sandbox) and an end-to-end analysis.
- **Cost**: one extra Yahoo chart request per analysis and ~300 more prompt tokens per agent call (about a dozen calls per analysis).
```

---

## 4. Verify (all must pass)

1. **Unit tests:** `cd backend && pytest tests/test_indicators.py tests/test_pipeline_technicals.py`: **17 passed** (13 + 4).
2. **Full backend suite** passes as before, including the OHLC/chart tests and the analysis end-to-end tests. The step counts are unchanged; no agent was added.
3. **Live check — the main proof:** run a real analysis for **RELIANCE.NS**, **AAPL** and **GC=F**. For each, report:
   - **Stored:** the `technicals` stored on the analysis has `rsi14`, `macd`, `atr14`, `volatility_30d` and `var95_1d` filled in.
   - **Quoted correctly:** the Technical Analyst's message quotes the **same** RSI and MACD values as `technicals`, rounded the same, and mentions no indicator that isn't in the block.
   - **Grounded levels:** the Trader's stop-loss is roughly consistent with the ATR-based stop distance shown.
   - **No technicals:** for a symbol with too little history, the context shows "COMPUTED TECHNICALS: unavailable" and the agents say so rather than guessing.
4. **Cost check:** report the prompt-token change per analysis (`backend/tests/measure_tokens.py` if it still runs). Expect roughly +300 tokens per agent call.
5. **Commit message:** contains no secret values. Push normally.

## 5. Tell the owner

- **Results:** the §5.3 results for the three symbols, with the RSI/MACD values the agents quoted next to the stored ones.
- **No app build needed:** this is backend only, so it goes live with a backend deploy.
