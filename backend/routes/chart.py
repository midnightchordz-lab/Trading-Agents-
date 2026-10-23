"""Chart screenshot reading — step one of "Analyse a chart".

POST /api/chart/read takes one image, reads it with the vision model and
returns what it saw plus candidate assets to confirm. The desk run itself is
the normal POST /api/analyze with `chart_reading_id`, so pricing, wallet and
history work exactly as for any analysis; reading a chart costs nothing.

Same upload hardening as the portfolio import: the session check and every
limiter run BEFORE the body is read, the body is read with a hard byte cap,
and the type comes from the file's own bytes. The image is never stored.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import UploadFile

import chart_reader as cr
from core import db, logger, now_iso
from deps import get_current_user
from limits import limit_chart_read, limit_chart_read_daily, limit_chart_read_global
from market_data import search_sync
from routes.portfolio import _byte_capped

api_router = APIRouter(prefix="/api")

READING_TTL_MINUTES = 30
_MAX_BODY = cr.MAX_IMAGE_BYTES + 64 * 1024
_TOO_BIG = "That image is too large (5 MB max). Crop it to just the chart, or take a smaller screenshot."


@api_router.post(
    "/chart/read",
    dependencies=[Depends(limit_chart_read), Depends(limit_chart_read_daily), Depends(limit_chart_read_global)],
)
async def chart_read(request: Request, user: dict = Depends(get_current_user)):
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > _MAX_BODY:
        raise HTTPException(status_code=413, detail=_TOO_BIG)
    capped = Request(request.scope, _byte_capped(request.receive, _MAX_BODY))
    try:
        form = await capped.form(max_files=1, max_fields=1)
    except HTTPException as e:
        if e.status_code == 413:
            raise HTTPException(status_code=413, detail=_TOO_BIG)
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Couldn't read that upload. Try again.")
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=400, detail="No image was uploaded.")
        data = await upload.read(cr.MAX_IMAGE_BYTES + 1)
    finally:
        await form.close()

    if len(data) > cr.MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=_TOO_BIG)
    if not cr.sniff_image(data):
        raise HTTPException(status_code=400, detail="Please upload a PNG, JPG or WebP screenshot of a chart.")

    try:
        reading = await cr.read_chart(data)
    except cr.ChartVisionUnavailable:
        logger.error("chart read: the installed AI library can't send images")
        raise HTTPException(status_code=503, detail="Chart reading isn't available right now.")
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Reading the chart took too long. Try again.")
    except Exception as e:
        logger.warning(f"chart read failed: {type(e).__name__}")
        raise HTTPException(status_code=502, detail="Couldn't read that chart. Try again in a moment.")
    finally:
        del data  # the image is never kept

    if not reading["is_chart"]:
        return {"is_chart": False, "reading_id": None, "reading": reading, "candidates": [],
                "message": "This doesn't look like a price chart. Try a screenshot that shows the chart itself."}

    candidates = []
    query = cr.search_query(reading)
    if query:
        try:
            found = await asyncio.to_thread(search_sync, query)
            candidates = [{"symbol": c["symbol"], "name": c.get("name"), "exchange": c.get("exchange")}
                          for c in (found or [])[:5] if c.get("symbol")]
        except Exception as e:
            logger.warning(f"chart read: search failed: {e}")

    reading_id = str(uuid.uuid4())
    await db.chart_readings.insert_one({
        "id": reading_id,
        "owner": user["id"],
        "reading": reading,
        "created_at": now_iso(),
        # TTL index (server.py) deletes these; analyze also refuses expired ones.
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=READING_TTL_MINUTES),
    })
    return {"is_chart": True, "reading_id": reading_id, "reading": reading, "candidates": candidates}


async def load_reading(reading_id: str, user: dict | None) -> dict:
    """The caller's own, unexpired chart reading — or a 400 telling them to read it again."""
    doc = await db.chart_readings.find_one({"id": reading_id, "owner": (user or {}).get("id")}) if user else None
    expires = (doc or {}).get("expires_at")
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if not doc or (expires is not None and expires < datetime.now(timezone.utc)):
        raise HTTPException(status_code=400, detail="That chart reading has expired. Upload the chart again.")
    return doc["reading"]
