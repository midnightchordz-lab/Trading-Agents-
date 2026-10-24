"""Unit tests for portfolio_optimizer.py (pure functions, no server/DB/network)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import portfolio_optimizer as pfopt


def _synthetic_prices(symbols, n=260, seed=0):
    rng = np.random.default_rng(seed)
    hist = {}
    for i, s in enumerate(symbols):
        rets = rng.normal(0.0004 + i * 0.0001, 0.015, n)
        hist[s] = list(100 * np.cumprod(1 + rets))
    return hist


def test_build_price_frame_drops_short_history():
    hist = _synthetic_prices(["A", "B"], n=200)
    hist["C"] = [100.0, 101.0]  # too short
    df, dropped = pfopt.build_price_frame(hist)
    assert "C" in dropped
    assert set(df.columns) == {"A", "B"}


def test_hrp_weights_sum_to_one():
    hist = _synthetic_prices(["A", "B", "C", "D"])
    df, _ = pfopt.build_price_frame(hist)
    result = pfopt.optimize(df, "hrp")
    assert abs(sum(result["weights"].values()) - 1.0) < 1e-6
    assert all(w >= -1e-9 for w in result["weights"].values())


def test_max_sharpe_weights_sum_to_one():
    hist = _synthetic_prices(["A", "B", "C"])
    df, _ = pfopt.build_price_frame(hist)
    result = pfopt.optimize(df, "max_sharpe")
    assert abs(sum(result["weights"].values()) - 1.0) < 1e-6


def test_black_litterman_view_moves_weight_toward_buy():
    hist = _synthetic_prices(["A", "B", "C"], seed=3)
    df, _ = pfopt.build_price_frame(hist)
    current_prices = {s: hist[s][-1] for s in hist}
    no_view = pfopt.optimize(df, "max_sharpe")
    verdicts = {"A": {"decision": "BUY", "confidence": 90, "target_price": current_prices["A"] * 1.4}}
    with_view = pfopt.optimize(df, "max_sharpe", verdicts=verdicts, current_prices=current_prices)
    assert "A" in with_view["views_used"]
    assert with_view["weights"]["A"] >= no_view["weights"]["A"] - 1e-9


def test_hold_and_missing_target_produce_no_view():
    views, conf = pfopt.build_bl_views(
        {
            "A": {"decision": "HOLD", "confidence": 70, "target_price": 100},
            "B": {"decision": "BUY", "confidence": 70, "target_price": None},
            "C": {"decision": "BUY", "confidence": 70, "target_price": 120},
        },
        current_prices={"A": 100, "B": 100, "C": 100},
    )
    assert views == {"C": pytest.approx(0.2)}
    assert set(conf.keys()) == {"C"}


def test_classify_actions_describes_model_weights():
    actions = pfopt.classify_actions({"A": 0.3, "C": 0.5}, {"A": 0.0, "B": 0.5, "C": 0.2})
    by_symbol = {a["symbol"]: a["action"] for a in actions}
    assert by_symbol == {"A": "NOT IN MODEL", "B": "BELOW MODEL", "C": "ABOVE MODEL"}


def test_classify_actions_in_line_within_band():
    actions = pfopt.classify_actions({"A": 0.30}, {"A": 0.32})
    assert actions[0]["action"] == "IN LINE"


def test_a_big_gap_on_a_small_weight_is_not_in_line():
    # 10.3% held vs 6.4% in the model is 60% more, not "in line".
    actions = pfopt.classify_actions({"A": 0.103, "B": 0.155}, {"A": 0.064, "B": 0.131})
    by_symbol = {a["symbol"]: a["action"] for a in actions}
    assert by_symbol == {"A": "ABOVE MODEL", "B": "IN LINE"}


def test_no_trade_language_anywhere_in_the_labels():
    import re
    assert not any(re.search(r"\b(BUY|SELL|ADD|TRIM|HOLD|EXIT)\b", r) for r in pfopt.RELATIONS)
    assert not hasattr(pfopt, "discrete_allocation")     # no "shares to buy" any more


