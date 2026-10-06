"""POST /api/portfolio/import against the running backend (real Yahoo search).

Each test signs in as its own fresh account: the import limiter is per account
(3/min), so sharing one would make the tests rate-limit each other.
"""
import os
import uuid
from datetime import datetime, timezone

import requests

import auth_helper as ah

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://trade-agent-app.preview.emergentagent.com").rstrip("/")
URL = f"{BASE_URL}/api/portfolio/import"


def fresh_token() -> str:
    uid = f"test-import-{uuid.uuid4()}"
    ah._db.users.insert_one({
        "id": uid, "phone": None, "email": f"{uid}@example.com", "google_sub": None, "apple_sub": None,
        "consent": {"agreed": True, "agreed_at": "2026-01-01T00:00:00+00:00", "version": "1.1"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return ah.au.create_session_token(uid, ah._JWT_SECRET)


def upload(name: str, data: bytes, token: str | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return requests.post(URL, files={"file": (name, data)}, headers=headers, timeout=60)


def test_needs_a_session():
    assert upload("h.csv", b"Name,Qty,Price\nTCS,1,1\n").status_code == 401


def test_resolves_symbols_and_names_and_flags_bad_rows():
    csv = (b"Name,Quantity,Avg Price\n"
           b"RELIANCE.NS,10,2450\n"
           b"Tata Consultancy Services,5,3800\n"
           b"Zero Qty Ltd,0,10\n")
    r = upload("holdings.csv", csv, fresh_token())
    assert r.status_code == 200, r.text
    rows = {row["input"]: row for row in r.json()["rows"]}

    assert rows["RELIANCE.NS"]["status"] == "ok"
    assert rows["RELIANCE.NS"]["symbol"] == "RELIANCE.NS"
    assert rows["RELIANCE.NS"]["quantity"] == 10 and rows["RELIANCE.NS"]["avg_price"] == 2450

    tcs = rows["Tata Consultancy Services"]
    assert tcs["status"] in ("ok", "check")
    assert "TCS.NS" in [c["symbol"] for c in tcs["candidates"]]

    bad = rows["Zero Qty Ltd"]
    assert bad["status"] == "invalid" and bad["error"] == "Quantity must be a number above 0"
    assert r.json()["counts"]["invalid"] == 1


def test_unsupported_format_gets_a_readable_400():
    r = upload("holdings.xls", b"\xd0\xcf\x11\xe0junk", fresh_token())
    assert r.status_code == 400
    assert r.json()["detail"] == "Please save the sheet as .xlsx or CSV and upload that."


def test_oversized_upload_is_refused_with_413():
    big = b"Name,Qty,Price\n" + b"A" * 2_000_000
    r = upload("big.csv", big, fresh_token())
    assert r.status_code == 413
    assert r.json()["detail"] == "That file is too large (1 MB max)."


def test_anonymous_upload_is_refused_before_the_body_matters():
    # Even a body far over the cap gets the 401, not a 413: the session check
    # runs before any of the upload is read.
    assert upload("big.csv", b"A" * 2_000_000).status_code == 401
