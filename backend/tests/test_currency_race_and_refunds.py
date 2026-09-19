"""Finding 15 (currency race) and the refund / chargeback clawback.

The race: two first top-ups arriving together both saw an unlocked wallet and
both wrote a currency. One customer's link had already been created in the
other currency, so a ₹99 payment could add 99 to a USD balance — about $99 of
analyses for ₹99 — or $5 could add 5 to an INR balance.

The clawback: a refunded or charged-back payment left the credit on the
balance, so a refund was a free top-up.
"""
import hashlib
import hmac
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
import iap  # noqa: E402
import routes.payments as pay  # noqa: E402
from tests.async_loop import run_async  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")
BASE = "http://localhost:8001/api"
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-change-me")
WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]

_USERS: list[str] = []
_LEDGER: list[str] = []
_PAYMENTS: list[str] = []
_EVENTS: list[str] = []


def seed_account(balance=0.0, currency=None):
    uid = f"TEST_f15-{uuid.uuid4()}"
    db.users.insert_one({
        "id": uid, "phone": None, "email": f"{uid}@example.com", "identity_type": "email",
        "google_sub": None, "apple_sub": None, "free_credits_remaining": 0,
        "consent": {"agreed": True, "agreed_at": datetime.now(timezone.utc).isoformat(), "version": "1.0"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _USERS.append(uid)
    wallet = {"device_id": f"user:{uid}", "balance": balance}
    if currency:
        wallet["currency"] = currency
    db.wallets.insert_one(wallet)
    return uid, {"Authorization": f"Bearer {au.create_session_token(uid, JWT_SECRET)}"}


def seed_captured_payment(wallet_key, amount, currency, payment_id):
    record = {
        "razorpay_payment_link_id": f"plink_{uuid.uuid4().hex[:14]}",
        "razorpay_order_id": f"order_{uuid.uuid4().hex[:14]}",
        "reference_id": f"wallet_{uuid.uuid4().hex[:20]}",
        "wallet_key": wallet_key, "amount": amount, "currency": currency,
        "status": "captured", "payment_id": payment_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.payments.insert_one(dict(record))
    _PAYMENTS.append(payment_id)
    return record


def post_webhook(event: dict):
    import json as _json
    raw = _json.dumps(event).encode()
    sig = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    event_id = f"test-{uuid.uuid4()}"
    _EVENTS.append(event_id)
    return requests.post(
        f"{BASE}/pay/webhook", data=raw, timeout=25,
        headers={"Content-Type": "application/json",
                 "X-Razorpay-Signature": sig,
                 "X-Razorpay-Event-Id": event_id},
    )


def refund_event(refund_id, payment_id, amount_minor, currency, name="refund.processed"):
    return {
        "event": name,
        "payload": {
            "refund": {"entity": {"id": refund_id, "payment_id": payment_id,
                                  "amount": amount_minor, "currency": currency,
                                  "status": "processed"}},
            "payment": {"entity": {"id": payment_id}},
        },
    }


def teardown_module():
    for uid in _USERS:
        db.users.delete_one({"id": uid})
        db.wallets.delete_one({"device_id": f"user:{uid}"})
        db.wallet_ledger.delete_many({"wallet_key": f"user:{uid}"})
    for pid in _PAYMENTS:
        db.payments.delete_many({"payment_id": pid})
    for lid in _LEDGER:
        db.wallet_ledger.delete_many({"payment_id": lid})
    for eid in _EVENTS:
        db.webhook_events.delete_many({"event_id": eid})


pytestmark = pytest.mark.skipif(not WEBHOOK_SECRET, reason="RAZORPAY_WEBHOOK_SECRET not set")


# --- F15: the currency can only be locked once ---------------------------
def test_second_order_adopts_the_locked_currency_not_its_own_region():
    """The race, serialized: the wallet is locked to INR by the first request,
    so a second request that would have chosen USD must be told INR — the link
    it gets back has to be payable into the wallet that actually exists."""
    uid, headers = seed_account()
    first = requests.post(f"{BASE}/pay/order", headers=headers, timeout=40,
                          json={"amount": 99, "region": "IN", "email": "a@example.com",
                                "phone": "+919812345678"})
    if first.status_code == 502:
        pytest.skip(f"Razorpay unavailable: {first.text}")
    assert first.status_code == 200, first.text
    assert first.json()["currency"] == "INR"
    assert db.wallets.find_one({"device_id": f"user:{uid}"})["currency"] == "INR"

    second = requests.post(f"{BASE}/pay/order", headers=headers, timeout=40,
                           json={"amount": 99, "region": "US", "currency": "USD",
                                 "email": "a@example.com", "phone": "+919812345678"})
    if second.status_code == 502:
        pytest.skip(f"Razorpay unavailable: {second.text}")
    assert second.status_code == 200, second.text
    assert second.json()["currency"] == "INR", "a locked wallet must not be re-priced"
    assert db.wallets.find_one({"device_id": f"user:{uid}"})["currency"] == "INR"
    # $5 is not an INR pack, so the adopted currency is also what gets validated.
    rejected = requests.post(f"{BASE}/pay/order", headers=headers, timeout=40,
                             json={"amount": 5, "region": "US", "email": "a@example.com",
                                   "phone": "+919812345678"})
    assert rejected.status_code == 400
    assert "99" in rejected.json()["detail"]


def test_parallel_claims_produce_exactly_one_currency():
    """The claim itself, hammered: 20 concurrent attempts, half wanting INR and
    half USD, must leave ONE currency and every winner must agree on it."""
    uid, _ = seed_account()
    key = f"user:{uid}"

    async def claim(desired):
        await pay.db.wallets.update_one(
            {"device_id": key}, {"$setOnInsert": {"device_id": key, "balance": 0.0}}, upsert=True)
        claimed = await pay.db.wallets.find_one_and_update(
            {"device_id": key, "currency": {"$in": [None, ""]}},
            {"$set": {"currency": desired}},
            return_document=pay.ReturnDocument.AFTER,
        )
        if claimed:
            return claimed["currency"]
        locked = await pay.db.wallets.find_one({"device_id": key})
        return (locked or {}).get("currency") or desired

    async def race():
        import asyncio
        wanted = ["INR" if i % 2 else "USD" for i in range(20)]
        return await asyncio.gather(*(claim(w) for w in wanted))

    results = run_async(race())
    assert len(set(results)) == 1, results
    assert db.wallets.count_documents({"device_id": key}) == 1
    assert db.wallets.find_one({"device_id": key})["currency"] == results[0]


def test_credit_refuses_a_currency_that_does_not_match_the_wallet():
    """The damage-control half: even if a mismatch were ever produced, the
    money is not added and the payment is flagged instead."""
    uid, _ = seed_account(balance=0.0, currency="USD")
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    record = seed_captured_payment(f"user:{uid}", 99.0, "INR", payment_id)
    _LEDGER.append(payment_id)

    credited = run_async(pay.credit_wallet_once(payment_id, record))
    assert credited is False
    assert db.wallets.find_one({"device_id": f"user:{uid}"})["balance"] == 0.0
    assert db.wallet_ledger.find_one({"payment_id": payment_id}) is None
    flagged = db.payments.find_one({"payment_id": payment_id})
    assert flagged["status"] == "currency_mismatch"
    assert flagged["needs_review"] is True


def test_ledger_records_the_currency_actually_paid():
    """It used to write USD for every row, including ₹ top-ups."""
    uid, _ = seed_account(balance=0.0, currency="INR")
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    record = seed_captured_payment(f"user:{uid}", 99.0, "INR", payment_id)
    _LEDGER.append(payment_id)
    assert run_async(pay.credit_wallet_once(payment_id, record)) is True
    row = db.wallet_ledger.find_one({"payment_id": payment_id})
    assert row["currency"] == "INR"
    assert row["amount"] == 99.0
    assert db.wallets.find_one({"device_id": f"user:{uid}"})["balance"] == 99.0


def test_a_first_topup_into_an_unlabelled_wallet_locks_the_currency():
    uid, _ = seed_account(balance=0.0)
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    record = seed_captured_payment(f"user:{uid}", 99.0, "INR", payment_id)
    _LEDGER.append(payment_id)
    assert run_async(pay.credit_wallet_once(payment_id, record)) is True
    assert db.wallets.find_one({"device_id": f"user:{uid}"})["currency"] == "INR"


# --- refund clawback (Razorpay) ------------------------------------------
def test_full_refund_takes_the_credit_back():
    uid, _ = seed_account(balance=99.0, currency="INR")
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    seed_captured_payment(f"user:{uid}", 99.0, "INR", payment_id)
    refund_id = f"rfnd_{uuid.uuid4().hex[:12]}"
    _LEDGER.append(f"refund:{refund_id}")

    res = post_webhook(refund_event(refund_id, payment_id, 9900, "INR"))
    assert res.status_code == 200, res.text
    assert res.json()["clawed_back"] is True
    assert db.wallets.find_one({"device_id": f"user:{uid}"})["balance"] == 0.0
    row = db.wallet_ledger.find_one({"payment_id": f"refund:{refund_id}"})
    assert row["amount"] == -99.0
    assert row["currency"] == "INR"
    assert row["kind"] == "clawback"
    assert row["shortfall"] == 0.0
    assert db.payments.find_one({"payment_id": payment_id})["status"] == "refunded"


def test_partial_refund_takes_back_only_that_part():
    uid, _ = seed_account(balance=99.0, currency="INR")
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    seed_captured_payment(f"user:{uid}", 99.0, "INR", payment_id)
    refund_id = f"rfnd_{uuid.uuid4().hex[:12]}"
    _LEDGER.append(f"refund:{refund_id}")

    assert post_webhook(refund_event(refund_id, payment_id, 4900, "INR")).status_code == 200
    assert db.wallets.find_one({"device_id": f"user:{uid}"})["balance"] == 50.0
    assert db.payments.find_one({"payment_id": payment_id})["status"] == "partially_refunded"


def test_created_and_processed_for_one_refund_claw_back_once():
    """Razorpay sends both, and either can arrive first."""
    uid, _ = seed_account(balance=99.0, currency="INR")
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    seed_captured_payment(f"user:{uid}", 99.0, "INR", payment_id)
    refund_id = f"rfnd_{uuid.uuid4().hex[:12]}"
    _LEDGER.append(f"refund:{refund_id}")

    first = post_webhook(refund_event(refund_id, payment_id, 9900, "INR", "refund.created"))
    second = post_webhook(refund_event(refund_id, payment_id, 9900, "INR", "refund.processed"))
    assert first.json()["clawed_back"] is True
    assert second.json().get("duplicate") is True
    assert second.json().get("clawed_back") is False
    assert db.wallets.find_one({"device_id": f"user:{uid}"})["balance"] == 0.0
    assert db.wallet_ledger.count_documents({"payment_id": f"refund:{refund_id}"}) == 1


def test_a_spent_balance_goes_to_zero_and_never_negative():
    """The money bought analyses that were already run. We take what is there
    and record the rest — a negative balance would lock the user out of the app
    over someone else's refund."""
    uid, _ = seed_account(balance=10.0, currency="INR")
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    seed_captured_payment(f"user:{uid}", 99.0, "INR", payment_id)
    refund_id = f"rfnd_{uuid.uuid4().hex[:12]}"
    _LEDGER.append(f"refund:{refund_id}")

    body = post_webhook(refund_event(refund_id, payment_id, 9900, "INR")).json()
    assert body["recovered"] == 10.0
    assert body["shortfall"] == 89.0
    assert db.wallets.find_one({"device_id": f"user:{uid}"})["balance"] == 0.0
    row = db.wallet_ledger.find_one({"payment_id": f"refund:{refund_id}"})
    assert row["amount"] == -10.0
    assert row["requested"] == -99.0
    assert row["shortfall"] == 89.0


def test_refund_for_an_unknown_payment_changes_nothing():
    refund_id = f"rfnd_{uuid.uuid4().hex[:12]}"
    res = post_webhook(refund_event(refund_id, f"pay_{uuid.uuid4().hex[:14]}", 9900, "INR"))
    assert res.status_code == 200
    assert res.json()["ignored"] == "unknown payment"
    assert db.wallet_ledger.find_one({"payment_id": f"refund:{refund_id}"}) is None


def test_refund_in_another_currency_is_refused_not_converted():
    uid, _ = seed_account(balance=99.0, currency="INR")
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    seed_captured_payment(f"user:{uid}", 99.0, "INR", payment_id)
    refund_id = f"rfnd_{uuid.uuid4().hex[:12]}"
    res = post_webhook(refund_event(refund_id, payment_id, 500, "USD"))
    assert res.json()["ignored"] == "refund currency mismatch"
    assert db.wallets.find_one({"device_id": f"user:{uid}"})["balance"] == 99.0


def test_chargeback_claws_back_the_whole_payment():
    uid, _ = seed_account(balance=99.0, currency="INR")
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    seed_captured_payment(f"user:{uid}", 99.0, "INR", payment_id)
    _LEDGER.append(f"chargeback:{payment_id}")
    res = post_webhook({
        "event": "payment.dispute.lost",
        "payload": {"payment": {"entity": {"id": payment_id}}},
    })
    assert res.status_code == 200
    assert db.wallets.find_one({"device_id": f"user:{uid}"})["balance"] == 0.0
    assert db.payments.find_one({"payment_id": payment_id})["status"] == "chargeback"


def test_an_unsigned_refund_webhook_is_rejected():
    """The clawback must be no easier to forge than the credit."""
    uid, _ = seed_account(balance=99.0, currency="INR")
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    seed_captured_payment(f"user:{uid}", 99.0, "INR", payment_id)
    res = requests.post(f"{BASE}/pay/webhook", timeout=20,
                        json=refund_event(f"rfnd_{uuid.uuid4().hex[:12]}", payment_id, 9900, "INR"))
    assert res.status_code == 400
    assert db.wallets.find_one({"device_id": f"user:{uid}"})["balance"] == 99.0


# --- refund clawback (Apple / RevenueCat) --------------------------------
def apple_event(event_type, product_id, wallet_key, transaction_id, **extra):
    return {"event": {"id": str(uuid.uuid4()), "type": event_type, "store": "APP_STORE",
                      "product_id": product_id, "app_user_id": wallet_key,
                      "transaction_id": transaction_id, "environment": "PRODUCTION", **extra}}


@pytest.mark.parametrize("event_type", ["CANCELLATION", "REFUND"])
def test_apple_refund_is_classified_as_a_clawback(event_type):
    action, detail = iap.classify_event(
        apple_event(event_type, "credits_5", "user:abc", "txn-1")["event"])
    assert action == "clawback"
    assert detail["transaction_id"] == "txn-1"


def test_unsubscribe_is_not_a_refund():
    action, detail = iap.classify_event(
        apple_event("CANCELLATION", "credits_5", "user:abc", "txn-1",
                    cancel_reason="UNSUBSCRIBE")["event"])
    assert action == "ignore"
    assert "UNSUBSCRIBE" in detail["reason"]


def test_cancellation_of_a_product_we_never_sold_is_ignored():
    action, _ = iap.classify_event(
        apple_event("CANCELLATION", "some_subscription", "user:abc", "txn-1")["event"])
    assert action == "ignore"


def test_purchase_is_still_a_credit():
    action, _ = iap.classify_event(
        apple_event("NON_RENEWING_PURCHASE", "credits_5", "user:abc", "txn-1")["event"])
    assert action == "credit"


def test_apple_refund_reverses_what_was_actually_credited():
    """The amount comes from OUR ledger row, not the current price table — the
    only honest amount to reverse is the one that was added."""
    if not iap.WEBHOOK_AUTH:
        pytest.skip("REVENUECAT_WEBHOOK_AUTH not set")
    uid, _ = seed_account(balance=0.0, currency="INR")
    wallet_key = f"user:{uid}"
    txn = f"apple-txn-{uuid.uuid4().hex[:10]}"
    _LEDGER.append(f"apple:{txn}")
    _LEDGER.append(f"refund:apple:{txn}")
    assert run_async(pay.credit_iap_once(txn, wallet_key, 99.0, "credits_5", "INR", "PRODUCTION")) is True
    assert db.wallets.find_one({"device_id": wallet_key})["balance"] == 99.0

    res = requests.post(f"{BASE}/pay/iap/webhook", timeout=25,
                        headers={"Authorization": iap.WEBHOOK_AUTH},
                        json=apple_event("CANCELLATION", "credits_5", wallet_key, txn))
    assert res.status_code == 200, res.text
    assert res.json()["clawed_back"] is True
    assert db.wallets.find_one({"device_id": wallet_key})["balance"] == 0.0
    assert db.wallet_ledger.find_one({"payment_id": f"refund:apple:{txn}"})["amount"] == -99.0


def test_apple_refund_with_no_credit_on_record_is_a_no_op():
    if not iap.WEBHOOK_AUTH:
        pytest.skip("REVENUECAT_WEBHOOK_AUTH not set")
    uid, _ = seed_account(balance=50.0, currency="USD")
    res = requests.post(f"{BASE}/pay/iap/webhook", timeout=25,
                        headers={"Authorization": iap.WEBHOOK_AUTH},
                        json=apple_event("CANCELLATION", "credits_5", f"user:{uid}",
                                         f"never-credited-{uuid.uuid4().hex[:8]}"))
    assert res.status_code == 200
    assert res.json()["ignored"] == "no credit on record for this transaction"
    assert db.wallets.find_one({"device_id": f"user:{uid}"})["balance"] == 50.0
