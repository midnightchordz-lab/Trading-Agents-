"""Portfolio optimization — reads cached verdicts and price history only.

Never runs the agent pipeline and never mutates an analysis; it is a pure
calculation over what has already been produced.
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import UploadFile
from pydantic import BaseModel, Field

import portfolio_import as pimport
import portfolio_optimizer as pfopt
from limits import limit_portfolio, limit_portfolio_import, limit_portfolio_import_global
from core import db, logger
from market_data import TICKER_RE, _yf_get, fetch_quote_sync, search_sync

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
    # allow_inf_nan=False: JSON permits NaN / Infinity, numpy propagates them
    # through the whole covariance matrix, and the request came back as a 500.
    # Rejecting them at the boundary makes it a 422 with a field name.
    quantity: float = Field(allow_inf_nan=False)
    avg_price: float = Field(allow_inf_nan=False)


class PortfolioOptimizeRequest(BaseModel):
    # Capped because this endpoint needs no session and fans every holding out
    # to two external Yahoo calls — an uncapped list is a free amplifier for
    # anyone who wants to burn our upstream quota. 50 is far beyond any real
    # portfolio a phone screen can show.
    holdings: list[PortfolioHolding] = Field(max_length=50)
    objective: str = "hrp"  # "hrp" | "max_sharpe" | "min_volatility"
    use_agent_views: bool = False
    cash: float = Field(default=0.0, allow_inf_nan=False)  # uninvested cash included in total value


@api_router.post("/portfolio/optimize", dependencies=[Depends(limit_portfolio)])
async def portfolio_optimize(body: PortfolioOptimizeRequest):
    if not body.holdings:
        raise HTTPException(status_code=400, detail="No holdings supplied")
    symbols = [h.symbol.strip().upper() for h in body.holdings]
    if not all(TICKER_RE.match(s) for s in symbols):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol in holdings")
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
    except Exception:
        # The library's own exception text names internal matrices and file
        # paths; it belongs in the log, not in a client response.
        logger.exception("portfolio optimization failed")
        raise HTTPException(
            status_code=422,
            detail="Couldn't optimize this portfolio — try different holdings or another objective.",
        )

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


# --- Holdings import from a spreadsheet (additive) ---
# Parses an uploaded .xlsx/.csv and resolves each stock name to a ticker, but
# saves nothing: holdings live on the device, so the app shows this result as
# a preview and the user confirms which rows to add.
_IMPORT_SEARCH_CONCURRENCY = 5
# The file cap plus room for the multipart envelope (boundaries, part headers).
_IMPORT_MAX_BODY = pimport.MAX_FILE_BYTES + 64 * 1024
_TOO_BIG = "That file is too large (1 MB max)."


def _byte_capped(receive, limit: int):
    """Wrap the ASGI receive channel so reading stops with a 413 the moment the
    body passes `limit` — covers chunked uploads that send no Content-Length."""
    seen = 0

    async def capped():
        nonlocal seen
        message = await receive()
        if message["type"] == "http.request":
            seen += len(message.get("body", b""))
            if seen > limit:
                raise HTTPException(status_code=413, detail=_TOO_BIG)
        return message

    return capped


# The handler takes the raw Request rather than an `UploadFile = File(...)`
# parameter on purpose: with a File parameter FastAPI reads and spools the
# entire upload BEFORE running dependencies, so an anonymous caller could push
# gigabytes at the server before the 401 or the rate limit ever ran. Here the
# session check and both limiters run first, and the body is then read with a
# hard byte cap.
@api_router.post(
    "/portfolio/import",
    dependencies=[Depends(limit_portfolio_import), Depends(limit_portfolio_import_global)],
)
async def portfolio_import_file(request: Request):
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > _IMPORT_MAX_BODY:
        raise HTTPException(status_code=413, detail=_TOO_BIG)
    capped = Request(request.scope, _byte_capped(request.receive, _IMPORT_MAX_BODY))
    try:
        form = await capped.form(max_files=1, max_fields=1)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Couldn't read that upload. Try again.")
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=400, detail="No file was uploaded.")
        filename = upload.filename or ""
        data = await upload.read(pimport.MAX_FILE_BYTES + 1)
    finally:
        await form.close()

    try:
        # Parsing is CPU-bound and synchronous; off the event loop so one
        # upload can't stall every other request.
        rows = await asyncio.to_thread(pimport.parse_holdings_file, filename, data)
    except pimport.HoldingsFileError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("portfolio import: unreadable file")
        raise HTTPException(status_code=400, detail="Couldn't read that file. Try saving it again as .xlsx or CSV.")

    sem = asyncio.Semaphore(_IMPORT_SEARCH_CONCURRENCY)

    async def resolve(entry: dict) -> dict:
        if entry["error"]:
            return {**entry, "status": "invalid", "symbol": None, "candidates": []}
        async with sem:
            try:
                found = await asyncio.to_thread(search_sync, entry["input"])
            except Exception as e:
                logger.warning(f"portfolio import: search failed: {e}")
                return {**entry, "status": "not_found", "symbol": None, "candidates": [],
                        "error": "Couldn't look this up right now — try the exact symbol, e.g. RELIANCE.NS"}
        status, symbol, cands = pimport.choose_symbol(entry["input"], found)
        return {
            **entry,
            "status": status,
            "symbol": symbol,
            "candidates": [{"symbol": c["symbol"], "name": c.get("name"), "exchange": c.get("exchange")} for c in cands],
            "error": "No matching stock found" if status == "not_found" else None,
        }

    resolved = await asyncio.gather(*(resolve(e) for e in rows))
    counts = {k: sum(1 for r in resolved if r["status"] == k) for k in ("ok", "check", "not_found", "invalid")}
    return {"rows": resolved, "counts": counts}
