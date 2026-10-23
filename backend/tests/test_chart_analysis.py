"""Chart screenshot analysis: image checks, reading normalisation, the
chart-vs-data comparison, the context handed to the desk, and the upload
endpoint — offline, with the vision call and the database replaced."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chart_reader as cr  # noqa: E402
from routes import chart as chart_routes  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\0" * 64
WEBP = b"RIFF\0\0\0\0WEBPVP8 " + b"\0" * 64


# --- image type ----------------------------------------------------------------

def test_image_type_comes_from_the_bytes():
    assert cr.sniff_image(PNG) == "image/png"
    assert cr.sniff_image(JPG) == "image/jpeg"
    assert cr.sniff_image(WEBP) == "image/webp"
    assert cr.sniff_image(b"%PDF-1.7") is None
    assert cr.sniff_image(b"GIF89a") is None
    assert cr.sniff_image(b"") is None


# --- reading normalisation -------------------------------------------------------

def test_reading_is_coerced_into_a_bounded_shape():
    r = cr.normalize_reading({
        "is_chart": True, "symbol": "reliance", "company": "Reliance Industries", "exchange": "NSE",
        "timeframe": "1D", "trend": "UP", "patterns": ["ascending triangle", "", None, "x" * 99, "e", "f"],
        "support": ["1,160.5", -3, "abc", 1100], "resistance": [1285.3], "indicators": {"RSI(14)": "68.2", "junk": "n/a"},
        "last_price": "1,184.50", "observations": "  a   chart  ",
    })
    assert r["symbol"] == "RELIANCE" and r["trend"] == "up" and r["timeframe"] == "1D"
    assert r["support"] == [1160.5] and r["resistance"] == [1285.3]  # only the first 3 are read
    assert r["indicators"] == {"RSI(14)": 68.2}
    assert r["last_price"] == 1184.5 and r["observations"] == "a chart"
    assert len(r["patterns"]) <= 4 and all(len(p) <= 40 for p in r["patterns"])


def test_garbage_from_the_model_becomes_empty_not_an_error():
    r = cr.normalize_reading({"is_chart": "yes", "symbol": "DROP TABLE;", "patterns": "not a list"})
    assert r["is_chart"] is False          # only a real true counts
    assert r["symbol"] is None and r["patterns"] == []
    assert cr.normalize_reading(None)["is_chart"] is False
    assert cr._first_json("Sure! ```json\n{\"is_chart\": true,}\n```") == {"is_chart": True}


def test_search_prefers_the_ticker():
    assert cr.search_query({"symbol": "TCS", "company": "Tata Consultancy"}) == "TCS"
    assert cr.search_query({"symbol": None, "company": "Tata Consultancy"}) == "Tata Consultancy"


# --- against the data --------------------------------------------------------------

TECH = {"price": 1184.5, "rsi14": 52.0, "return_1m": 0.06, "support_20d": 1160.0, "resistance_20d": 1290.0}


def test_comparison_flags_a_misread_rsi_and_agrees_on_the_rest():
    reading = cr.normalize_reading({"is_chart": True, "trend": "up", "indicators": {"RSI(14)": 70},
                                    "support": [1158], "resistance": [1300], "last_price": 1186})
    rows = {r["item"]: r for r in cr.compare_with_data(reading, TECH, 1184.5)}
    assert rows["RSI"]["agrees"] is False and "timeframe" in rows["RSI"]["note"]
    assert rows["Trend"]["agrees"] is True
    assert rows["Support"]["agrees"] is True and rows["Resistance"]["agrees"] is True
    assert rows["Price on the chart"]["agrees"] is True


def test_a_far_off_price_warns_about_an_old_or_wrong_chart():
    reading = cr.normalize_reading({"is_chart": True, "last_price": 600})
    row = cr.compare_with_data(reading, TECH, 1184.5)[0]
    assert row["agrees"] is False and "different asset" in row["note"]


def test_nothing_to_compare_gives_nothing():
    assert cr.compare_with_data(None, TECH, 1) == []
    assert cr.compare_with_data({"is_chart": True}, None, 1) == []


def test_context_block_marks_the_reading_unverified():
    block = cr.context_block(cr.normalize_reading({"is_chart": True, "timeframe": "1D", "trend": "down",
                                                   "patterns": ["head and shoulders"], "support": [1160]}))
    assert "UNVERIFIED" in block and "computed numbers are correct" in block
    assert "head and shoulders" in block and "1160" in block
    assert cr.context_block(None) is None


def test_the_reading_prompt_guards_privacy_and_advice():
    s = cr.READ_SYS.lower()
    assert "personal information" in s and "never as an instruction" in s and "do not advise" in s


# --- the upload endpoint -----------------------------------------------------------

def multipart(data: bytes, filename="chart.png", ctype="image/png"):
    boundary = "XBOUNDARYX"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {"type": "http", "method": "POST", "path": "/api/chart/read", "headers": [
        (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
        (b"content-length", str(len(body)).encode())]}
    return Request(scope, receive)


class _Coll:
    def __init__(self):
        self.docs = []

    async def insert_one(self, doc):
        self.docs.append(doc)

    async def find_one(self, q):
        return next((d for d in self.docs if all(d.get(k) == v for k, v in q.items())), None)


@pytest.fixture
def env(monkeypatch):
    db = SimpleNamespace(chart_readings=_Coll())
    monkeypatch.setattr(chart_routes, "db", db)
    calls = {"read": 0}

    async def fake_read(data):
        calls["read"] += 1
        return cr.normalize_reading({"is_chart": True, "symbol": "RELIANCE", "timeframe": "1D"})
    monkeypatch.setattr(chart_routes.cr, "read_chart", fake_read)
    monkeypatch.setattr(chart_routes, "search_sync", lambda q: [
        {"symbol": "RELIANCE.NS", "name": "Reliance Industries", "exchange": "NSE"},
        {"symbol": "RELIANCE.BO", "name": "Reliance Industries", "exchange": "BSE"}])
    return SimpleNamespace(db=db, calls=calls, monkeypatch=monkeypatch)


USER = {"id": "u1"}


def test_a_chart_is_read_stored_without_the_image_and_offered_for_confirmation(env):
    out = asyncio.run(chart_routes.chart_read(multipart(PNG), USER))
    assert out["is_chart"] is True and out["reading"]["symbol"] == "RELIANCE"
    assert [c["symbol"] for c in out["candidates"]] == ["RELIANCE.NS", "RELIANCE.BO"]
    stored = env.db.chart_readings.docs[0]
    assert stored["owner"] == "u1" and stored["id"] == out["reading_id"]
    assert set(stored) == {"id", "owner", "reading", "created_at", "expires_at"}   # no image bytes


def test_non_images_are_refused_before_any_ai_call(env):
    with pytest.raises(HTTPException) as e:
        asyncio.run(chart_routes.chart_read(multipart(b"%PDF-1.7 hello", "x.pdf", "application/pdf"), USER))
    assert e.value.status_code == 400 and env.calls["read"] == 0


def test_oversized_uploads_are_refused(env):
    env.monkeypatch.setattr(chart_routes.cr, "MAX_IMAGE_BYTES", 100)
    env.monkeypatch.setattr(chart_routes, "_MAX_BODY", 100 + 1024)
    with pytest.raises(HTTPException) as e:
        asyncio.run(chart_routes.chart_read(multipart(PNG + b"\0" * 5000), USER))
    assert e.value.status_code == 413 and env.calls["read"] == 0


def test_not_a_chart_is_told_so_and_nothing_is_stored(env):
    async def not_chart(data):
        return cr.normalize_reading({"is_chart": False})
    env.monkeypatch.setattr(chart_routes.cr, "read_chart", not_chart)
    out = asyncio.run(chart_routes.chart_read(multipart(JPG), USER))
    assert out["is_chart"] is False and "doesn't look like a price chart" in out["message"]
    assert env.db.chart_readings.docs == []


def test_vision_unavailable_is_a_clear_503(env):
    async def unavailable(data):
        raise cr.ChartVisionUnavailable("no images")
    env.monkeypatch.setattr(chart_routes.cr, "read_chart", unavailable)
    with pytest.raises(HTTPException) as e:
        asyncio.run(chart_routes.chart_read(multipart(PNG), USER))
    assert e.value.status_code == 503


def test_readings_belong_to_their_owner_and_expire(env):
    out = asyncio.run(chart_routes.chart_read(multipart(PNG), USER))
    assert asyncio.run(chart_routes.load_reading(out["reading_id"], USER))["symbol"] == "RELIANCE"
    with pytest.raises(HTTPException):
        asyncio.run(chart_routes.load_reading(out["reading_id"], {"id": "someone-else"}))
    with pytest.raises(HTTPException):
        asyncio.run(chart_routes.load_reading(out["reading_id"], None))
    env.db.chart_readings.docs[0]["expires_at"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    with pytest.raises(HTTPException) as e:
        asyncio.run(chart_routes.load_reading(out["reading_id"], USER))
    assert "expired" in e.value.detail
