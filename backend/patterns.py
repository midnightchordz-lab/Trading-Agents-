"""Chart patterns for the daily scans — pure functions over daily OHLC lists.

Each function answers one factual question about the most recent completed
bars ("did yesterday form a shooting star?"), never whether to trade. The
definitions follow the common textbook forms; where a pattern has many
variants, the docstring says which one is used. Lists are oldest first.

Ideas from je-suis-tm/quant-trading (Apache 2.0); the code here is written
fresh, with tests, rather than copied.
"""
from __future__ import annotations

import math
from typing import Optional

TREND_LOOKBACK = 5   # bars used to decide "after a rise" / "after a fall"


def _trend(closes: list[float], end: int) -> Optional[float]:
    """Change over the TREND_LOOKBACK bars before index `end` (exclusive)."""
    if end - TREND_LOOKBACK - 1 < 0:
        return None
    a, b = closes[end - TREND_LOOKBACK - 1], closes[end - 1]
    return (b / a - 1) if a else None


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------
def candle(opens, highs, lows, closes) -> Optional[str]:
    """'shooting_star' or 'hammer' for the last bar, else None.

    Both: a small real body (at most a third of the day's range) with one
    shadow at least twice the body and the other shadow at most half the body
    (or under a tenth of the range for near-doji bodies).
    Shooting star: long UPPER shadow, after a rise over the previous 5 bars.
    Hammer: long LOWER shadow, after a fall over the previous 5 bars."""
    if min(len(opens), len(highs), len(lows), len(closes)) < TREND_LOOKBACK + 2:
        return None
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    rng = h - l
    if rng <= 0:
        return None
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    if body > rng / 3:
        return None
    small = max(body * 0.5, rng * 0.1)
    unit = max(body, rng * 0.05)          # a doji still needs a real shadow
    trend = _trend(closes, len(closes) - 1)
    if trend is None:
        return None
    if upper >= 2 * unit and lower <= small and trend > 0.02:
        return "shooting_star"
    if lower >= 2 * unit and upper <= small and trend < -0.02:
        return "hammer"
    return None


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------
def bollinger(closes: list[float], period: int = 20, k: float = 2.0) -> list[Optional[tuple]]:
    """Per bar (mid, upper, lower), None until `period` closes exist."""
    out: list[Optional[tuple]] = []
    for i in range(len(closes)):
        if i + 1 < period:
            out.append(None)
            continue
        win = closes[i + 1 - period: i + 1]
        mid = sum(win) / period
        sd = math.sqrt(sum((x - mid) ** 2 for x in win) / period)
        out.append((mid, mid + k * sd, mid - k * sd))
    return out


