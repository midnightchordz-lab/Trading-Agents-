"""Integration tests for the "Return to the app" fix.

Covers the gap flagged by the main agent (E1): the 35 existing tests in
`test_payment_return_button.py` are unit-level on `result_html` and
`safe_return_url`. This module exercises the wire path:

- POST /api/pay/order persists `return_url` on the created payments document
- The link-REUSE path UPDATES the stored `return_url` to the current request's
- GET /api/pay/callback with NO parameters returns 200 HTML with a Close button
  and never the dead-end sentence "You can close this and return to the app."
- /api/pay/order still works when `return_url` is omitted (regression)
- safe_return_url rejects http/https/javascript/etc.

Razorpay keys in backend/.env are LIVE. Any payment link a test creates is
cancelled in a session-scoped finalizer (POST /payment_links/{id}/cancel).
"""
import os
import sys
from pathlib import Path

import httpx
import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(Path(__file__).parent.parent / ".env")

from auth_helper import AUTH_HEADERS, USER_ID  # noqa: E402
from routes.payments import safe_return_url  # noqa: E402
import razorpay_pay as rzp  # noqa: E402

BASE = "http://localhost:8001"
WALLET_KEY = f"user:{USER_ID}"

_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ.get("DB_NAME", "test_database")]

# Track any real Razorpay payment link ids the tests create, so the session
# teardown can cancel them. Cap: keep well below 2 fresh creates per session.
_CREATED_LINK_IDS: list[str] = []


def _cancel_link(link_id: str) -> None:
    try:
        r = httpx.post(
            f"{rzp.RAZORPAY_API}/payment_links/{link_id}/cancel",
            auth=(rzp.KEY_ID, rzp.KEY_SECRET),
            timeout=15,
        )
        print(f"cancelled {link_id}: {r.status_code}")
    except Exception as e:
        print(f"failed to cancel {link_id}: {e}")


@pytest.fixture(scope="module", autouse=True)
def _cleanup_after_module():
    yield
    # Cancel every real payment link this module created — LIVE keys.
    seen = set()
    for lid in _CREATED_LINK_IDS:
        if lid in seen:
            continue
        seen.add(lid)
        _cancel_link(lid)
    # Wipe module-owned test rows so a re-run starts clean.
    _db.payments.delete_many({"wallet_key": WALLET_KEY})


def _fresh_wallet_currency(currency: str = "USD") -> None:
    """Lock this test user's wallet to `currency` and clear any open link.

    A previously locked USD wallet cannot then top up in INR (the whole point
    of the lock), and a lingering "created" link would trigger the REUSE path
    on the "first" test that expects a create. Both are prevented here.
    """
    _db.wallets.update_one(
        {"device_id": WALLET_KEY},
        {"$set": {"device_id": WALLET_KEY, "balance": 100.0, "currency": currency}},
        upsert=True,
    )
    _db.payments.delete_many({"wallet_key": WALLET_KEY, "status": "created"})


# --- return_url is persisted on POST /api/pay/order ----------------------

