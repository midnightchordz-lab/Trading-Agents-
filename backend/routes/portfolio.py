"""Portfolio optimization — reads cached verdicts and price history only.

Never runs the agent pipeline and never mutates an analysis; it is a pure
calculation over what has already been produced.
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import portfolio_optimizer as pfopt
from core import db, logger
from market_data import _yf_get, fetch_quote_sync

api_router = APIRouter(prefix="/api")


# --- Portfolio optimization (additive; never mutates analyses or the pipeline) ---
def fetch_price_history_sync(symbol: str, days: int = 300) -> list:
    """Daily closes for covariance/return estimation. Independent of the
    existing /chart and /ohlc endpoints so their behavior is untouched."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = _yf_get(url, {"range": "1y", "interval": "1d"})
    r.raise_for_status()
    result = r.json()["chart"]["result"][0]
    closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", []) or []
    return [round(float(c), 4) for c in closes if c is not None][-days:]


async def latest_verdict_for(symbol: str) -> Optional[dict]:
    """Most recent completed analysis' verdict for a symbol, or None.

    Never triggers a new analysis run; only reads what already exists.
    """
    doc = await db.analyses.find_one(
        {"symbol": symbol, "status": "completed", "verdict": {"$ne": None}},
        sort=[("updated_at", -1)],
    )
    return doc["verdict"] if doc else None


class PortfolioHolding(BaseModel):
    symbol: str
    quantity: float
    avg_price: float


class PortfolioOptimizeRequest(BaseModel):
    # Capped because this endpoint needs no session and fans every holding out
    # to two external Yahoo calls — an uncapped list is a free amplifier for
    # anyone who wants to burn our upstream quota. 50 is far beyond any real
    # portfolio a phone screen can show.
    holdings: list[PortfolioHolding] = Field(max_length=50)
    objective: str = "hrp"  # "hrp" | "max_sharpe" | "min_volatility"
    use_agent_views: bool = False
    cash: float = 0.0  # additional uninvested cash to include in total value


@api_router.post("/portfolio/optimize")
async def portfolio_optimize(body: PortfolioOptimizeRequest):
    if not body.holdings:
        raise HTTPException(status_code=400, detail="No holdings supplied")
    symbols = [h.symbol.strip().upper() for h in body.holdings]
    if len(set(symbols)) != len(symbols):
        raise HTTPException(status_code=400, detail="Duplicate symbols in holdings")

    quotes = await asyncio.gather(
        *[asyncio.to_thread(fetch_quote_sync, s) for s in symbols], return_exceptions=True
    )
    current_prices: dict = {}
    quote_failed: list = []
    for sym, q in zip(symbols, quotes):
        if isinstance(q, Exception) or not q.get("price"):
            quote_failed.append(sym)
        else:
            current_prices[sym] = float(q["price"])

    histories = await asyncio.gather(
        *[asyncio.to_thread(fetch_price_history_sync, s) for s in symbols], return_exceptions=True
    )
    price_history = {
        sym: (h if not isinstance(h, Exception) else [])
        for sym, h in zip(symbols, histories)
    }

    prices_df, dropped = pfopt.build_price_frame(price_history)
    if prices_df.empty or prices_df.shape[1] < 2:
        raise HTTPException(status_code=422, detail="Not enough price history to optimize (need at least 2 symbols with sufficient history)")

    usable_symbols = list(prices_df.columns)
    holdings_by_symbol = {h.symbol.strip().upper(): h for h in body.holdings}
    values = {s: holdings_by_symbol[s].quantity * current_prices.get(s, holdings_by_symbol[s].avg_price) for s in usable_symbols if s in holdings_by_symbol}
    total_value = sum(values.values()) + max(0.0, body.cash)
    current_weights = {s: (v / total_value if total_value else 0.0) for s, v in values.items()}

    verdicts, missing_view_for = {}, []
    if body.use_agent_views:
        for sym in usable_symbols:
            v = await latest_verdict_for(sym)
            if v:
                verdicts[sym] = v
            else:
                missing_view_for.append(sym)

    objective = body.objective if body.objective in ("hrp", "max_sharpe", "min_volatility") else "hrp"
    try:
        result = pfopt.optimize(
            prices_df,
            objective,
            verdicts=verdicts if body.use_agent_views else None,
            current_prices={s: current_prices.get(s) for s in usable_symbols if current_prices.get(s)},
        )
    except Exception as e:
        logger.exception("portfolio optimization failed")
        raise HTTPException(status_code=422, detail=f"Optimization failed: {e}")

    actions = pfopt.classify_actions(current_weights, result["weights"])
    alloc, leftover = ({}, total_value)
    try:
        alloc, leftover = pfopt.discrete_allocation(result["weights"], current_prices, total_value)
    except Exception as e:
        logger.warning(f"discrete allocation failed: {e}")

    return {
        "objective": objective,
        "used_agent_views_for": result.get("views_used", []),
        "missing_agent_view_for": missing_view_for,
        "dropped_symbols": dropped,
        "total_value": round(total_value, 2),
        "current_weights": {k: round(v, 4) for k, v in current_weights.items()},
        "suggested_weights": {k: round(v, 4) for k, v in result["weights"].items()},
        "actions": actions,
        "suggested_shares": alloc,
        "leftover_cash": round(leftover, 2),
        "expected_return": result["expected_return"],
        "volatility": result["volatility"],
        "sharpe": result["sharpe"],
        "current_prices": current_prices,
    }

