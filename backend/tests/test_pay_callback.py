"""Razorpay checkout-callback robustness.

The callback used to declare the three razorpay_* fields as required Form
fields. Razorpay only sends them on a SUCCESSFUL authorisation — a cancelled
or failed payment (and some bank / UPI redirect chains) comes back with no
body at all, which meant the customer was shown a raw FastAPI 422 inside the
checkout WebView. These tests pin the defensive parsing: never 422, never
credit without a valid signature.
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv(Path(__file__).parent.parent.parent / "frontend" / ".env")

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or os.environ["PUBLIC_BASE_URL"]
).rstrip("/")
CALLBACK = f"{BASE_URL}/api/pay/callback"


class TestCallbackNeverReturns422:
    def test_empty_post_is_handled_as_a_cancelled_payment(self):
        r = requests.post(CALLBACK, timeout=15)
        assert r.status_code == 200, r.text
        assert "nothing was charged" in r.text

    def test_get_redirect_without_fields_is_handled(self):
        r = requests.get(f"{CALLBACK}?order_id=order_does_not_exist", timeout=15)
        assert r.status_code == 200, r.text
        assert "nothing was charged" in r.text

    def test_failure_payload_from_razorpay_is_handled(self):
        r = requests.post(
            CALLBACK,
            data={"error[code]": "BAD_REQUEST_ERROR", "error[description]": "Payment failed"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert "nothing was charged" in r.text


class TestCallbackStillRejectsForgeries:
    def test_bad_signature_is_rejected(self):
        r = requests.post(
            CALLBACK,
            data={
                "razorpay_payment_id": "pay_forged",
                "razorpay_order_id": "order_forged",
                "razorpay_signature": "deadbeef",
            },
            timeout=15,
        )
        assert r.status_code == 400, r.text
        assert "verify" in r.text.lower()

    def test_query_param_forgery_is_rejected_too(self):
        r = requests.get(
            CALLBACK,
            params={
                "razorpay_payment_id": "pay_forged",
                "razorpay_order_id": "order_forged",
                "razorpay_signature": "deadbeef",
            },
            timeout=15,
        )
        assert r.status_code == 400, r.text


class TestCheckoutPageContract:
    def test_checkout_page_sets_redirect_and_order_id_on_the_callback(self):
        # Any existing order id works — the page is intentionally unauthenticated.
        from pymongo import MongoClient

        db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]
        order = db.payments.find_one({}, sort=[("created_at", -1)])
        if not order:
            import pytest

            pytest.skip("no orders in the database to render a checkout page for")
        r = requests.get(f"{BASE_URL}/api/pay/checkout/{order['razorpay_order_id']}", timeout=15)
        assert r.status_code == 200, r.text
        assert "redirect: true" in r.text
        assert f"order_id={order['razorpay_order_id']}" in r.text
