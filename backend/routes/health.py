"""Portfolio Health Check — POST /api/portfolio/health.

Fetches a year of dated prices for each holding (plus FX and the benchmark
index), then hands everything to portfolio_health.build_report. Free to run:
no LLM call is made, only Yahoo, so it is bounded by rate limits rather than
priced. Holdings live on the device and are not stored here either.
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import portfolio_health as ph
from core import logger
from deps import get_current_user
from limits import limit_health_check, limit_health_check_global
from market_data import TICKER_RE, fetch_dated_history_sync, fetch_sector_sync

api_router = APIRouter(prefix="/api")

_CONCURRENCY = 5


class HealthHolding(BaseModel):
    symbol: str
    quantity: float = Field(gt=0, allow_inf_nan=False)


class HealthRequest(BaseModel):
    # Same cap as /portfolio/optimize and the app's MAX_HOLDINGS.
    holdings: list[HealthHolding] = Field(max_length=50)
    cash: float = Field(default=0.0, ge=0, allow_inf_nan=False)  # in the report's base currency
    # The user's own target mix, percent per group (see portfolio_health.BUCKETS).
    targets: Optional[dict[str, float]] = None


@api_router.post(
    "/portfolio/health",
    dependencies=[Depends(limit_health_check), Depends(limit_health_check_global)],
)
async def portfolio_health(body: HealthRequest, user: dict = Depends(get_current_user)):
    symbols = [h.symbol.strip().upper() for h in body.holdings]
    if not symbols:
        raise HTTPException(status_code=400, detail="Add at least one holding first.")
    if not all(TICKER_RE.match(s) for s in symbols):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol in holdings")
    if len(set(symbols)) != len(symbols):
        raise HTTPException(status_code=400, detail="Duplicate symbols in holdings")
    try:
        targets = ph.parse_targets(body.targets)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def fetch(fn, arg):
        async with sem:
            try:
                return await asyncio.to_thread(fn, arg)
            except Exception as e:
                logger.warning(f"health check: {fn.__name__}({arg}) failed: {type(e).__name__}")
                return None

    histories = dict(zip(symbols, await asyncio.gather(*(fetch(fetch_dated_history_sync, s) for s in symbols))))
    if not any(histories.values()):
        raise HTTPException(status_code=502, detail="Couldn't fetch prices right now. Try again in a minute.")
    currencies = sorted({ph._unit(h.get("currency"))[0] for h in histories.values() if h and h.get("currency")})
    base = ph.base_currency(currencies)
    foreign = [c for c in currencies if c != base]
    bench_symbol = ph.BENCHMARKS.get(base, ph.BENCHMARKS["USD"])[0]

    # Sectors only matter for single stocks; funds and crypto have none.
    equity = [s for s, h in histories.items() if h and (h.get("quote_type") or "EQUITY") == "EQUITY"]
    sector_list, fx_list, benchmark = await asyncio.gather(
        asyncio.gather(*(fetch(fetch_sector_sync, s) for s in equity)),
        asyncio.gather(*(fetch(fetch_dated_history_sync, f"{c}{base}=X") for c in foreign)),
        fetch(fetch_dated_history_sync, bench_symbol),
    )
    sectors = dict(zip(equity, sector_list))
    fx = {c: (h or {}).get("closes") or None for c, h in zip(foreign, fx_list)}

    try:
        report = await asyncio.to_thread(
            ph.build_report,
            [{"symbol": s, "quantity": h.quantity} for s, h in zip(symbols, body.holdings)],
            body.cash, targets, histories, sectors, fx, benchmark, base,
        )
    except Exception:
        logger.exception("health check failed")
        raise HTTPException(status_code=422, detail="Couldn't check this portfolio — try again or with fewer holdings.")
    return report
