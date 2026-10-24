"""Chart-pattern detection on hand-built price series where the answer is
known. Factual shapes only — nothing here says what to trade."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import patterns as pt  # noqa: E402


def flat_bars(closes, spread=0.5):
    """OHLC around each close with a small, symmetric range."""
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + spread for o, c in zip(opens, closes)]
    lows = [min(o, c) - spread for o, c in zip(opens, closes)]
    return opens, highs, lows, list(closes)


def with_last(o, h, l, c, last):
    lo, lh, ll, lc = last
    return o[:-1] + [lo], h[:-1] + [lh], l[:-1] + [ll], c[:-1] + [lc]


# --- candles ------------------------------------------------------------------

def test_shooting_star_after_a_rise():
    closes = [100 + i for i in range(10)]                  # rising into the last bar
    o, h, l, c = with_last(*flat_bars(closes), (109.0, 115.0, 108.8, 109.3))
    assert pt.candle(o, h, l, c) == "shooting_star"


def test_hammer_after_a_fall():
    closes = [120 - i for i in range(10)]
    o, h, l, c = with_last(*flat_bars(closes), (111.0, 111.2, 105.0, 110.7))
    assert pt.candle(o, h, l, c) == "hammer"


def test_same_shape_without_the_trend_is_nothing():
    closes = [100.0] * 10                                  # flat, no prior rise
    o, h, l, c = with_last(*flat_bars(closes), (100.0, 106.0, 99.8, 100.3))
    assert pt.candle(o, h, l, c) is None


def test_big_bodies_are_not_stars():
    closes = [100 + i for i in range(10)]
    o, h, l, c = with_last(*flat_bars(closes), (104.0, 112.0, 103.8, 110.0))
    assert pt.candle(o, h, l, c) is None


# --- Bollinger ------------------------------------------------------------------

def test_bollinger_band_maths():
    closes = [10.0] * 19 + [12.0]
    mid, up, lo = pt.bollinger(closes)[-1]
    sd = math.sqrt(((2 - 0.1) ** 2 + 19 * 0.1 ** 2) / 20)
    assert abs(mid - 10.1) < 1e-9 and abs(up - (10.1 + 2 * sd)) < 1e-9 and abs(lo - (10.1 - 2 * sd)) < 1e-9


def test_break_out_of_a_squeeze():
    wild = [100 + (8 if i % 2 else -8) for i in range(100)]   # wide bands for months
    quiet = [100 + (0.3 if i % 2 else -0.3) for i in range(25)]  # then a squeeze
    assert pt.bollinger_break(wild + quiet + [106.0]) == "up"
    assert pt.bollinger_break(wild + quiet + [94.0]) == "down"


def test_band_touch_without_a_squeeze_is_nothing():
    wild = [100 + (8 if i % 2 else -8) for i in range(130)]
    assert pt.bollinger_break(wild + [125.0]) is None


def test_w_bottom_shape():
    base = [100 + (0.5 if i % 2 else -0.5) for i in range(40)]
    w = ([100, 97, 94, 91, 88, 86] +            # first low, below the lower band
         [88, 91, 94, 97, 100, 101] +           # rebound to the middle band
         [99, 96, 92, 89, 87.5] +               # second low, near the first but less extreme
         [90, 93, 96, 99, 101, 102, 103, 103.5, 104, 104.5, 105, 105, 105])
    assert pt.w_or_m(base + w) == "w_bottom"
    assert pt.w_or_m(base + [100.0] * 30) is None


def test_m_top_is_the_mirror():
    base = [100 + (0.5 if i % 2 else -0.5) for i in range(40)]
    m = ([100, 103, 106, 109, 112, 114] + [112, 109, 106, 103, 100, 99] +
         [101, 104, 108, 111, 112.5] + [110, 107, 104, 101, 99, 98, 97, 96.5, 96, 95.5, 95, 95, 95])
    assert pt.w_or_m(base + m) == "m_top"


# --- Parabolic SAR ------------------------------------------------------------------

def test_sar_follows_trend_and_flips():
    up = [100 + i for i in range(40)]
    down = [139 - 2 * i for i in range(1, 6)]
    _, h, l, _ = flat_bars(up + down)
    trends = pt.parabolic_sar(h, l)
    assert trends[30] == 1 and trends[-1] == -1
    flip_at = next(i for i in range(40, len(trends)) if trends[i] == -1)
    _, h2, l2, _ = flat_bars((up + down)[: flip_at + 1])
    assert pt.sar_flip(h2, l2) == "down"
    _, h3, l3, _ = flat_bars(up)
    assert pt.sar_flip(h3, l3) is None


# --- Heikin-Ashi ------------------------------------------------------------------

def test_heikin_ashi_turn_after_a_run():
    closes = [100 - i for i in range(9)] + [98]           # a fall, then one strong up day
    o, h, l, c = flat_bars(closes)
    cols = pt.heikin_ashi_colors(o, h, l, c)
    assert cols[-2] == -1 and cols[-1] == 1
    assert pt.heikin_ashi_turn(o, h, l, c) == "up"


def test_no_turn_inside_a_run():
    o, h, l, c = flat_bars([100 + i for i in range(10)])
    assert pt.heikin_ashi_turn(o, h, l, c) is None


def test_detect_returns_every_field_and_tolerates_short_history():
    o, h, l, c = flat_bars([100.0, 101.0, 102.0])
    assert set(pt.detect(o, h, l, c)) == {"candle", "bb_break", "bb_shape", "sar_flip", "ha_turn"}
    assert all(v is None for v in pt.detect(o, h, l, c).values())
