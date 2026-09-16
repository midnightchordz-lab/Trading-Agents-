"""Apple In-App Purchase (RevenueCat) top-ups.

The webhook is the only thing that can make a wallet balance grow from an iOS
purchase, so these tests pin the three properties that actually protect money:
nothing is credited without the shared secret, nothing is credited for an
event we didn't sell (renewals, Android, unknown product ids, anonymous
users), and a re-delivered event credits exactly once — RevenueCat delivers
at least once, so duplicates are expected, not exceptional.
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

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(BACKEND_DIR.parent / "frontend" / ".env")

import iap  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("PUBLIC_BASE_URL")
    or "http://localhost:8001"
).rstrip("/")
WEBHOOK = f"{BASE_URL}/api/pay/iap/webhook"
SECRET = os.environ.get("REVENUECAT_WEBHOOK_AUTH", "")

_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ["DB_NAME"]]

needs_secret = pytest.mark.skipif(not SECRET, reason="REVENUECAT_WEBHOOK_AUTH not configured")


@pytest.fixture
def wallet_key():
    key = f"user:TEST_iap-{uuid.uuid4()}"
    _db.wallets.insert_one({"device_id": key, "balance": 0.0})
    yield key
    _db.wallets.delete_one({"device_id": key})
    _db.wallet_ledger.delete_many({"wallet_key": key})


def event(**overrides) -> dict:
    base = {
        "id": f"TEST_evt-{uuid.uuid4()}",
        "type": "NON_RENEWING_PURCHASE",
        "store": "APP_STORE",
        "product_id": "credits_10",
        "app_user_id": "user:someone",
        "transaction_id": f"TEST_txn-{uuid.uuid4()}",
        "environment": "SANDBOX",
    }
    base.update(overrides)
    return {"api_version": "1.0", "event": base}


def balance(key: str) -> float:
    return float((_db.wallets.find_one({"device_id": key}) or {}).get("balance") or 0)


class TestProductMapping:
    """The amount credited comes from the immutable Apple product id and
    nowhere else — never from anything the client or the webhook body says."""

    def test_the_three_packs_match_the_razorpay_packs(self):
        import wallet as wal

        assert sorted(iap.PRODUCT_CREDIT.values()) == sorted(wal.TOPUP_PACKS)

    def test_packs_are_listed_cheapest_first(self):
        amounts = [p["amount"] for p in iap.packs()]
        assert amounts == sorted(amounts)


class TestClassifyEvent:
    def test_apple_consumable_purchase_is_credited(self):
        action, detail = iap.classify_event(event(app_user_id="user:abc")["event"])
        assert action == "credit"
        assert detail["amount"] == 10.0
        assert detail["wallet_key"] == "user:abc"

    @pytest.mark.parametrize("overrides", [
        {"type": "INITIAL_PURCHASE"},
        {"type": "RENEWAL"},
        {"type": "TEST"},
        {"store": "PLAY_STORE"},
        {"store": "STRIPE"},
    ])
    def test_events_we_do_not_sell_are_ignored_not_credited(self, overrides):
        action, _ = iap.classify_event(event(**overrides)["event"])
        assert action == "ignore"

    @pytest.mark.parametrize("overrides", [
        {"product_id": "credits_9999"},
        {"product_id": None},
        {"app_user_id": "$RCAnonymousID:abc123"},
        {"app_user_id": "user:"},
        {"app_user_id": None},
        {"transaction_id": ""},
    ])
    def test_unusable_purchases_are_rejected(self, overrides):
        action, _ = iap.classify_event(event(**overrides)["event"])
        assert action == "invalid"


class TestWebhookAuth:
    def test_no_authorization_header_is_rejected(self):
        r = requests.post(WEBHOOK, json=event(), timeout=15)
        assert r.status_code == 401

    def test_wrong_secret_is_rejected(self):
        r = requests.post(WEBHOOK, json=event(), headers={"Authorization": "nope"}, timeout=15)
        assert r.status_code == 401

    @needs_secret
    def test_secret_is_not_leaked_by_the_config_endpoint(self):
        r = requests.get(f"{BASE_URL}/api/pay/iap/config", timeout=15)
        assert r.status_code == 200
        assert SECRET not in r.text


class TestConfigEndpoint:
    def test_lists_the_packs_and_never_a_private_key(self):
        r = requests.get(f"{BASE_URL}/api/pay/iap/config", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert [p["product_id"] for p in data["packs"]] == ["credits_5", "credits_10", "credits_25"]
        assert data["currency"] == "USD"
        # An unconfigured deployment must report disabled AND send no key at
        # all, so the app shows "not switched on" instead of failing inside
        # StoreKit with an empty key.
        if not data["enabled"]:
            assert data["ios_api_key"] == ""


@needs_secret
class TestCrediting:
    def test_valid_purchase_credits_the_mapped_amount(self, wallet_key):
        r = requests.post(
            WEBHOOK,
            json=event(app_user_id=wallet_key, product_id="credits_25"),
            headers={"Authorization": SECRET},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        assert r.json()["credited"] is True
        assert balance(wallet_key) == 25.0

    def test_redelivery_of_the_same_event_credits_once(self, wallet_key):
        payload = event(app_user_id=wallet_key, product_id="credits_5")
        for _ in range(3):
            r = requests.post(WEBHOOK, json=payload, headers={"Authorization": SECRET}, timeout=20)
            assert r.status_code == 200, r.text
        assert balance(wallet_key) == 5.0

    def test_same_transaction_under_a_new_event_id_credits_once(self, wallet_key):
        txn = f"TEST_txn-{uuid.uuid4()}"
        first = event(app_user_id=wallet_key, product_id="credits_10", transaction_id=txn)
        second = event(app_user_id=wallet_key, product_id="credits_10", transaction_id=txn)
        assert first["event"]["id"] != second["event"]["id"]
        for payload in (first, second):
            r = requests.post(WEBHOOK, json=payload, headers={"Authorization": SECRET}, timeout=20)
            assert r.status_code == 200, r.text
        assert balance(wallet_key) == 10.0

    def test_two_separate_purchases_both_credit(self, wallet_key):
        for _ in range(2):
            r = requests.post(
                WEBHOOK,
                json=event(app_user_id=wallet_key, product_id="credits_5"),
                headers={"Authorization": SECRET},
                timeout=20,
            )
            assert r.status_code == 200, r.text
        assert balance(wallet_key) == 10.0

    def test_renewal_event_never_credits(self, wallet_key):
        r = requests.post(
            WEBHOOK,
            json=event(app_user_id=wallet_key, type="RENEWAL"),
            headers={"Authorization": SECRET},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        assert "ignored" in r.json()
        assert balance(wallet_key) == 0.0

    def test_android_purchase_never_credits_the_ios_path(self, wallet_key):
        r = requests.post(
            WEBHOOK,
            json=event(app_user_id=wallet_key, store="PLAY_STORE"),
            headers={"Authorization": SECRET},
            timeout=20,
        )
        assert r.status_code == 200, r.text
        assert balance(wallet_key) == 0.0

    def test_unknown_product_is_a_400_and_credits_nothing(self, wallet_key):
        r = requests.post(
            WEBHOOK,
            json=event(app_user_id=wallet_key, product_id="credits_1000000"),
            headers={"Authorization": SECRET},
            timeout=20,
        )
        assert r.status_code == 400, r.text
        assert balance(wallet_key) == 0.0

    def test_missing_event_id_is_a_400(self):
        payload = event()
        payload["event"].pop("id")
        r = requests.post(WEBHOOK, json=payload, headers={"Authorization": SECRET}, timeout=20)
        assert r.status_code == 400

    def test_malformed_body_is_a_400_not_a_500(self):
        r = requests.post(
            WEBHOOK, data="not json", headers={"Authorization": SECRET, "Content-Type": "application/json"}, timeout=20
        )
        assert r.status_code == 400

    def test_ledger_records_the_source_so_apple_revenue_is_distinguishable(self, wallet_key):
        txn = f"TEST_txn-{uuid.uuid4()}"
        requests.post(
            WEBHOOK,
            json=event(app_user_id=wallet_key, product_id="credits_5", transaction_id=txn),
            headers={"Authorization": SECRET},
            timeout=20,
        )
        row = _db.wallet_ledger.find_one({"payment_id": f"apple:{txn}"})
        assert row is not None
        assert row["source"] == "apple_iap"
        assert row["product_id"] == "credits_5"
        assert row["amount"] == 5.0


def teardown_module():
    _db.wallet_ledger.delete_many({"payment_id": {"$regex": "^apple:TEST_txn-"}})
    _db.webhook_events.delete_many({"event_id": {"$regex": "^rc:TEST_evt-"}})
    _db.wallets.delete_many({"device_id": {"$regex": "^user:TEST_iap-"}})
    _ = datetime.now(timezone.utc)