def bollinger_break(closes: list[float], lookback: int = 120, squeeze_pct: float = 0.2,
                    recent: int = 5) -> Optional[str]:
    """'up' or 'down' when the last close is outside the 20-day band AND the
    band was in a squeeze recently: its width (upper - lower) / mid fell into
    the narrowest 20% of the last 120 days at some point in the 5 sessions
    before. A break after a quiet spell, not just any band touch."""
    bands = bollinger(closes)
    widths = [((b[1] - b[2]) / b[0]) if b and b[0] else None for b in bands]
    hist = [w for w in widths[-lookback - 1:-1] if w is not None]
    if len(hist) < 40 or bands[-1] is None:
        return None
    ordered = sorted(hist)
    cutoff, median = ordered[int(len(hist) * squeeze_pct)], ordered[len(hist) // 2]
    if cutoff >= 0.9 * median:
        return None   # the band barely changed width: there is no "quiet spell" to break out of
    squeezed = any(w is not None and w <= cutoff for w in widths[-recent - 1:-1])
    if not squeezed:
        return None
    _, upper, lower = bands[-1]
    if closes[-1] > upper:
        return "up"
    if closes[-1] < lower:
        return "down"
    return None


def w_or_m(closes: list[float], window: int = 30, tol: float = 0.03) -> Optional[str]:
    """'w_bottom' or 'm_top' within the last `window` bars (simplified form).

    W-bottom: a first low at or below the lower band, a rebound to the middle
    band, a second low within 3% of the first but ABOVE the lower band (sellers
    weaker the second time), and the last close back above the middle band.
    M-top is the mirror at the upper band. Price-only shape detection: it says
    the shape formed, nothing about what happens next."""
    if len(closes) < window + 20:
        return None
    bands = bollinger(closes)
    seg = list(range(len(closes) - window, len(closes)))
    mid_now = bands[-1][0]

    def find(low: bool) -> Optional[str]:
        touch = next((i for i in seg[:-2]
                      if (closes[i] <= bands[i][2] if low else closes[i] >= bands[i][1])), None)
        if touch is None:
            return None
        rebound = next((j for j in range(touch + 1, seg[-1])
                        if (closes[j] >= bands[j][0] if low else closes[j] <= bands[j][0])), None)
        if rebound is None:
            return None
        # The first low (high) is the extreme of that whole dip (rally), not
        # the day it first crossed the band.
        first = (min if low else max)(range(touch, rebound), key=lambda j: closes[j])
        rest = range(rebound + 1, seg[-1])
        if not rest:
            return None
        second = (min if low else max)(rest, key=lambda j: closes[j])
        a, b2 = closes[first], closes[second]
        near = abs(b2 / a - 1) <= tol
        inside = closes[second] > bands[second][2] if low else closes[second] < bands[second][1]
        confirmed = closes[-1] > mid_now if low else closes[-1] < mid_now
        if near and inside and confirmed:
            return "w_bottom" if low else "m_top"
        return None

    return find(True) or find(False)


# ---------------------------------------------------------------------------
# Parabolic SAR
# ---------------------------------------------------------------------------
def parabolic_sar(highs: list[float], lows: list[float], step: float = 0.02, max_af: float = 0.2) -> list[int]:
    """Wilder's Parabolic SAR; returns the trend per bar (+1 up, -1 down)."""
    n = len(highs)
    if n < 3:
        return []
    up = highs[1] >= highs[0]
    sar = lows[0] if up else highs[0]
    ep = highs[1] if up else lows[1]
    af = step
    trends = [1 if up else -1, 1 if up else -1]
    for i in range(2, n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, lows[i - 1], lows[i - 2])
            if lows[i] < sar:
                up, sar, ep, af = False, ep, lows[i], step
            elif highs[i] > ep:
                ep, af = highs[i], min(af + step, max_af)
        else:
            sar = max(sar, highs[i - 1], highs[i - 2])
            if highs[i] > sar:
                up, sar, ep, af = True, ep, highs[i], step
            elif lows[i] < ep:
                ep, af = lows[i], min(af + step, max_af)
        trends.append(1 if up else -1)
    return trends


def sar_flip(highs: list[float], lows: list[float]) -> Optional[str]:
    """'up' / 'down' if the SAR trend flipped on the last bar."""
    t = parabolic_sar(highs, lows)
    if len(t) < 30 or t[-1] == t[-2]:
        return None
    return "up" if t[-1] > 0 else "down"


# ---------------------------------------------------------------------------
# Heikin-Ashi
# ---------------------------------------------------------------------------
def heikin_ashi_colors(opens, highs, lows, closes) -> list[int]:
    """+1 for a rising (green) Heikin-Ashi candle, -1 for falling, per bar."""
    n = len(closes)
    if n == 0:
        return []
    ha_open = (opens[0] + closes[0]) / 2
    ha_close = (opens[0] + highs[0] + lows[0] + closes[0]) / 4
    out = [1 if ha_close >= ha_open else -1]
    for i in range(1, n):
        ha_open = (ha_open + ha_close) / 2
        ha_close = (opens[i] + highs[i] + lows[i] + closes[i]) / 4
        out.append(1 if ha_close >= ha_open else -1)
    return out


def heikin_ashi_turn(opens, highs, lows, closes, run: int = 3) -> Optional[str]:
    """'up' / 'down' when the last Heikin-Ashi candle changed colour after at
    least `run` candles of the other colour."""
    col = heikin_ashi_colors(opens, highs, lows, closes)
    if len(col) < run + 1 or col[-1] == col[-2]:
        return None
    if all(c == col[-2] for c in col[-run - 1:-1]):
        return "up" if col[-1] > 0 else "down"
    return None


def detect(opens, highs, lows, closes) -> dict:
    """Every pattern for the last completed bar, as plain values."""
    return {
        "candle": candle(opens, highs, lows, closes),
        "bb_break": bollinger_break(closes),
        "bb_shape": w_or_m(closes),
        "sar_flip": sar_flip(highs, lows),
        "ha_turn": heikin_ashi_turn(opens, highs, lows, closes),
    }
