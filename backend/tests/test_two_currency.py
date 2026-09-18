"""Two currencies (USD / INR), locked once per account.

The whole point of INR is UPI: UPI settles INR only, so a Payment Link created
with `currency: "INR"` shows UPI automatically and a USD one cannot, no matter
what the Razorpay account settings say. Setting the currency correctly IS the
payment-method mechanism — there is no "enable UPI" code to test.

The case these tests exist for is the dangerous one: an account that already
holds a real, already-earned balance with no currency field yet (i.e. from
before this feature) must be locked to USD automatically and must never be
allowed to lock into INR later, which would silently reinterpret a $12.50
balance as ₹12.50.
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

import auth as au  # noqa: E402
import iap  # noqa: E402
import wallet as wal  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("PUBLIC_BASE_URL")
    or "http://localhost:8001"
).rstrip("/")
JWT_SECRET = os.environ["JWT_SECRET"]
IAP_SECRET = os.environ.get("REVENUECAT_WEBHOOK_AUTH", "")
PAYMENTS_LIVE = bool(os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET"))

_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ["DB_NAME"]]

needs_payments = pytest.mark.skipif(not PAYMENTS_LIVE, reason="Razorpay keys not configured")
needs_iap_secret = pytest.mark.skipif(not IAP_SECRET, reason="REVENUECAT_WEBHOOK_AUTH not configured")


def mk_user(*, balance=None, currency=None, free_credits=0):
    """A signed-in account with both contact fields (Razorpay links require
    email AND phone), and a wallet in whatever state the test needs."""
    uid = f"TEST_cur-{uuid.uuid4()}"
    _db.users.insert_one({
        "id": uid,
        "email": f"{uid}@example.com",
        "phone": f"+1555{uuid.uuid4().int % 10_000_000:07d}",
        "free_credits_remaining": free_credits,
        "consent": {"agreed": True, "agreed_at": "2026-01-01T00:00:00+00:00", "version": "1.0"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    doc = {"device_id": f"user:{uid}"}
    if balance is not None:
        doc["balance"] = balance
    if currency is not None:
        doc["currency"] = currency
    if len(doc) > 1:
        _db.wallets.insert_one(doc)
    tok = au.create_session_token(uid, JWT_SECRET)
    return uid, {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}


def wallet_doc(uid):
    return _db.wallets.find_one({"device_id": f"user:{uid}"}) or {}


def order(headers, amount, currency=None):
    body = {"amount": amount}
    if currency is not None:
        body["currency"] = currency
    return requests.post(f"{BASE_URL}/api/pay/order", headers=headers, json=body, timeout=30)


# ---------------------------------------------------------------------------
# Pure wallet.py behaviour
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """Acceptance criterion 7: every call with no currency argument must behave
    exactly as it did before this feature existed."""

    def test_get_price_defaults_to_usd(self):
        for action in ("full_analysis", "compare", "portfolio_optimize"):
            assert wal.get_price(action) == wal.PRICES[action]

    def test_unknown_action_is_still_free(self):
        assert wal.get_price("no_such_action") == 0.0
        assert wal.get_price("no_such_action", "INR") == 0.0

    def test_sufficiency_defaults_to_usd(self):
        assert wal.has_sufficient_balance(0.25, "full_analysis") is True
        assert wal.has_sufficient_balance(0.24, "full_analysis") is False

    def test_charge_defaults_to_usd(self):
        assert wal.new_balance_after_charge(1.0, "full_analysis") == 0.75

    def test_valid_topup_defaults_to_usd(self):
        for pack in wal.TOPUP_PACKS:
            assert wal.is_valid_topup(pack) is True
        assert wal.is_valid_topup(99.0) is False


class TestCurrencyResolution:
    def test_inr_tables_are_distinct_from_usd(self):
        assert wal.PRICES_INR != wal.PRICES
        assert wal.TOPUP_PACKS_INR != wal.TOPUP_PACKS
        assert wal.CURRENCY_SYMBOL_INR == "\u20b9"

    @pytest.mark.parametrize("value", ["USD", "usd", "", "EUR", "inr", None, "xxx"])
    def test_anything_that_is_not_exactly_inr_resolves_to_usd(self, value):
        """Fails toward USD deliberately: resolving a corrupt stored value to
        the cheaper-numbered INR table would devalue a real balance."""
        assert wal.prices_for(value) is wal.PRICES
        assert wal.topup_packs_for(value) is wal.TOPUP_PACKS
        assert wal.currency_symbol_for(value) == wal.CURRENCY_SYMBOL

    def test_inr_resolves_to_inr(self):
        assert wal.prices_for("INR") is wal.PRICES_INR
        assert wal.topup_packs_for("INR") is wal.TOPUP_PACKS_INR
        assert wal.currency_symbol_for("INR") == "\u20b9"


class TestCrossCurrencyPackValidation:
    """Acceptance criterion 3."""

    @pytest.mark.parametrize("amount", [5.0, 10.0, 25.0])
    def test_usd_pack_is_not_a_valid_inr_pack(self, amount):
        assert wal.is_valid_topup(amount, "USD") is True
        assert wal.is_valid_topup(amount, "INR") is False

    @pytest.mark.parametrize("amount", [99.0, 199.0, 499.0])
    def test_inr_pack_is_not_a_valid_usd_pack(self, amount):
        assert wal.is_valid_topup(amount, "INR") is True
        assert wal.is_valid_topup(amount, "USD") is False


class TestInrBalanceMath:
    def test_inr_charge_leaves_the_right_remainder(self):
        assert wal.new_balance_after_charge(99.0, "full_analysis", "INR") == 79.0

    def test_inr_balance_never_goes_negative(self):
        assert wal.new_balance_after_charge(5.0, "full_analysis", "INR") == 0.0

    def test_inr_sufficiency_uses_inr_prices(self):
        # ₹5 cannot pay a ₹20 analysis, even though $5 would pay a $0.25 one.
        assert wal.has_sufficient_balance(5.0, "full_analysis", "INR") is False
        assert wal.has_sufficient_balance(20.0, "full_analysis", "INR") is True


# ---------------------------------------------------------------------------
# Locking, end to end
# ---------------------------------------------------------------------------

@needs_payments
class TestFirstTopupLocksCurrency:
    """Acceptance criterion 1."""

    def test_new_account_locks_to_the_chosen_currency(self):
        uid, hdrs = mk_user()
        try:
            r = order(hdrs, 99.0, "INR")
            assert r.status_code == 200, r.text
            assert r.json()["currency"] == "INR"
            assert wallet_doc(uid)["currency"] == "INR"
        finally:
            cleanup(uid)

    def test_a_later_request_naming_a_different_currency_is_ignored(self):
        uid, hdrs = mk_user()
        try:
            assert order(hdrs, 99.0, "INR").status_code == 200
            # Asking for USD with a USD pack amount must fail: the account is
            # INR now, and 5 is not an INR pack.
            r = order(hdrs, 5.0, "USD")
            assert r.status_code == 400, r.text
            assert "99" in r.json()["detail"]
            # Still INR, and an INR amount still works.
            assert wallet_doc(uid)["currency"] == "INR"
            second = order(hdrs, 199.0, "USD")
            assert second.status_code == 200, second.text
            assert second.json()["currency"] == "INR"
        finally:
            cleanup(uid)

    def test_no_currency_specified_defaults_to_usd(self):
        uid, hdrs = mk_user()
        try:
            r = order(hdrs, 5.0)
            assert r.status_code == 200, r.text
            assert r.json()["currency"] == "USD"
            assert wallet_doc(uid)["currency"] == "USD"
        finally:
            cleanup(uid)

    def test_a_nonsense_currency_falls_back_to_usd(self):
        uid, hdrs = mk_user()
        try:
            r = order(hdrs, 5.0, "EUR")
            assert r.status_code == 200, r.text
            assert r.json()["currency"] == "USD"
            assert wallet_doc(uid)["currency"] == "USD"
        finally:
            cleanup(uid)

    def test_the_stored_payment_records_the_locked_currency(self):
        uid, hdrs = mk_user()
        try:
            r = order(hdrs, 199.0, "INR")
            assert r.status_code == 200, r.text
            row = _db.payments.find_one({"wallet_key": f"user:{uid}"})
            assert row["currency"] == "INR"
            assert row["amount"] == 199.0
        finally:
            cleanup(uid)


@needs_payments
class TestExistingBalanceIsProtected:
    """Acceptance criterion 2 — the case that protects real money.

    A wallet holding a nonzero balance with no currency field can only have
    earned it in USD (the only currency that existed then). It must lock to
    USD whatever the request asks for, so $12.50 can never become ₹12.50.
    """

    def test_existing_balance_forces_usd_and_rejects_an_inr_pack(self):
        uid, hdrs = mk_user(balance=12.5)
        try:
            r = order(hdrs, 99.0, "INR")
            assert r.status_code == 400, r.text
            # Offered the USD packs, because that's what this account now is.
            assert "5" in r.json()["detail"]
            doc = wallet_doc(uid)
            assert doc["currency"] == "USD", "existing balance was not protected"
            assert doc["balance"] == 12.5, "balance must not be touched"
        finally:
            cleanup(uid)

    def test_existing_balance_then_tops_up_normally_in_usd(self):
        uid, hdrs = mk_user(balance=12.5)
        try:
            assert order(hdrs, 99.0, "INR").status_code == 400
            r = order(hdrs, 5.0, "INR")  # currency still ignored
            assert r.status_code == 200, r.text
            assert r.json()["currency"] == "USD"
            assert wallet_doc(uid)["balance"] == 12.5
        finally:
            cleanup(uid)

    def test_an_empty_legacy_wallet_may_still_choose_inr(self):
        """A zero balance has nothing to revalue, so the choice is safe."""
        uid, hdrs = mk_user(balance=0.0)
        try:
            r = order(hdrs, 99.0, "INR")
            assert r.status_code == 200, r.text
            assert wallet_doc(uid)["currency"] == "INR"
        finally:
            cleanup(uid)

    def test_an_already_locked_usd_wallet_with_balance_stays_usd(self):
        uid, hdrs = mk_user(balance=12.5, currency="USD")
        try:
            assert order(hdrs, 99.0, "INR").status_code == 400
            assert wallet_doc(uid)["currency"] == "USD"
        finally:
            cleanup(uid)


class TestWalletBalanceEndpoint:
    """Acceptance criterion 5."""

    def test_reports_inr_prices_packs_and_symbol(self):
        uid, hdrs = mk_user(balance=99.0, currency="INR")
        try:
            r = requests.get(f"{BASE_URL}/api/wallet/balance", headers=hdrs, timeout=20)
            assert r.status_code == 200, r.text
            j = r.json()
            assert j["currency"] == "INR"
            assert j["symbol"] == "\u20b9"
            assert j["packs"] == wal.TOPUP_PACKS_INR
            assert j["prices"]["full_analysis"] == wal.PRICES_INR["full_analysis"]
            assert j["currency_locked"] is True
        finally:
            cleanup(uid)

    def test_reports_usd_for_a_locked_usd_account(self):
        uid, hdrs = mk_user(balance=10.0, currency="USD")
        try:
            j = requests.get(f"{BASE_URL}/api/wallet/balance", headers=hdrs, timeout=20).json()
            assert j["currency"] == "USD"
            assert j["symbol"] == "$"
            assert j["packs"] == wal.TOPUP_PACKS
            assert j["currency_locked"] is True
        finally:
            cleanup(uid)

    def test_an_unlocked_account_reads_usd_but_reports_it_is_not_locked(self):
        uid, hdrs = mk_user()
        try:
            j = requests.get(f"{BASE_URL}/api/wallet/balance", headers=hdrs, timeout=20).json()
            assert j["currency"] == "USD"
            assert j["currency_locked"] is False
            codes = [o["code"] for o in j["currency_options"]]
            assert codes == list(wal.SUPPORTED_CURRENCIES)
            inr = next(o for o in j["currency_options"] if o["code"] == "INR")
            # The app renders these directly, so it holds no currency knowledge
            # of its own — no hardcoded amounts or symbols.
            assert inr["packs"] == wal.TOPUP_PACKS_INR
            assert inr["symbol"] == "\u20b9"
            assert inr["prices"]["full_analysis"] == wal.PRICES_INR["full_analysis"]
        finally:
            cleanup(uid)


class TestAnalyzeChargesInTheAccountsCurrency:
    """Acceptance criterion 6."""

    def test_insufficient_balance_message_uses_rupees(self):
        # ₹5 can't pay for a ₹20 analysis.
        uid, hdrs = mk_user(balance=5.0, currency="INR", free_credits=0)
        try:
            r = requests.post(f"{BASE_URL}/api/analyze", headers=hdrs,
                              json={"symbol": "TESTCUR"}, timeout=30)
            assert r.status_code == 402, r.text
            detail = r.json()["detail"]
            assert "\u20b920.00" in detail, detail
            assert "\u20b95.00" in detail, detail
            assert "$" not in detail
        finally:
            cleanup(uid)

    def test_insufficient_balance_message_uses_dollars_for_a_usd_account(self):
        uid, hdrs = mk_user(balance=0.01, currency="USD", free_credits=0)
        try:
            r = requests.post(f"{BASE_URL}/api/analyze", headers=hdrs,
                              json={"symbol": "TESTCUR"}, timeout=30)
            assert r.status_code == 402, r.text
            detail = r.json()["detail"]
            assert "$0.25" in detail, detail
            assert "\u20b9" not in detail
        finally:
            cleanup(uid)

    def test_an_inr_account_with_enough_balance_is_charged_the_inr_price(self):
        uid, hdrs = mk_user(balance=99.0, currency="INR", free_credits=0)
        try:
            r = requests.post(f"{BASE_URL}/api/analyze", headers=hdrs,
                              json={"symbol": "AAPL"}, timeout=60)
            assert r.status_code == 200, r.text
            body = r.json()
            if body.get("billed"):
                assert body["price_charged"] == wal.PRICES_INR["full_analysis"]
                assert wallet_doc(uid)["balance"] == pytest.approx(79.0)
        finally:
            _db.analyses.delete_many({"id": {"$in": []}})
            cleanup(uid)


# ---------------------------------------------------------------------------
# Apple IAP must never add a USD amount to an INR-locked balance
# ---------------------------------------------------------------------------

class TestIapCreditAmountResolution:
    def test_usd_wallet_gets_the_usd_face_value(self):
        assert iap.credit_amount_for("credits_5", "USD") == 5.0
        assert iap.credit_amount_for("credits_25") == 25.0

    def test_inr_wallet_gets_the_inr_pack_value(self):
        assert iap.credit_amount_for("credits_5", "INR") == 99.0
        assert iap.credit_amount_for("credits_25", "INR") == 499.0

    def test_inr_iap_amounts_match_the_razorpay_inr_packs(self):
        assert sorted(iap.PRODUCT_CREDIT_INR.values()) == sorted(wal.TOPUP_PACKS_INR)

    def test_unknown_product_resolves_to_nothing_in_either_currency(self):
        assert iap.credit_amount_for("credits_9999", "USD") is None
        assert iap.credit_amount_for("credits_9999", "INR") is None

    def test_displayed_packs_follow_the_currency(self):
        assert [p["amount"] for p in iap.packs("INR")] == wal.TOPUP_PACKS_INR
        assert [p["amount"] for p in iap.packs("USD")] == wal.TOPUP_PACKS


@needs_iap_secret
class TestIapCreditsInTheWalletsOwnCurrency:
    def iap_event(self, wallet_key, product_id, currency="USD"):
        return {
            "api_version": "1.0",
            "event": {
                "id": f"TEST_cur-evt-{uuid.uuid4()}",
                "type": "NON_RENEWING_PURCHASE",
                "store": "APP_STORE",
                "product_id": product_id,
                "app_user_id": wallet_key,
                "transaction_id": f"TEST_cur-txn-{uuid.uuid4()}",
                "currency": currency,
                "environment": "SANDBOX",
            },
        }

    def post(self, payload):
        return requests.post(
            f"{BASE_URL}/api/pay/iap/webhook",
            json=payload,
            headers={"Authorization": IAP_SECRET},
            timeout=25,
        )

    def test_an_inr_locked_wallet_is_credited_rupees_not_dollars(self):
        uid, _ = mk_user(balance=0.0, currency="INR")
        key = f"user:{uid}"
        try:
            r = self.post(self.iap_event(key, "credits_5", currency="INR"))
            assert r.status_code == 200, r.text
            assert r.json()["credited"] is True
            # The bug this guards against would leave a balance of 5.
            assert wallet_doc(uid)["balance"] == 99.0
            row = _db.wallet_ledger.find_one({"wallet_key": key, "source": "apple_iap"})
            assert row["currency"] == "INR"
            assert row["amount"] == 99.0
        finally:
            cleanup(uid)

    def test_apple_billing_in_inr_for_a_usd_wallet_still_credits_dollars(self):
        """Apple bills in the buyer's storefront currency, which says nothing
        about the units the balance is kept in."""
        uid, _ = mk_user(balance=0.0, currency="USD")
        key = f"user:{uid}"
        try:
            r = self.post(self.iap_event(key, "credits_10", currency="INR"))
            assert r.status_code == 200, r.text
            assert wallet_doc(uid)["balance"] == 10.0
        finally:
            cleanup(uid)

    def test_an_unlocked_wallet_is_credited_usd_and_locked_to_usd(self):
        uid, _ = mk_user()
        key = f"user:{uid}"
        try:
            r = self.post(self.iap_event(key, "credits_5"))
            assert r.status_code == 200, r.text
            doc = wallet_doc(uid)
            assert doc["balance"] == 5.0
            # Explicit from now on, so a later Razorpay top-up doesn't have to
            # infer what this balance is denominated in.
            assert doc["currency"] == "USD"
        finally:
            cleanup(uid)

    def test_a_second_purchase_does_not_relock_or_reprice(self):
        uid, _ = mk_user(balance=0.0, currency="INR")
        key = f"user:{uid}"
        try:
            self.post(self.iap_event(key, "credits_5", currency="INR"))
            self.post(self.iap_event(key, "credits_10", currency="INR"))
            doc = wallet_doc(uid)
            assert doc["balance"] == 99.0 + 199.0
            assert doc["currency"] == "INR"
        finally:
            cleanup(uid)


def cleanup(uid):
    key = f"user:{uid}"
    _db.users.delete_many({"id": uid})
    _db.wallets.delete_many({"device_id": key})
    _db.payments.delete_many({"wallet_key": key})
    _db.wallet_ledger.delete_many({"wallet_key": key})


def teardown_module():
    _db.webhook_events.delete_many({"event_id": {"$regex": "^rc:TEST_cur-evt-"}})
    _db.analyses.delete_many({"symbol": "TESTCUR"})
