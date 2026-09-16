"""Read-only market endpoints: search, quotes, charts, candles, news, movers.

All public and all cheap — no LLM calls, no billing, no session required.
"""
import asyncio
import time

from fastapi import APIRouter, HTTPException

from core import logger
from market_data import (
    RANGE_MAP,
    _news_cache,
    fetch_chart_sync,
    fetch_news_sync,
    fetch_ohlc_sync,
    fetch_quote_sync,
    get_market,
    search_sync,
    tag_news_sentiment,
)

api_router = APIRouter(prefix="/api")


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------
@api_router.get("/")
async def root():
    return {"service": "TradingAgents", "status": "ok"}


@api_router.get("/search")
async def search(q: str):
    q = (q or "").strip()
    if not q:
        return {"results": []}
    try:
        results = await asyncio.to_thread(search_sync, q)
        return {"results": results}
    except Exception as e:
        logger.warning(f"search failed: {e}")
        return {"results": []}


@api_router.get("/quote/{symbol}")
async def quote(symbol: str):
    try:
        data = await asyncio.to_thread(fetch_quote_sync, symbol)
        return data
    except Exception as e:
        logger.warning(f"quote failed for {symbol}: {e}")
        raise HTTPException(status_code=404, detail="Quote unavailable for this ticker")


@api_router.get("/chart/{symbol}")
async def chart(symbol: str, range: str = "1M"):
    rng = range.upper()
    if rng not in RANGE_MAP:
        rng = "1M"
    try:
        data = await asyncio.to_thread(fetch_chart_sync, symbol, rng)
        return data
    except Exception as e:
        logger.warning(f"chart failed for {symbol}: {e}")
        raise HTTPException(status_code=404, detail="Chart unavailable for this ticker")


@api_router.get("/ohlc/{symbol}")
async def ohlc(symbol: str, range: str = "1M"):
    rng = range.upper()
    if rng not in RANGE_MAP:
        rng = "1M"
    try:
        return await asyncio.to_thread(fetch_ohlc_sync, symbol, rng)
    except Exception as e:
        logger.warning(f"ohlc failed for {symbol}: {e}")
        raise HTTPException(status_code=404, detail="OHLC unavailable for this ticker")


@api_router.get("/news/{symbol}")
async def news(symbol: str):
    key = (symbol or "").upper().strip()
    entry = _news_cache.get(key)
    if entry and time.time() - entry["ts"] < 600:
        return {"results": entry["data"]}
    try:
        items = await asyncio.to_thread(fetch_news_sync, symbol)
        items = await tag_news_sentiment(key, items)
        _news_cache[key] = {"ts": time.time(), "data": items}
        return {"results": items}
    except Exception as e:
        logger.warning(f"news failed for {symbol}: {e}")
        return {"results": []}



@api_router.get("/trending")
async def trending():
    return {"results": await get_market("trending")}


@api_router.get("/markets/{category}")
async def markets(category: str):
    return {"results": await get_market(category)}

