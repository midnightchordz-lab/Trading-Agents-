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
