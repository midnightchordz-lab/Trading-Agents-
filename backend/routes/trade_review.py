"""Trade Review — POST /api/trade-review.

Takes up to three broker statements — PDF, Excel (.xlsx) or CSV (e.g. a
Kotak transaction statement plus the gain/loss statement for the same period,
or a Zerodha tradebook), parses them in memory and
returns the review. Nothing is stored: not the files, not the trades, not the
report. Statements carry the holder's name and PAN; the parsers never read
those into their output.

Pricing: each account's first TRADE_REVIEW_FREE_RUNS (2) reviews are free,
then each run costs the "trade_review" price (₹99 / $1.25) from the wallet.
Nothing is claimed or charged until the report has been built, so a file
that fails to parse never costs anything; the free run is claimed and the
wallet debited with single conditional updates, so two requests at once can
neither share the last free run nor overdraw the balance. Admins, and
deployments with WALLET_ENFORCEMENT_ENABLED off, are not charged.

Same upload hardening as the portfolio import and chart reading: the session
check and every limiter run BEFORE the body is read, the body is read with a
hard byte cap, and the file type comes from the bytes. PDF parsing is CPU
work, so it runs off the event loop, at most two at a time, with a timeout.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import UploadFile

import trade_review as tr
import tradebook_parsers as tp
import tradebook_tabular as ttab
import free_grants as fg
import wallet as wal
from core import db, logger, now_iso
from deps import CONSENT_VERSION, WALLET_ENFORCEMENT_ENABLED, get_current_user, is_admin, wallet_key_for
from limits import limit_trade_review, limit_trade_review_daily, limit_trade_review_global
from routes.payments import suggest_currency
from routes.portfolio import _byte_capped

api_router = APIRouter(prefix="/api")

MAX_FILES = 3
_MAX_BODY = MAX_FILES * tp.MAX_FILE_BYTES + 64 * 1024
_TOO_BIG = "Those files are too large (8 MB each, 3 files max)."
_PARSE_SLOTS = asyncio.Semaphore(2)
_TIMEOUT = 120


async def billing_status(user: dict) -> dict:
    """What the next run will cost this account."""
    key = wallet_key_for(user, None)
    wallet = await db.wallets.find_one({"device_id": key}) or {}
    currency = wallet.get("currency") or suggest_currency(user, None)
    fresh = await db.users.find_one({"id": user["id"]}, {"_id": 0, "trade_review_free_used": 1}) or {}
    used = int(fresh.get("trade_review_free_used") or 0)
    return {
        "charged": WALLET_ENFORCEMENT_ENABLED and not is_admin(user),
        "free_runs": wal.TRADE_REVIEW_FREE_RUNS,
        "free_left": max(0, wal.TRADE_REVIEW_FREE_RUNS - used),
        "price": wal.get_price("trade_review", currency),
        "currency": currency,
        "symbol": wal.currency_symbol_for(currency),
        "balance": round(float(wallet.get("balance") or 0.0), 2),
        "wallet_key": key,
    }


def _payment_needed(st: dict) -> HTTPException:
    return HTTPException(status_code=402, detail=(
        f"Your {st['free_runs']} free reviews are used. Each review is now {st['symbol']}{st['price']:g} — "
        f"your wallet has {st['symbol']}{st['balance']:g}. Top up in the Portfolio tab's wallet and run it again."))


async def claim_run(user: dict, st: dict) -> dict:
    """Use a free run if one is left, else debit the wallet. Atomic either way."""
    if not st["charged"]:
        return {"free_run": True, "charged": None, "symbol": st["symbol"], "free_left": st["free_left"]}
    got_free = await db.users.update_one(
        {"id": user["id"], "$or": [{"trade_review_free_used": {"$exists": False}},
                                   {"trade_review_free_used": {"$lt": wal.TRADE_REVIEW_FREE_RUNS}}]},
        {"$inc": {"trade_review_free_used": 1}},
    )
    if got_free.modified_count == 1:
        return {"free_run": True, "charged": None, "symbol": st["symbol"], "free_left": max(0, st["free_left"] - 1)}
    debit = await db.wallets.update_one(
        {"device_id": st["wallet_key"], "balance": {"$gte": st["price"]}},
        {"$inc": {"balance": -st["price"]}, "$set": {"updated_at": now_iso()}},
    )
    if debit.modified_count != 1:
        raise _payment_needed({**st, "balance": (await billing_status(user))["balance"]})
    return {"free_run": False, "charged": st["price"], "symbol": st["symbol"], "free_left": 0}


@api_router.get("/trade-review/pricing")
async def trade_review_pricing(user: dict = Depends(get_current_user), request: Request = None):
    await fg.claim_deferred(user, request)
    st = await billing_status(user)
    st.pop("wallet_key")
    return st


def _review(blobs: list[bytes]) -> dict:
    parsed = [ttab.parse_any(b) for b in blobs]
    return tr.build_report(parsed)


@api_router.post(
    "/trade-review",
    dependencies=[Depends(limit_trade_review), Depends(limit_trade_review_daily), Depends(limit_trade_review_global)],
)
async def trade_review(request: Request, user: dict = Depends(get_current_user)):
    # A broker statement is personal financial data: the same DPDP consent gate
    # as /analyze, checked before anything is read.
    if not is_admin(user):
        consent = user.get("consent") or {}
        if not (consent.get("agreed") and consent.get("version") == CONSENT_VERSION):
            raise HTTPException(status_code=403, detail="consent_required")
    # Refuse before reading or parsing anything when the run can't be paid for.
    await fg.claim_deferred(user, request)
    st = await billing_status(user)
    if st["charged"] and st["free_left"] == 0 and st["balance"] < st["price"]:
        raise _payment_needed(st)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > _MAX_BODY:
        raise HTTPException(status_code=413, detail=_TOO_BIG)
    capped = Request(request.scope, _byte_capped(request.receive, _MAX_BODY))
    try:
        form = await capped.form(max_files=MAX_FILES, max_fields=MAX_FILES)
    except HTTPException as e:
        if e.status_code == 413:
            raise HTTPException(status_code=413, detail=_TOO_BIG)
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Couldn't read that upload. Try again.")
    try:
        uploads = [u for u in form.getlist("files") if isinstance(u, UploadFile)]
        if not uploads:
            raise HTTPException(status_code=400, detail="No statement was uploaded.")
        blobs = []
        for u in uploads[:MAX_FILES]:
            data = await u.read(tp.MAX_FILE_BYTES + 1)
            if len(data) > tp.MAX_FILE_BYTES:
                raise HTTPException(status_code=413, detail=_TOO_BIG)
            blobs.append(data)
    finally:
        await form.close()

    try:
        async with _PARSE_SLOTS:
            report = await asyncio.wait_for(asyncio.to_thread(_review, blobs), timeout=_TIMEOUT)
    except tp.TradebookError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="That took too long. Try a shorter date range.")
    except Exception:
        logger.exception("trade review failed")
        raise HTTPException(status_code=422, detail="Couldn't review those statements. Upload your broker's "
                                                    "tradebook or P&L as PDF, .xlsx or CSV and try again.")
    finally:
        del blobs  # the statements are never kept
    report["billing"] = await claim_run(user, st)
    logger.info(f"trade review: {len(report['sources'])} file(s), {report['positions']} positions, "
                f"{report['contracts']} contracts, free={report['billing']['free_run']}")
    return report
