"""Running an analysis, and reading back the ones you have run.

This is where billing meets the pipeline: the launch-free daily allowance,
free signup credits and the wallet charge are all resolved here, before a
single LLM call is made. History is private per account — see
deps.own_analyses_filter.
"""
import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import wallet as wal
from core import db, logger, now_iso
from deps import (
    LAUNCH_FREE_UNTIL,
    WALLET_ENFORCEMENT_ENABLED,
    CONSENT_VERSION,
    consume_free_credit,
    get_current_user,
    get_free_credits_remaining,
    get_wallet_balance,
    is_admin,
    latest_completed_analysis_for,
    own_analyses_filter,
    owner_hash_for,
    require_user,
    wallet_key_for,
)
from limits import ANALYZE_MAX_CONCURRENT, limit_analyze, limit_poll
from market_data import fetch_quote_sync
from pipeline import SUPPORTED_LANGUAGES, TOTAL_STEPS, run_analysis

api_router = APIRouter(prefix="/api")


class AnalyzeRequest(BaseModel):
    symbol: str
    name: Optional[str] = None
    language: str = "en"
    device_id: Optional[str] = None  # required only when WALLET_ENFORCEMENT_ENABLED

@api_router.post("/analyze", dependencies=[Depends(limit_analyze)])
async def analyze(body: AnalyzeRequest, user: Optional[dict] = Depends(require_user)):
    symbol = (body.symbol or "").strip().upper()
    if not symbol or not re.match(r'^[A-Z0-9.\-\^=]{1,20}$', symbol):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")
    language = body.language if body.language in SUPPORTED_LANGUAGES else "en"

    # A per-minute limit alone doesn't bound the real cost here: an analysis is
    # a long multi-agent LLM run, so six starts in a minute can leave six runs
    # executing at once for minutes afterwards. This caps what a single account
    # can have IN FLIGHT.
    #
    # Counted only over the last 10 minutes on purpose: a run whose process
    # died mid-pipeline stays `status: running` forever, and without the window
    # three of those would lock the account out of analysis permanently — a
    # self-inflicted denial of service that the user could never clear. It is
    # matched on `owner_hash`, the same HMAC the private-history filter uses,
    # so no identity is written onto an analysis to make this work.
    if user:
        since = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        in_flight = await db.analyses.count_documents({
            "owner_hash": owner_hash_for(user),
            "status": "running",
            "created_at": {"$gt": since},
        })
        if in_flight >= ANALYZE_MAX_CONCURRENT:
            raise HTTPException(
                status_code=429,
                detail=f"You already have {in_flight} analyses running — wait for one to finish.",
                headers={"Retry-After": "30"},
            )

    # During a configured launch-free window, everyone gets a capped daily
    # allowance instead of unlimited free runs — unlimited-free has real,
    # unbounded cost exposure (nothing stops scripted abuse while analyses
    # cost nothing); the cap closes that gap while staying generous. Tracked
    # on the wallet doc, not on analyses — a count, not which tickers were
    # run, so this doesn't touch the "analyses aren't linked to identity"
    # privacy commitment at all. Once the day's allowance is spent the request
    # falls through to normal billing (free credits, then wallet), it is not a
    # hard block.
    launch_free_now = wal.is_launch_free_period(LAUNCH_FREE_UNTIL, datetime.now(timezone.utc))
    launch_free_daily_ok = False
    if launch_free_now:
        launch_key = wallet_key_for(user, body.device_id)
        if not launch_key:
            raise HTTPException(status_code=400, detail="device_id is required")
        today_str = datetime.now(timezone.utc).date().isoformat()
        wallet_doc = await db.wallets.find_one({"device_id": launch_key}) or {}
        reset_date, reset_count = wal.launch_free_daily_state(
            wallet_doc.get("launch_free_daily_date", ""),
            wallet_doc.get("launch_free_daily_count", 0),
            today_str,
        )
        launch_free_daily_ok = wal.has_launch_free_daily_quota(reset_count)
        new_count = reset_count + 1 if launch_free_daily_ok else reset_count
        await db.wallets.update_one(
            {"device_id": launch_key},
            {"$set": {
                "launch_free_daily_date": reset_date,
                "launch_free_daily_count": new_count,
                "updated_at": now_iso(),
            }},
            upsert=True,
        )
    admin_bypass = is_admin(user) or launch_free_daily_ok

    # Consent is about personal data — an anonymous device-only user (no
    # sign-in) hasn't given us any, so this only applies to signed-in
    # accounts, and admin/reviewer accounts aren't real end-users whose
    # DPDP rights are in play here. Gated on is_admin specifically, NOT on
    # admin_bypass: a launch-free user is a real end-user, and a promotion
    # must never quietly switch a legal gate off.
    if user and not is_admin(user):
        consent = user.get("consent") or {}
        if not (consent.get("agreed") and consent.get("version") == CONSENT_VERSION):
            raise HTTPException(status_code=403, detail="consent_required")

    used_free_credit = False
    billed = WALLET_ENFORCEMENT_ENABLED and not admin_bypass
    if billed:
        wkey = wallet_key_for(user, body.device_id)
        if not wkey:
            raise HTTPException(status_code=400, detail="device_id is required")

        cached = await latest_completed_analysis_for(symbol, language)
        cached_verdict = cached["verdict"] if cached else None
        reference_price = (cached.get("quote") or {}).get("price") if cached else None
        live_quote = None
        try:
            live_quote = await asyncio.to_thread(fetch_quote_sync, symbol)
        except Exception as e:
            logger.warning(f"pricing quote fetch failed for {symbol}: {e}")
        live_price = live_quote.get("price") if live_quote else None

        if not wal.should_charge_for_recheck(cached_verdict, live_price, reference_price):
            # Nothing has meaningfully changed since the cached verdict —
            # serve it for free. No new analysis document, no LLM calls.
            # Billing fields must describe THIS request, not the original
            # run's (which may have been another account or an admin).
            # The caller is marked as a viewer so this still shows in their
            # own (now private) history and stays openable by them.
            viewer_hash = owner_hash_for(user)
            if viewer_hash:
                await db.analyses.update_one(
                    {"id": cached["id"]}, {"$addToSet": {"viewer_hashes": viewer_hash}}
                )
            return {**cached, "served_from_cache": True, "billed": False,
                    "price_charged": None, "admin_bypass": admin_bypass,
                    "used_free_credit": False,
                    "free_credits_remaining": await get_free_credits_remaining(user)}

        # Free signup credits are spent before any money is. Only reached
        # when a fresh run is actually needed, so an unchanged re-check
        # never burns one.
        if wal.should_use_free_credit(await get_free_credits_remaining(user)) and await consume_free_credit(user):
            used_free_credit = True
            billed = False
        else:
            wallet_doc = await db.wallets.find_one({"device_id": wkey})
            currency = (wallet_doc or {}).get("currency") or "USD"
            price = wal.get_price("full_analysis", currency)
            # Debited with ONE conditional update, never read-then-write: two
            # requests arriving together would otherwise both read the same
            # balance, both pass the check, and both write the same reduced
            # value — funding two paid runs off one charge. The `$gte` filter
            # makes "can they afford it" and "take it" the same operation, so
            # exactly one of the two can win.
            charged = await db.wallets.update_one(
                {"device_id": wkey, "balance": {"$gte": price}},
                {"$inc": {"balance": -price}, "$set": {"updated_at": now_iso()}},
            )
            if charged.modified_count != 1:
                balance = await get_wallet_balance(wkey)
                raise HTTPException(
                    status_code=402,
                    detail=(f"Insufficient balance: need {wal.currency_symbol_for(currency)}{price:.2f}, "
                            f"have {wal.currency_symbol_for(currency)}{balance:.2f}"),
                )

    analysis = {
        "id": str(uuid.uuid4()),
        "symbol": symbol,
        "name": (body.name or symbol),
        "language": language,
        "status": "running",
        "messages": [],
        "quote": None,
        "verdict": None,
        "debate": None,
        "timeframes": None,
        "current_step": 0,
        "total_steps": TOTAL_STEPS,
        "error": None,
        "billed": billed,
        # `currency` is only ever bound in the branch that actually charged;
        # the outer conditional short-circuits for admin / free-credit runs,
        # where no real currency was resolved for this request.
        "price_charged": wal.get_price("full_analysis", currency if billed else "USD") if billed else None,
        "admin_bypass": admin_bypass if WALLET_ENFORCEMENT_ENABLED else False,
        "launch_free_active": launch_free_daily_ok if WALLET_ENFORCEMENT_ENABLED else False,
        "used_free_credit": used_free_credit,
        "free_credits_remaining": await get_free_credits_remaining(
            await db.users.find_one({"id": user["id"]}, {"_id": 0}) if user else None
        ),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    # Stored alongside, never returned to the client: lets the owner delete
    # their own record without putting an identity on the analysis itself.
    await db.analyses.insert_one({**analysis, "owner_hash": owner_hash_for(user)})
    asyncio.create_task(run_analysis(analysis["id"], symbol, language))
    return analysis


@api_router.get("/analysis/{analysis_id}", dependencies=[Depends(limit_poll)])
async def get_analysis(analysis_id: str, user: dict = Depends(get_current_user)):
    doc = await db.analyses.find_one(
        {"id": analysis_id, **own_analyses_filter(user)}, {"_id": 0, "owner_hash": 0, "viewer_hashes": 0}
    )
    if not doc:
        # Deliberately 404, not 403: an id that exists but belongs to someone
        # else must be indistinguishable from one that doesn't exist.
        raise HTTPException(status_code=404, detail="Analysis not found")
    return doc


@api_router.get("/history", dependencies=[Depends(limit_poll)])
async def history(user: dict = Depends(get_current_user)):
    docs = await db.analyses.find(
        own_analyses_filter(user), {"_id": 0, "messages": 0, "owner_hash": 0, "viewer_hashes": 0}
    ).sort("created_at", -1).to_list(100)
    return {"results": docs}


@api_router.delete("/analysis/{analysis_id}")
async def delete_analysis(analysis_id: str, user: dict = Depends(get_current_user)):
    """Removes an analysis from the caller's own history.

    A run the caller started is deleted outright. A run that merely *appeared*
    in their history because an unchanged cached verdict was served to them
    belongs to another account, so only the caller's viewer mark is removed —
    deleting the shared document would destroy someone else's record and the
    free-re-check cache along with it."""
    h = owner_hash_for(user)
    doc = await db.analyses.find_one(
        {"id": analysis_id, **own_analyses_filter(user)}, {"_id": 0, "id": 1, "owner_hash": 1}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if doc.get("owner_hash") == h:
        await db.analyses.delete_one({"id": analysis_id})
    else:
        await db.analyses.update_one({"id": analysis_id}, {"$pull": {"viewer_hashes": h}})
    return {"ok": True}

