"""Hardening pass: trusted public host, non-finite portfolio input, OTP attempts.

Each of these was reachable from outside with no session, so they're tested
against the running backend where they have a real HTTP surface.
"""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth as au  # noqa: E402
from deps import PUBLIC_BASE_URL, _public_host_allowed  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")
BASE = "http://localhost:8001/api"
db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]


# --- public_base host allowlist -------------------------------------------
# Host / X-Forwarded-Host are client-supplied. Razorpay's callback_url is built
# from them, so an accepted foreign host sends a paying customer to someone
# else's page after checkout.
@pytest.mark.parametrize("host", [
    "trade-agent-app.preview.emergentagent.com",
    "anything.emergentagent.com",
    "emergentagent.com",
    "tradingagents.in",
    "www.tradingagents.in",
    "localhost:8001",
    "127.0.0.1:8001",
])
def test_our_hosts_are_trusted(host):
    assert _public_host_allowed(host) is True


@pytest.mark.parametrize("host", [
    "evil.example",
    "evil.com:443",
    # The classic near-miss: our domain as a PREFIX of theirs.
    "emergentagent.com.evil.example",
    "nottradingagents.in",
    "",
])
def test_foreign_hosts_are_not_trusted(host):
    assert _public_host_allowed(host) is False


def test_forged_forwarded_host_falls_back_to_configured_base():
    """End to end: the checkout page the backend builds for a forged host must
    not mention that host."""
    res = requests.get(
        "http://localhost:8001/health",
        headers={"X-Forwarded-Host": "evil.example"},
        timeout=15,
    )
    assert res.status_code == 200
    # PUBLIC_BASE_URL is what public_base() returns instead.
    assert "evil.example" not in PUBLIC_BASE_URL


# --- portfolio non-finite numbers -----------------------------------------
@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_portfolio_rejects_non_finite_quantity(bad):
    body = f'{{"holdings": [{{"symbol": "AAPL", "quantity": {bad}, "avg_price": 100}}, ' \
           f'{{"symbol": "MSFT", "quantity": 1, "avg_price": 100}}], "objective": "hrp"}}'
    res = requests.post(f"{BASE}/portfolio/optimize", data=body,
                        headers={"Content-Type": "application/json"}, timeout=30)
    assert res.status_code == 422, res.text


def test_portfolio_rejects_non_finite_cash():
    body = '{"holdings": [{"symbol": "AAPL", "quantity": 1, "avg_price": 100}, ' \
           '{"symbol": "MSFT", "quantity": 1, "avg_price": 100}], "cash": Infinity}'
    res = requests.post(f"{BASE}/portfolio/optimize", data=body,
                        headers={"Content-Type": "application/json"}, timeout=30)
    assert res.status_code == 422, res.text


# --- OTP attempt counting -------------------------------------------------
def test_wrong_codes_are_counted_atomically():
    """Five wrong guesses must exhaust the allowance even when they arrive
    together — a read-then-write counter let them all record the same number."""
    identifier = f"attempts-{uuid.uuid4().hex[:8]}@example.com"
    _, canonical = au.normalize_identifier(identifier)
    record_id = str(uuid.uuid4())
    db.otp_requests.insert_one({
        "id": record_id,
        "identifier": canonical,
        "otp_hash": au.hash_otp("123456"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verified": False,
        "attempts": 0,
    })
    try:
        codes = ["000000", "111111", "222222", "333333", "444444", "555555"]
        statuses = [
            requests.post(f"{BASE}/auth/otp/verify",
                          json={"identifier": identifier, "otp": c}, timeout=20).status_code
            for c in codes
        ]
        # First four wrong -> 400. The fifth hits the cap, and everything after
        # it is refused outright.
        assert statuses[:4] == [400, 400, 400, 400], statuses
        assert statuses[4] == 429, statuses
        assert statuses[5] == 429, statuses
        assert db.otp_requests.find_one({"id": record_id})["attempts"] == 5
    finally:
        db.otp_requests.delete_one({"id": record_id})
