"""Iteration 14 — Razorpay Payment Link migration verification.

Covers:
  * POST /api/pay/order returns a plink_ id + rzp.io URL, USD, right amount.
  * contact_required:email for a phone-only account; contact_required:phone for
    email-only account; contact is persisted after supply.
  * Removed self-hosted checkout page returns 404.
  * GET /api/pay/status/{plink_id} works, 404 unknown, 403 another user's,
    401 unauth, never credits while unpaid.
  * Payment-link callback: forged signature 400; cancel/no-success 200 with
    "nothing was charged"; a real-created link with a *validly signed* callback
    for a fake payment id returns 200 + "still confirming" and credits nothing.
  * No callback branch ever produces a 422.
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
load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv(Path(__file__).parent.parent.parent / "frontend" / ".env")

import auth as au  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("EXPO_BACKEND_URL")
    or os.environ["PUBLIC_BASE_URL"]
).rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ.get("DB_NAME", "test_database")
JWT_SECRET = os.environ["JWT_SECRET"]
KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

_client = MongoClient(MONGO_URL)
_db = _client[DB_NAME]


def _mk_user(phone=None, email=None):
    uid = f"test-{uuid.uuid4()}"
    _db.users.insert_one({
        "id": uid,
        "phone": phone,
        "email": email if email is not None else f"{uid}@example.com",
        "google_sub": None,
        "apple_sub": None,
        "consent": {"agreed": True, "agreed_at": "2026-01-01T00:00:00+00:00", "version": "1.0"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    tok = au.create_session_token(uid, JWT_SECRET)
    return uid, {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}


def _bal(uid):
    doc = _db.wallets.find_one({"device_id": f"user:{uid}"})
    return float(doc["balance"]) if doc else 0.0


def _cleanup(uid):
    _db.users.delete_many({"id": uid})
    _db.wallets.delete_many({"device_id": f"user:{uid}"})
    _db.payments.delete_many({"wallet_key": f"user:{uid}"})


# --------------------- /pay/order shape ---------------------

class TestOrderShape:
    def test_returns_plink_id_and_rzp_hosted_url_and_usd(self):
        uid, h = _mk_user()
        try:
            r = requests.post(
                f"{BASE_URL}/api/pay/order",
                headers=h,
                json={"amount": 10, "phone": "+15550001234"},
                timeout=20,
            )
            assert r.status_code == 200, r.text
            b = r.json()
            assert b["order_id"].startswith("plink_"), b
            assert b["currency"] == "USD"
            assert b["amount"] == 10
            assert b["checkout_url"].startswith("https://")
            # must be Razorpay-hosted, never our own domain
            assert "rzp.io" in b["checkout_url"] or "razorpay.com" in b["checkout_url"], b["checkout_url"]
            assert "trade-agent-app.preview.emergentagent.com" not in b["checkout_url"]
            # ensure DB record uses new schema
            rec = _db.payments.find_one({"razorpay_payment_link_id": b["order_id"]})
            assert rec is not None
            assert rec.get("short_url", "").startswith("https://")
            assert rec.get("reference_id", "").startswith("wallet_")
            assert rec["status"] == "created"
        finally:
            _cleanup(uid)

    def test_email_only_account_asks_for_phone(self):
        uid, h = _mk_user()  # email only
        try:
            r = requests.post(f"{BASE_URL}/api/pay/order", headers=h,
                              json={"amount": 5}, timeout=15)
            assert r.status_code == 400, r.text
            assert r.json()["detail"] == "contact_required:phone"
        finally:
            _cleanup(uid)

    def test_phone_only_account_asks_for_email(self):
        uid, h = _mk_user(phone="+15559990001", email=None)
        try:
            # remove default email to simulate a phone-only account
            _db.users.update_one({"id": uid}, {"$set": {"email": None}})
            r = requests.post(f"{BASE_URL}/api/pay/order", headers=h,
                              json={"amount": 5}, timeout=15)
            assert r.status_code == 400, r.text
            assert r.json()["detail"] == "contact_required:email"
        finally:
            _cleanup(uid)

    def test_invalid_phone_is_rejected_as_still_missing(self):
        uid, h = _mk_user()
        try:
            r = requests.post(f"{BASE_URL}/api/pay/order", headers=h,
                              json={"amount": 5, "phone": "notaphone"}, timeout=15)
            assert r.status_code == 400, r.text
            # normalize_identifier can't turn garbage into a phone -> still missing
            assert "contact_required" in r.json()["detail"], r.text
        finally:
            _cleanup(uid)

    def test_supplying_missing_contact_persists_and_next_call_needs_none(self):
        uid, h = _mk_user()
        try:
            r1 = requests.post(f"{BASE_URL}/api/pay/order", headers=h,
                               json={"amount": 5, "phone": "+15550009999"}, timeout=20)
            assert r1.status_code == 200, r1.text
            u = _db.users.find_one({"id": uid})
            # Unverified contact is stored separately and must NEVER overwrite
            # the verified sign-in identity — that write was an admin-escalation
            # path (any user could claim the owner's phone).
            assert u.get("billing_phone") == "+15550009999"
            assert u.get("phone") is None
            # No contact in body — must still succeed.
            r2 = requests.post(f"{BASE_URL}/api/pay/order", headers=h,
                               json={"amount": 25}, timeout=20)
            assert r2.status_code == 200, r2.text
            assert r2.json()["order_id"].startswith("plink_")
        finally:
            _cleanup(uid)


# --------------------- self-hosted checkout removed ---------------------

class TestLegacyCheckoutRemoved:
    def test_get_pay_checkout_id_is_404(self):
        # A random plausible order id — endpoint itself must be gone.
        r = requests.get(f"{BASE_URL}/api/pay/checkout/order_ABC123", timeout=10)
        assert r.status_code == 404, r.text


# --------------------- /pay/status semantics ---------------------

class TestPayStatus:
    def test_unauth_is_401(self):
        r = requests.get(f"{BASE_URL}/api/pay/status/plink_anything", timeout=10)
        assert r.status_code == 401, r.text

    def test_unknown_id_is_404(self):
        _, h = _mk_user()
        r = requests.get(f"{BASE_URL}/api/pay/status/plink_does_not_exist_zzz",
                         headers=h, timeout=15)
        assert r.status_code == 404, r.text

    def test_other_users_order_is_403(self):
        # user A creates a link, user B tries to read its status.
        a_uid, a_h = _mk_user()
        b_uid, b_h = _mk_user()
        try:
            r = requests.post(f"{BASE_URL}/api/pay/order", headers=a_h,
                              json={"amount": 5, "phone": "+15550001234"}, timeout=15)
            assert r.status_code == 200, r.text
            link_id = r.json()["order_id"]
            r2 = requests.get(f"{BASE_URL}/api/pay/status/{link_id}",
                              headers=b_h, timeout=15)
            assert r2.status_code == 403, r2.text
        finally:
            _cleanup(a_uid); _cleanup(b_uid)

    def test_unpaid_link_does_not_credit(self):
        uid, h = _mk_user()
        try:
            r = requests.post(f"{BASE_URL}/api/pay/order", headers=h,
                              json={"amount": 5, "phone": "+15550001234"}, timeout=15)
            link_id = r.json()["order_id"]
            r2 = requests.get(f"{BASE_URL}/api/pay/status/{link_id}",
                              headers=h, timeout=20)
            assert r2.status_code == 200, r2.text
            body = r2.json()
            assert body["order_id"] == link_id
            assert body["status"] != "captured"
            assert body["balance"] == 0.0
            assert _bal(uid) == 0.0
        finally:
            _cleanup(uid)


# --------------------- callback: link path ---------------------

class TestLinkCallback:
    def test_cancel_query_returns_200_and_marks_failed(self):
        uid, h = _mk_user()
        try:
            r = requests.post(f"{BASE_URL}/api/pay/order", headers=h,
                              json={"amount": 5, "phone": "+15550001234"}, timeout=15)
            link_id = r.json()["order_id"]
            ref_id = _db.payments.find_one({"razorpay_payment_link_id": link_id})["reference_id"]
            r2 = requests.get(
                f"{BASE_URL}/api/pay/callback",
                params={
                    "razorpay_payment_link_id": link_id,
                    "razorpay_payment_link_reference_id": ref_id,
                    "razorpay_payment_link_status": "cancelled",
                },
                timeout=15,
                allow_redirects=False,
            )
            assert r2.status_code == 200, r2.text
            assert "nothing was charged" in r2.text
            # record marked failed
            doc = _db.payments.find_one({"razorpay_payment_link_id": link_id})
            assert doc["status"] == "failed"
            assert _bal(uid) == 0.0
        finally:
            _cleanup(uid)

    def test_forged_signature_is_400_and_no_credit(self):
        uid, h = _mk_user()
        try:
            r = requests.post(f"{BASE_URL}/api/pay/order", headers=h,
                              json={"amount": 5, "phone": "+15550001234"}, timeout=15)
            link_id = r.json()["order_id"]
            ref_id = _db.payments.find_one({"razorpay_payment_link_id": link_id})["reference_id"]
            r2 = requests.get(
                f"{BASE_URL}/api/pay/callback",
                params={
                    "razorpay_payment_id": "pay_fake",
                    "razorpay_payment_link_id": link_id,
                    "razorpay_payment_link_reference_id": ref_id,
                    "razorpay_payment_link_status": "paid",
                    "razorpay_signature": "deadbeef" * 8,
                },
                timeout=15,
                allow_redirects=False,
            )
            assert r2.status_code == 400, r2.text
            assert "verify" in r2.text.lower()
            assert _bal(uid) == 0.0
        finally:
            _cleanup(uid)

    def test_validly_signed_but_fake_payment_returns_still_confirming(self):
        """A *correctly signed* link callback whose payment id doesn't exist
        must be accepted (200 + 'still confirming'), and credit nothing."""
        if not KEY_SECRET:
            pytest.skip("RAZORPAY_KEY_SECRET not set")
        uid, h = _mk_user()
        try:
            r = requests.post(f"{BASE_URL}/api/pay/order", headers=h,
                              json={"amount": 5, "phone": "+15550001234"}, timeout=15)
            link_id = r.json()["order_id"]
            rec = _db.payments.find_one({"razorpay_payment_link_id": link_id})
            ref_id = rec["reference_id"]
            payment_id = f"pay_fake{uuid.uuid4().hex[:10]}"
            msg = f"{link_id}|{ref_id}|paid|{payment_id}".encode()
            sig = hmac.new(KEY_SECRET.encode(), msg, hashlib.sha256).hexdigest()
            r2 = requests.get(
                f"{BASE_URL}/api/pay/callback",
                params={
                    "razorpay_payment_id": payment_id,
                    "razorpay_payment_link_id": link_id,
                    "razorpay_payment_link_reference_id": ref_id,
                    "razorpay_payment_link_status": "paid",
                    "razorpay_signature": sig,
                },
                timeout=20,
                allow_redirects=False,
            )
            assert r2.status_code == 200, r2.text
            assert "still confirming" in r2.text.lower() or "confirming" in r2.text.lower()
            assert _bal(uid) == 0.0
        finally:
            _cleanup(uid)

    def test_no_callback_variant_ever_returns_422(self):
        # Cover a bunch of weird payloads — none of them may 422.
        variants = [
            ("GET", {"razorpay_payment_link_id": "plink_x"}, None),
            ("GET", {}, None),
            ("POST", None, {}),
            ("POST", None, {"razorpay_payment_link_id": "plink_x"}),
        ]
        for method, params, data in variants:
            r = requests.request(method, f"{BASE_URL}/api/pay/callback",
                                 params=params, data=data, timeout=15,
                                 allow_redirects=False)
            assert r.status_code != 422, f"{method} {params}/{data} -> 422"


# --------------------- webhook code path ---------------------

class TestWebhookGuardStillClosed:
    def test_no_signature_is_400(self):
        r = requests.post(f"{BASE_URL}/api/pay/webhook",
                          data=b'{"event":"payment_link.paid"}',
                          headers={"Content-Type": "application/json"}, timeout=10)
        assert r.status_code == 400

    def test_bad_signature_is_400(self):
        r = requests.post(f"{BASE_URL}/api/pay/webhook",
                          data=b'{"event":"payment_link.paid"}',
                          headers={"Content-Type": "application/json",
                                   "X-Razorpay-Signature": "0" * 64},
                          timeout=10)
        assert r.status_code == 400
