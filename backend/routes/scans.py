"""Stock scanners: read-only, public, cheap — no LLM, no billing.

The lists are computed in the background by scan_jobs.py and read from Mongo
here. Every response says how old the data is (`as_of`), where it came from
(`source`) and whether it is delayed, so the app never shows an old list as live.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

import scanner as sc
from core import db
from limits import limit_market

api_router = APIRouter(prefix="/api")


def _catalog() -> dict:
    return {c["id"]: c for c in sc.scan_catalog()}


def _meta(entry: dict, doc: dict | None) -> dict:
    doc = doc or {}
    return {
        **entry,
        "count": len(doc.get("items") or []),
        "as_of": doc.get("as_of"),
        "source": doc.get("source"),
        "delayed": bool(doc.get("delayed")),
        "note": doc.get("note"),
        # Daily lists: the trading day whose close they describe.
        "session_date": doc.get("session_date"),
        # True while a daily list is recomputed from today's live price.
        "provisional": bool(doc.get("provisional")),
    }


@api_router.get("/scans", dependencies=[Depends(limit_market)])
async def list_scans():
    catalog = _catalog()
    docs = {d["_id"]: d async for d in db.scan_results.find({}, {"items": 1, "as_of": 1, "source": 1, "delayed": 1,
                                                                    "note": 1, "session_date": 1,
                                                                    "provisional": 1})}
    vix = await db.scan_meta.find_one({"_id": "india_vix"})
    return {
        "india_market_open": sc.session_fraction(datetime.now(timezone.utc)) is not None,
        # {value, change_pct, as_of, source, delayed}, or None before the first fetch.
        "india_vix": {k: v for k, v in vix.items() if k != "_id"} if vix else None,
        "scans": [_meta(entry, docs.get(sid)) for sid, entry in catalog.items()],
    }


@api_router.get("/scans/{scan_id}", dependencies=[Depends(limit_market)])
async def get_scan(scan_id: str):
    entry = _catalog().get(scan_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Unknown scan")
    doc = await db.scan_results.find_one({"_id": scan_id})
    group = entry["group"]
    items = [{**it, "analyze_symbol": sc.yahoo_symbol(group, it["symbol"])} for it in (doc or {}).get("items") or []]
    return {**_meta(entry, doc), "items": items}
