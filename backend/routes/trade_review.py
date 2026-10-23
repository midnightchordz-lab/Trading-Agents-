"""Trade Review — POST /api/trade-review.

Takes up to three broker statement PDFs (e.g. a Kotak transaction statement
plus the gain/loss statement for the same period), parses them in memory and
returns the review. Nothing is stored: not the files, not the trades, not the
report. Statements carry the holder's name and PAN; the parsers never read
those into their output.

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
from core import logger
from deps import get_current_user
from limits import limit_trade_review, limit_trade_review_daily, limit_trade_review_global
from routes.portfolio import _byte_capped

api_router = APIRouter(prefix="/api")

MAX_FILES = 3
_MAX_BODY = MAX_FILES * tp.MAX_FILE_BYTES + 64 * 1024
_TOO_BIG = "Those files are too large (8 MB each, 3 files max)."
_PARSE_SLOTS = asyncio.Semaphore(2)
_TIMEOUT = 120


def _review(blobs: list[bytes]) -> dict:
    parsed = [tp.parse(tp.pdf_lines(b)) for b in blobs]
    return tr.build_report(parsed)


@api_router.post(
    "/trade-review",
    dependencies=[Depends(limit_trade_review), Depends(limit_trade_review_daily), Depends(limit_trade_review_global)],
)
async def trade_review(request: Request, user: dict = Depends(get_current_user)):
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
            if not data.startswith(b"%PDF"):
                raise HTTPException(status_code=400, detail="Please upload the PDF statements your broker gives you.")
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
        raise HTTPException(status_code=422, detail="Couldn't review those statements. Check they are the "
                                                    "supported Upstox or Kotak Neo PDFs and try again.")
    finally:
        del blobs  # the statements are never kept
    logger.info(f"trade review: {len(report['sources'])} file(s), {report['positions']} positions, "
                f"{report['contracts']} contracts")
    return report
