"""INR / USD can be chosen by anyone — whatever they signed in with — until
money reaches the wallet.

The currency lock was claimed the moment a checkout OPENED, so an Indian user
who once tapped a pack on a build that guessed USD (or who signed in by email
on a phone set to UTC) was stuck with a cards-only USD checkout forever,
without ever having paid. The lock only has to hold once money is involved:
a balance, any credited payment (refunds claw back in that currency), or a
payment link still open in the old currency.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth as au  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")
BASE = "http://localhost:8001/api"
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-change-me")
client = MongoClient(os.environ["MONGO_URL"])
db = client[os.environ.get("DB_NAME", "test_database")]

_CREATED: list[str] = []


def make_user(email=True):
    uid = f"TEST_curchg-{uuid.uuid4()}"
    db.users.insert_one({
        "id": uid,
        "phone": None,
        "email": f"{uid}@example.com" if email else None,
        "consent": {"agreed": True, "agreed_at": datetime.now(timezone.utc).isoformat(), "version": "1.1"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _CREATED.append(uid)
    return uid, {"Authorization": f"Bearer {au.create_session_token(uid, JWT_SECRET)}"}


def teardown_module():
    for uid in _CREATED:
        key = f"user:{uid}"
        db.users.delete_one({"id": uid})
        db.wallets.delete_one({"device_id": key})
        db.wallet_ledger.delete_many({"wallet_key": key})
        db.payments.delete_many({"wallet_key": key})


def wallet(uid, headers, region="US"):
    return requests.get(f"{BASE}/wallet/balance?device_id=user:{uid}&region={region}",
                        headers=headers, timeout=20).json()


def switch(headers, currency):
    return requests.post(f"{BASE}/wallet/currency", json={"currency": currency}, headers=headers, timeout=20)


def lock(uid, currency="USD", balance=0.0):
    db.wallets.update_one({"device_id": f"user:{uid}"},
                          {"$set": {"device_id": f"user:{uid}", "balance": balance, "currency": currency}},
                          upsert=True)


def test_email_user_outside_india_detection_can_pick_inr():
    uid, headers = make_user()
    before = wallet(uid, headers, region="US")
    assert before["currency"] == "USD" and before["currency_changeable"] is True
    res = switch(headers, "INR")
    assert res.status_code == 200, res.text
    after = wallet(uid, headers, region="US")
    assert after["currency"] == "INR"
    assert after["symbol"] == "₹"
    assert after["packs"] == [99.0, 199.0, 499.0]
    assert after["currency_locked"] is True


def test_abandoned_checkout_lock_can_be_undone():
    """Locked to USD by a checkout that was never paid: no balance, no ledger."""
    uid, headers = make_user()
    lock(uid, "USD")
    assert wallet(uid, headers)["currency_changeable"] is True
    assert switch(headers, "INR").status_code == 200
    assert wallet(uid, headers)["currency"] == "INR"


def test_and_back_again_while_still_empty():
    uid, headers = make_user()
    assert switch(headers, "INR").status_code == 200
    assert switch(headers, "USD").status_code == 200
    assert wallet(uid, headers, region="IN")["currency"] == "USD"


def test_a_balance_is_never_relabelled():
    uid, headers = make_user()
    lock(uid, "USD", balance=12.5)
    assert wallet(uid, headers)["currency_changeable"] is False
    res = switch(headers, "INR")
    assert res.status_code == 409
    body = wallet(uid, headers)
    assert body["currency"] == "USD" and body["balance"] == 12.5


def test_spent_wallet_with_payment_history_stays_put():
    """Balance spent to zero, but a past payment could still be refunded —
    and the clawback is in that payment's currency."""
    uid, headers = make_user()
    lock(uid, "USD", balance=0.0)
    db.wallet_ledger.insert_one({"payment_id": f"pay_TEST_{uuid.uuid4().hex}", "wallet_key": f"user:{uid}",
                                 "amount": 5.0, "currency": "USD", "source": "razorpay",
                                 "created_at": datetime.now(timezone.utc).isoformat()})
    assert wallet(uid, headers)["currency_changeable"] is False
    assert switch(headers, "INR").status_code == 409
    assert wallet(uid, headers)["currency"] == "USD"


def test_open_payment_link_blocks_until_it_expires():
    uid, headers = make_user()
    lock(uid, "USD")
    now = datetime.now(timezone.utc)
    link = {"wallet_key": f"user:{uid}", "status": "created", "amount": 5.0, "currency": "USD",
            "razorpay_order_id": f"plink_TEST_{uuid.uuid4().hex}",
            "expires_at": (now + timedelta(minutes=30)).isoformat()}
    db.payments.insert_one(link)
    res = switch(headers, "INR")
    assert res.status_code == 409 and "payment page open" in res.json()["detail"]
    db.payments.update_one({"razorpay_order_id": link["razorpay_order_id"]},
                           {"$set": {"expires_at": (now - timedelta(minutes=1)).isoformat()}})
    assert switch(headers, "INR").status_code == 200


def test_unknown_currency_rejected():
    uid, headers = make_user()
    assert switch(headers, "EUR").status_code == 400
    assert switch(headers, "").status_code == 400


def test_same_currency_is_a_no_op():
    uid, headers = make_user()
    lock(uid, "INR")
    res = switch(headers, "inr")
    assert res.status_code == 200 and res.json()["changed"] is False
