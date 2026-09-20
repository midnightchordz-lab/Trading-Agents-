"""Read-only market endpoints: search, quotes, charts, candles, news, movers.

All public and all cheap — no LLM calls, no billing, no session required.
"""
import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Request

import rate_limit as rl
from core import logger
from deps import client_ip
from limits import NEWS_LLM_GLOBAL_PER_MIN, NEWS_LLM_PER_MIN, limit_market
from market_data import (
    RANGE_MAP,
    TICKER_RE,
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


@api_router.get("/search", dependencies=[Depends(limit_market)])
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


@api_router.get("/quote/{symbol}", dependencies=[Depends(limit_market)])
async def quote(symbol: str):
    # Matched on the upper-cased form: case isn't a security property, and the
    # app does request lowercase symbols in places. Rejecting those would be a
    # regression for real users, not a fix.
    if not TICKER_RE.match((symbol or "").upper()):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")
    try:
        data = await asyncio.to_thread(fetch_quote_sync, symbol)
        return data
    except Exception as e:
        logger.warning(f"quote failed for {symbol}: {e}")
        raise HTTPException(status_code=404, detail="Quote unavailable for this ticker")


@api_router.get("/chart/{symbol}", dependencies=[Depends(limit_market)])
async def chart(symbol: str, range: str = "1M"):
    # Matched on the upper-cased form: case isn't a security property, and the
    # app does request lowercase symbols in places. Rejecting those would be a
    # regression for real users, not a fix.
    if not TICKER_RE.match((symbol or "").upper()):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")
    rng = range.upper()
    if rng not in RANGE_MAP:
        rng = "1M"
    try:
        data = await asyncio.to_thread(fetch_chart_sync, symbol, rng)
        return data
    except Exception as e:
        logger.warning(f"chart failed for {symbol}: {e}")
        raise HTTPException(status_code=404, detail="Chart unavailable for this ticker")


@api_router.get("/ohlc/{symbol}", dependencies=[Depends(limit_market)])
async def ohlc(symbol: str, range: str = "1M"):
    # Matched on the upper-cased form: case isn't a security property, and the
    # app does request lowercase symbols in places. Rejecting those would be a
    # regression for real users, not a fix.
    if not TICKER_RE.match((symbol or "").upper()):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")
    rng = range.upper()
    if rng not in RANGE_MAP:
        rng = "1M"
    try:
        return await asyncio.to_thread(fetch_ohlc_sync, symbol, rng)
    except Exception as e:
        logger.warning(f"ohlc failed for {symbol}: {e}")
        raise HTTPException(status_code=404, detail="OHLC unavailable for this ticker")


@api_router.get("/news/{symbol}")
async def news(symbol: str, request: Request):
    # Matched on the upper-cased form: case isn't a security property, and the
    # app does request lowercase symbols in places. Rejecting those would be a
    # regression for real users, not a fix.
    if not TICKER_RE.match((symbol or "").upper()):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")
    key = (symbol or "").upper().strip()
    entry = _news_cache.get(key)
    if entry and time.time() - entry["ts"] < 600:
        return {"results": entry["data"]}
    # Limited HERE, after the cache check, not on the route: a cache hit is
    # free (a dict read) and there is no reason to refuse one. Only a MISS is
    # expensive — a Yahoo fetch plus a real LLM classification call — so only a
    # miss is counted. Limiting the route instead would have throttled users
    # reading the same cached headlines while doing nothing about the cost.
    await rl.enforce("news_llm", client_ip(request), NEWS_LLM_PER_MIN, 60)
    # And a whole-deployment ceiling on those LLM calls, because a distributed
    # source asking for a different symbol each time misses the cache every
    # time and no per-IP rule sees it.
    await rl.global_enforce("news_llm_global", NEWS_LLM_GLOBAL_PER_MIN, 60)
    try:
        items = await asyncio.to_thread(fetch_news_sync, symbol)
        items = await tag_news_sentiment(key, items)
        # Unbounded growth means one entry per distinct symbol ever asked
        # for, forever, on an endpoint that needs no login and makes a real
        # LLM call on a miss. Evict the oldest once it gets large.
        if len(_news_cache) >= 500:
            for stale in sorted(_news_cache, key=lambda k: _news_cache[k]["ts"])[:100]:
                _news_cache.pop(stale, None)
        _news_cache[key] = {"ts": time.time(), "data": items}
        return {"results": items}
    except Exception as e:
        logger.warning(f"news failed for {symbol}: {e}")
        return {"results": []}



@api_router.get("/trending", dependencies=[Depends(limit_market)])
async def trending():
    return {"results": await get_market("trending")}


@api_router.get("/markets/{category}", dependencies=[Depends(limit_market)])
async def markets(category: str):
    return {"results": await get_market(category)}

