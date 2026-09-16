"""Sanity checks for wallet balance endpoint after free-credits changes.

Verifies:
 - GET /api/wallet/balance includes free_credits_remaining and enforcement_enabled
 - enforcement_enabled is False while free credits remain
 - POST /api/analyze spends a free credit when actually running, and does
   not spend one on a cache hit
"""
import os
import sys
import time
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from auth_helper import AUTH_HEADERS, USER_ID  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")
BASE_URL = (
    os.environ.get("EXPO_BACKEND_URL")
    or os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ["PUBLIC_BASE_URL"]
).rstrip("/")
_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ.get("DB_NAME", "test_database")]


def _reset_free_credits(n: int = 10) -> None:
    _db.users.update_one({"id": USER_ID}, {"$set": {"free_credits_remaining": n}})


def _get_free() -> int:
    doc = _db.users.find_one({"id": USER_ID}) or {}
    return int(doc.get("free_credits_remaining", 0))


def _get_balance():
    r = requests.get(f"{BASE_URL}/api/wallet/balance", headers=AUTH_HEADERS, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


def test_wallet_balance_shape_with_free_credits():
    _reset_free_credits(10)
    body = _get_balance()
    assert "free_credits_remaining" in body
    assert "enforcement_enabled" in body
    assert body["free_credits_remaining"] == 10
    assert body["enforcement_enabled"] is False


def test_wallet_balance_enforcement_flips_when_free_zero():
    _reset_free_credits(0)
    body = _get_balance()
    assert body["free_credits_remaining"] == 0
    # Enforcement should turn on for non-admin users when no free credits.
    assert body["enforcement_enabled"] is True


def test_analyze_spends_free_credit_and_cache_does_not():
    # Fresh symbol -> real run, credit spent.
    symbol = "IBM"
    # Nuke cached analysis so this is a real run.
    _db.analyses.delete_many({"symbol": symbol, "user_id": USER_ID})
    _db.analyses.delete_many({"symbol": symbol})
    _reset_free_credits(5)
    before = _get_free()
    r = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=AUTH_HEADERS,
        json={"symbol": symbol},
        timeout=60,
    )
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    # Wait until analysis is not running so caching can kick in on repeat.
    for _ in range(60):
        rr = requests.get(f"{BASE_URL}/api/analysis/{aid}", headers=AUTH_HEADERS, timeout=15)
        if rr.status_code == 200 and rr.json().get("status") != "running":
            break
        time.sleep(1)
    mid = _get_free()
    assert mid == before - 1, f"expected 1 credit spent, before={before} after={mid}"

    # Second call should hit cache (same symbol, still fresh) — no spend.
    r2 = requests.post(
        f"{BASE_URL}/api/analyze",
        headers=AUTH_HEADERS,
        json={"symbol": symbol},
        timeout=30,
    )
    assert r2.status_code == 200, r2.text
    after = _get_free()
    assert after == mid, f"cache hit should NOT spend a credit (mid={mid} after={after})"


def test_admin_bypass_does_not_spend_free_credit():
    admin_id = os.environ.get("ADMIN_IDENTIFIERS", "").split(",")[0].strip()
    if not admin_id or not admin_id.startswith("+"):
        pytest.skip("no admin phone configured")
    # Create/pick an admin user, sign a token.
    import auth as au

    admin_user = _db.users.find_one({"phone": admin_id})
    if not admin_user:
        admin_user = {"id": f"admin-{admin_id}", "phone": admin_id, "created_at": "2024-01-01"}
        _db.users.insert_one(admin_user)
    _db.users.update_one({"id": admin_user["id"]}, {"$set": {"free_credits_remaining": 3}})
    token = au.create_session_token(admin_user["id"], os.environ.get("JWT_SECRET", "dev-only-change-me"))
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

    symbol = "INTC"
    _db.analyses.delete_many({"symbol": symbol})
    before = int((_db.users.find_one({"id": admin_user["id"]}) or {}).get("free_credits_remaining", 0))

    r = requests.post(f"{BASE_URL}/api/analyze", headers=headers, json={"symbol": symbol}, timeout=60)
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    for _ in range(60):
        rr = requests.get(f"{BASE_URL}/api/analysis/{aid}", headers=AUTH_HEADERS, timeout=15)
        if rr.status_code == 200 and rr.json().get("status") != "running":
            break
        time.sleep(1)

    after = int((_db.users.find_one({"id": admin_user["id"]}) or {}).get("free_credits_remaining", 0))
    assert after == before, f"admin must not spend free credit (before={before}, after={after})"

    # And balance for admin: is_admin True, enforcement False.
    bal = requests.get(f"{BASE_URL}/api/wallet/balance", headers=headers, timeout=15).json()
    assert bal.get("is_admin") is True
    assert bal.get("enforcement_enabled") is False
