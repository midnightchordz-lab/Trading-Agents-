"""Portfolio optimization — additive module, imported by server.py.

Wraps PyPortfolioOpt (https://github.com/pyportfolio/pyportfolioopt, MIT) to compute
a model allocation for the user's own holdings and describe, per holding, how
the user's weight compares with the model's (ABOVE MODEL / IN LINE /
BELOW MODEL / NOT IN MODEL). It is a comparison, not an instruction: the app
never tells anyone to buy, add, trim or sell (we are not a SEBI-registered
adviser), so no trade labels or share counts are produced. Two modes:

  - "hrp": Hierarchical Risk Parity on price history alone. No return forecast,
    generally the more robust default.
  - "black_litterman": price-history covariance + the desk's own verdicts as
    views. Only holdings with a cached, *completed* TradingAgents analysis get
    a view; everything else falls back to the historical-mean prior. This
    module never triggers a new agent run — the caller (server.py) decides
    whether to look one up.

Simplification: the Black-Litterman prior here is the historical mean return
(risk_models/expected_returns on price history), not a market-cap CAPM prior —
reliable market-cap data isn't available for every NSE/BSE symbol. Documented
so it isn't mistaken for the textbook market-implied prior.

Verify function names/signatures against the installed pyportfolioopt version;
the public API has been stable but minor signature changes do happen.
"""
from __future__ import annotations

from typing import Literal, Optional

import numpy as np
import pandas as pd
from pypfopt import (
    EfficientFrontier,
    black_litterman,
    expected_returns,
    risk_models,
)
from pypfopt.hierarchical_portfolio import HRPOpt

Objective = Literal["max_sharpe", "min_volatility", "hrp"]

# A model weight under this is "the model leaves it out".
NOT_IN_MODEL_THRESHOLD = 0.01
ACTION_BAND = 0.05  # within +/- 5 percentage points of the model counts as IN LINE

RELATIONS = ("ABOVE MODEL", "IN LINE", "BELOW MODEL", "NOT IN MODEL")


class Holding:
    __slots__ = ("symbol", "quantity", "avg_price")

    def __init__(self, symbol: str, quantity: float, avg_price: float):
        self.symbol = symbol
        self.quantity = quantity
        self.avg_price = avg_price


def build_price_frame(price_history: dict[str, list[float]]) -> pd.DataFrame:
    """price_history: {symbol: [close, close, ...]} (chronological, daily).

    Symbols with too little history to estimate covariance are dropped; the
    caller is told which via the returned list of dropped symbols.
    """
    series = {}
    dropped = []
    lengths = [len(v) for v in price_history.values() if v]
    if not lengths:
        return pd.DataFrame(), list(price_history.keys())
    target_len = max(lengths)
    for sym, closes in price_history.items():
        if len(closes) < min(30, target_len // 2 or 1):
            dropped.append(sym)
            continue
        series[sym] = pd.Series(closes[-target_len:])
    df = pd.DataFrame(series)
    df = df.dropna(axis=1, how="any") if not df.empty else df
    still_missing = [s for s in price_history if s not in df.columns and s not in dropped]
    return df, dropped + still_missing


def historical_prior(prices: pd.DataFrame) -> pd.Series:
    return expected_returns.mean_historical_return(prices)


def sample_covariance(prices: pd.DataFrame) -> pd.DataFrame:
    return risk_models.CovarianceShrinkage(prices).ledoit_wolf()


def build_bl_views(
    verdicts: dict[str, dict],
    current_prices: dict[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    """Turn cached agent verdicts into Black-Litterman absolute views.

    view return = (target_price / current_price) - 1, sign-flipped for SELL,
    skipped for HOLD or a verdict with no usable target. View confidence is
    the verdict's own confidence (0-100) scaled to 0-1 for Idzorek's method.
    """
    views: dict[str, float] = {}
    confidences: dict[str, float] = {}
    for sym, v in verdicts.items():
        price = current_prices.get(sym)
        target = v.get("target_price")
        decision = v.get("decision")
        conf = v.get("confidence")
        if not price or not target or decision not in ("BUY", "SELL") or conf is None:
            continue
        implied = (float(target) / float(price)) - 1.0
        if decision == "SELL":
            implied = -abs(implied)
        else:
            implied = abs(implied)
        views[sym] = implied
        confidences[sym] = max(0.05, min(0.95, float(conf) / 100.0))
    return views, confidences


def optimize(
    prices: pd.DataFrame,
    objective: Objective,
    verdicts: Optional[dict[str, dict]] = None,
    current_prices: Optional[dict[str, float]] = None,
) -> dict:
    """Return {weights, expected_return, volatility, sharpe, views_used}."""
    symbols = list(prices.columns)
    cov = sample_covariance(prices)

    if objective == "hrp":
        returns = expected_returns.returns_from_prices(prices)
        hrp = HRPOpt(returns=returns, cov_matrix=cov)
        weights = hrp.optimize()
        perf = hrp.portfolio_performance(verbose=False)
        return {
            "weights": dict(weights),
            "expected_return": perf[0],
            "volatility": perf[1],
            "sharpe": perf[2],
            "views_used": [],
        }

    views_used: list[str] = []
    if verdicts and current_prices:
        abs_views, confidences = build_bl_views(verdicts, current_prices)
        abs_views = {k: v for k, v in abs_views.items() if k in symbols}
        confidences = {k: v for k, v in confidences.items() if k in abs_views}
        views_used = list(abs_views.keys())
        prior = historical_prior(prices)
        if abs_views:
            bl = black_litterman.BlackLittermanModel(
                cov,
                pi=prior,
                absolute_views=abs_views,
                omega="idzorek",
                view_confidences=[confidences[s] for s in abs_views],
            )
            mu = bl.bl_returns()
        else:
            mu = prior
    else:
        mu = historical_prior(prices)

    ef = EfficientFrontier(mu, cov)
    if objective == "max_sharpe":
        ef.max_sharpe()
    else:
        ef.min_volatility()
    weights = ef.clean_weights()
    perf = ef.portfolio_performance(verbose=False)
    return {
        "weights": dict(weights),
        "expected_return": perf[0],
        "volatility": perf[1],
        "sharpe": perf[2],
        "views_used": views_used,
    }


def classify_actions(
    current_weights: dict[str, float],
    suggested_weights: dict[str, float],
) -> list[dict]:
    """Per symbol, how the user's weight compares with the model's weight.
    The field keeps its old name, "action", so app builds already installed
    still render it; the values are descriptive."""
    out = []
    symbols = set(current_weights) | set(suggested_weights)
    for sym in sorted(symbols):
        cur = current_weights.get(sym, 0.0)
        sug = suggested_weights.get(sym, 0.0)
        delta = sug - cur
        if sug < NOT_IN_MODEL_THRESHOLD and cur > 0:
            action = "NOT IN MODEL"
        elif delta > ACTION_BAND:
            action = "BELOW MODEL"      # the user holds less than the model mix
        elif delta < -ACTION_BAND:
            action = "ABOVE MODEL"
        else:
            action = "IN LINE"
        out.append({
            "symbol": sym,
            "current_weight": round(cur, 4),
            "suggested_weight": round(sug, 4),
            "delta": round(delta, 4),
            "action": action,
        })
    return out
