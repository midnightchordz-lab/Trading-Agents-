"""The public path, on purpose.

The rest of the suite now calls the backend on the loopback (see conftest.py)
because it is the same process and 70x closer. The one thing that skips is the
ingress: `/api/*` reaching port 8001, headers surviving the proxy, TLS. A
deployed app only ever talks to the backend this way, so it gets its own file
rather than being assumed.
"""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

PUBLIC_BASE = (
    os.environ.get("TEST_PUBLIC_BASE_URL")
    or "https://trade-agent-app.preview.emergentagent.com"
).rstrip("/")


def test_api_prefix_reaches_the_backend_through_the_ingress():
    res = requests.get(f"{PUBLIC_BASE}/api/pay/health", timeout=30)
    assert res.status_code == 200, res.text
    assert "razorpay" in res.json()


def test_market_data_works_through_the_ingress():
    res = requests.get(f"{PUBLIC_BASE}/api/quote/AAPL", timeout=30)
    assert res.status_code == 200, res.text
    assert res.json()["symbol"] == "AAPL"


def test_forwarded_headers_survive_the_proxy():
    """The signup-abuse guard and the OTP per-IP cap both key off the client
    address, which only arrives as an X-Forwarded-For through the ingress."""
    res = requests.get(
        f"{PUBLIC_BASE}/api/pay/health",
        headers={"X-Forwarded-For": "203.0.113.7"},
        timeout=30,
    )
    assert res.status_code == 200


def test_the_real_edge_chain_defeats_a_spoofed_client_address():
    """The one thing only the live ingress can prove: that `client_ip()` picks
    the address OUR edge saw and not the one the caller typed. Verified through
    the stored OTP row rather than a debug endpoint, so nothing extra is
    exposed to do it.

    One email code is requested (the row is written before any send, so this
    holds even if delivery fails). Phone is deliberately not used — that would
    cost an SMS and reach a real handset.
    """
    import uuid

    from pymongo import MongoClient

    db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]
    identifier = f"xffprobe{uuid.uuid4().hex[:8]}@gmail.com"
    spoofed = "203.0.113.250"
    res = requests.post(
        f"{PUBLIC_BASE}/api/auth/otp/request",
        json={"identifier": identifier},
        headers={"X-Forwarded-For": spoofed},
        timeout=40,
    )
    assert res.status_code in (200, 502), res.text  # 502 only if the mailer is down
    row = db.otp_requests.find_one({"identifier": identifier})
    try:
        assert row is not None, "no OTP row was written"
        assert row["ip"] != spoofed, (
            "the spoofed X-Forwarded-For entry was recorded as the client address — "
            "the per-IP OTP ceiling and the signup throttle are bypassable again"
        )
        assert row["ip"], "no client address was derived at all"
    finally:
        db.otp_requests.delete_many({"identifier": identifier})