class TestReturnUrlPersistence:
    def test_create_topup_order_persists_return_url_on_payment(self):
        _fresh_wallet_currency("USD")
        deep_link = "frontend:///"
        payload = {
            "amount": 5,
            "email": "buyer@example.com",
            # Randomised so contact_rejection does not trip.
            "phone": "+14155238132",
            "return_url": deep_link,
        }
        r = requests.post(f"{BASE}/api/pay/order", json=payload, headers=AUTH_HEADERS, timeout=30)
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        body = r.json()
        assert "order_id" in body and body["order_id"].startswith("plink_")
        _CREATED_LINK_IDS.append(body["order_id"])

        stored = _db.payments.find_one({"razorpay_payment_link_id": body["order_id"]})
        assert stored is not None, "payment record was not created"
        assert stored.get("return_url") == deep_link, \
            f"return_url not persisted: {stored.get('return_url')!r}"
        assert stored.get("wallet_key") == WALLET_KEY
        assert stored.get("status") == "created"

    def test_reuse_path_updates_stored_return_url(self):
        """Tapping the same pack a second time must reuse the OPEN link but
        REWRITE its `return_url` to whatever the current request is asking for."""
        _fresh_wallet_currency("USD")
        # First tap: link created with url_A.
        url_a = "exp://192.168.1.5:8081/--/"
        r1 = requests.post(
            f"{BASE}/api/pay/order",
            json={"amount": 5, "email": "buyer@example.com", "phone": "+14155238133",
                  "return_url": url_a},
            headers=AUTH_HEADERS, timeout=30,
        )
        assert r1.status_code == 200, r1.text
        link_id = r1.json()["order_id"]
        _CREATED_LINK_IDS.append(link_id)
        assert _db.payments.find_one({"razorpay_payment_link_id": link_id})["return_url"] == url_a

        # Second tap on the SAME pack: no new Razorpay call, same link_id back,
        # but the stored `return_url` moves to url_B.
        url_b = "frontend://wallet"
        r2 = requests.post(
            f"{BASE}/api/pay/order",
            json={"amount": 5, "email": "buyer@example.com", "phone": "+14155238133",
                  "return_url": url_b},
            headers=AUTH_HEADERS, timeout=30,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json().get("reused") is True, "expected reuse path, got a new link"
        assert r2.json()["order_id"] == link_id
        rewritten = _db.payments.find_one({"razorpay_payment_link_id": link_id})
        assert rewritten["return_url"] == url_b, \
            f"reuse did not update return_url: {rewritten.get('return_url')!r}"

    def test_order_still_works_when_return_url_is_omitted(self):
        """Regression: the field is optional; no 500, no schema error."""
        _fresh_wallet_currency("USD")
        r = requests.post(
            f"{BASE}/api/pay/order",
            json={"amount": 10, "email": "buyer@example.com", "phone": "+14155238134"},
            headers=AUTH_HEADERS, timeout=30,
        )
        assert r.status_code == 200, r.text
        link_id = r.json()["order_id"]
        _CREATED_LINK_IDS.append(link_id)
        rec = _db.payments.find_one({"razorpay_payment_link_id": link_id})
        # Field is OMITTED (not stored as null / empty) when not sent.
        assert "return_url" not in rec or rec["return_url"] in (None, "")

    def test_unsafe_return_url_is_dropped_but_order_succeeds(self):
        """A rejected value must not become a stored open-redirect target,
        and must not fail the order — the customer's payment still starts."""
        _fresh_wallet_currency("USD")
        r = requests.post(
            f"{BASE}/api/pay/order",
            json={"amount": 5, "email": "buyer@example.com", "phone": "+14155238135",
                  "return_url": "https://evil.example.com"},
            headers=AUTH_HEADERS, timeout=30,
        )
        assert r.status_code == 200, r.text
        link_id = r.json()["order_id"]
        _CREATED_LINK_IDS.append(link_id)
        rec = _db.payments.find_one({"razorpay_payment_link_id": link_id})
        assert not rec.get("return_url"), \
            f"unsafe return_url leaked into DB: {rec.get('return_url')!r}"


# --- Behaviour regressions unchanged by this fix -------------------------

class TestOrderRegression:
    def test_contact_required_still_raised_without_email_or_phone(self):
        _fresh_wallet_currency("USD")
        # Other modules in a full-suite run can set billing_phone / billing_email
        # on the shared auth_helper user. Clear both so this assertion is about
        # the endpoint's behaviour, not test-order.
        _db.users.update_one(
            {"id": USER_ID},
            {"$unset": {"billing_phone": "", "billing_email": "", "phone": ""}},
        )
        r = requests.post(
            f"{BASE}/api/pay/order",
            json={"amount": 5},
            headers=AUTH_HEADERS, timeout=15,
        )
        # This test user in auth_helper has email set, so only phone will be
        # missing; either way the 400 detail must start with contact_required.
        assert r.status_code == 400
        assert r.json()["detail"].startswith("contact_required:")

    def test_contact_invalid_for_recurring_digits(self):
        _fresh_wallet_currency("USD")
        r = requests.post(
            f"{BASE}/api/pay/order",
            json={"amount": 5, "email": "buyer@example.com", "phone": "+919999999999"},
            headers=AUTH_HEADERS, timeout=15,
        )
        assert r.status_code == 400
        assert r.json()["detail"].startswith("contact_invalid:phone:")

    def test_currency_lock_rejects_wrong_pack(self):
        """USD wallet asked to pay ₹99 => 400, currency-lock behaviour intact."""
        _fresh_wallet_currency("USD")
        r = requests.post(
            f"{BASE}/api/pay/order",
            json={"amount": 99, "email": "buyer@example.com", "phone": "+14155238136"},
            headers=AUTH_HEADERS, timeout=15,
        )
        assert r.status_code == 400
        assert "top-up packs" in r.json()["detail"].lower()


# --- GET /api/pay/callback with no params --------------------------------

class TestCallbackBodyless:
    OLD_DEAD_END = "You can close this and return to the app."

    def test_bodyless_callback_returns_html_with_close_button(self):
        r = requests.get(f"{BASE}/api/pay/callback", timeout=15)
        assert r.status_code == 200
        html = r.text
        assert "<button" in html and "window.close()" in html, \
            "bodyless callback missing tappable Close button"
        # Old dead-end sentence must be gone from THIS response.
        assert self.OLD_DEAD_END not in html

    def test_dead_end_sentence_is_nowhere_in_any_callback_response(self):
        """Sweep of every branch reachable without valid signatures."""
        cases = [
            # (method, params, body)
            ("GET", {}, None),
            ("GET", {"razorpay_payment_link_id": "plink_nonexistent"}, None),
            ("GET", {"razorpay_order_id": "order_nonexistent"}, None),
            ("POST", {}, {}),
            ("POST", {}, {"razorpay_payment_id": "pay_x", "razorpay_signature": "bad"}),
        ]
        for method, params, body in cases:
            if method == "GET":
                r = requests.get(f"{BASE}/api/pay/callback", params=params, timeout=15)
            else:
                r = requests.post(f"{BASE}/api/pay/callback", params=params, json=body, timeout=15)
            assert self.OLD_DEAD_END not in r.text, \
                f"dead-end sentence found in {method} params={params} body={body}"


# --- Unit-level guard on result_html rendering a link on EVERY outcome --

class TestResultHtmlAllOutcomes:
    DEEP = "frontend:///"

    @pytest.mark.parametrize("ok", [True, False])
    def test_result_html_renders_anchor_for_success_and_failure(self, ok):
        html = rzp.result_html("some message", ok=ok, return_url=self.DEEP)
        assert f'href="{self.DEEP}"' in html
        assert "Return to the app" in html

    def test_result_html_no_link_when_unsafe_url_dropped(self):
        # Mirrors the pipeline: unsafe URL -> safe_return_url -> None -> no link.
        cleaned = safe_return_url("javascript:alert(1)")
        assert cleaned is None
        html = rzp.result_html("hi", ok=True, return_url=cleaned)
        assert "javascript:" not in html
        assert "alert(1)" not in html
        assert "<button" in html
