"""Currency is chosen for the user instead of being asked.

UPI / Google Pay can only settle INR, so a USD payment link physically cannot
offer them. Indian users therefore need an INR wallet, and that has to happen
without a prompt (the prompt was the thing that left a user staring at a
cards-only checkout). These tests pin the decision itself, plus the guarantee
that a locked wallet ignores the region entirely.
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
from routes.payments import suggest_currency  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")
BASE = "http://localhost:8001/api"
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-change-me")
client = MongoClient(os.environ["MONGO_URL"])
db = client[os.environ.get("DB_NAME", "test_database")]

_CREATED: list[str] = []


def make_user(phone=None, email=None):
    uid = f"TEST_cur-{uuid.uuid4()}"
    db.users.insert_one({
        "id": uid,
        "phone": phone,
        "email": email or f"{uid}@example.com",
        "consent": {"agreed": True, "agreed_at": datetime.now(timezone.utc).isoformat(), "version": "1.0"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _CREATED.append(uid)
    return uid, {"Authorization": f"Bearer {au.create_session_token(uid, JWT_SECRET)}"}


def teardown_module():
    for uid in _CREATED:
        db.users.delete_one({"id": uid})
        db.wallets.delete_one({"device_id": f"user:{uid}"})


@pytest.mark.parametrize(
    "user,region,expected",
    [
        ({"phone": "+918446307145"}, None, "INR"),
        ({"phone": "+918446307145"}, "US", "INR"),          # verified phone wins
        ({"billing_phone": "+919812345678"}, "US", "INR"),  # contact given for a receipt
        ({"phone": "+14155550134"}, "IN", "INR"),           # region still counts
        ({"phone": "+14155550134"}, None, "USD"),
        ({}, "in", "INR"),                                   # case-insensitive
        ({}, " IN ", "INR"),
        ({}, "US", "USD"),
        ({}, None, "USD"),
        ({}, "", "USD"),
        (None, None, "USD"),
        ({"phone": None, "billing_phone": None}, None, "USD"),
    ],
)
def test_suggest_currency(user, region, expected):
    assert suggest_currency(user, region) == expected


def test_balance_offers_inr_for_indian_region():
    uid, headers = make_user()
    res = requests.get(f"{BASE}/wallet/balance?device_id=user:{uid}&region=IN", headers=headers, timeout=20)
    body = res.json()
    assert body["currency"] == "INR"
    assert body["symbol"] == "\u20b9"
    assert body["packs"] == [99.0, 199.0, 499.0]
    assert body["currency_locked"] is False


def test_balance_offers_usd_elsewhere():
    uid, headers = make_user()
    res = requests.get(f"{BASE}/wallet/balance?device_id=user:{uid}&region=US", headers=headers, timeout=20)
    body = res.json()
    assert body["currency"] == "USD"
    assert body["packs"] == [5.0, 10.0, 25.0]


def test_indian_phone_gets_inr_without_any_region():
    uid, headers = make_user(phone=f"+9198{uuid.uuid4().int % 10**8:08d}")
    res = requests.get(f"{BASE}/wallet/balance?device_id=user:{uid}", headers=headers, timeout=20)
    assert res.json()["currency"] == "INR"


def test_locked_wallet_ignores_region():
    """The whole point of locking: a real balance must never be reinterpreted.
    A $12.50 wallet stays USD however Indian the device looks."""
    uid, headers = make_user()
    db.wallets.update_one(
        {"device_id": f"user:{uid}"},
        {"$set": {"device_id": f"user:{uid}", "balance": 12.5, "currency": "USD"}},
        upsert=True,
    )
    res = requests.get(f"{BASE}/wallet/balance?device_id=user:{uid}&region=IN", headers=headers, timeout=20)
    body = res.json()
    assert body["currency"] == "USD"
    assert body["currency_locked"] is True
    assert body["balance"] == 12.5
