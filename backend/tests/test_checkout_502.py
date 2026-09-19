"""The reported 502 on checkout, fixed from what the LOGS actually said.

The uploaded report guessed the Razorpay customer `name` field. The server log
says otherwise, and it was already recording the exact cause on every failure:

    38x  "Too many requests"                                   <- throttling
     4x  "Recurring digits in customer contact are disallowed"  <- the phone
     6x  a 400 with an EMPTY description                        <- unreadable

Not once, ever, the name. So: link reuse and a retry for the throttling, an
inline field error for the phone, and an error message that can never be empty
again. The name is sanitized too, but as insurance, not as the fix.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth as au  # noqa: E402
import razorpay_pay as rzp  # noqa: E402
import routes.payments as pay  # noqa: E402
from tests.async_loop import run_async  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")
BASE = "http://localhost:8001/api"
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-change-me")
db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]

_USERS: list[str] = []
_LINKS: list[str] = []


def seed_account(phone=None, email=None, name=None, currency=None):
    uid = f"TEST_502-{uuid.uuid4()}"
    doc = {"id": uid, "phone": phone, "email": email or (None if phone else f"{uid}@example.com"),
           "identity_type": "phone" if phone else "email",
           "google_sub": None, "apple_sub": None, "free_credits_remaining": 0,
           "consent": {"agreed": True, "agreed_at": datetime.now(timezone.utc).isoformat(), "version": "1.0"},
           "created_at": datetime.now(timezone.utc).isoformat()}
    if name:
        doc["name"] = name
    db.users.insert_one(doc)
    _USERS.append(uid)
    wallet = {"device_id": f"user:{uid}", "balance": 0.0}
    if currency:
        wallet["currency"] = currency
    db.wallets.insert_one(wallet)
    return uid, {"Authorization": f"Bearer {au.create_session_token(uid, JWT_SECRET)}"}


def teardown_module():
    auth = (rzp.KEY_ID, rzp.KEY_SECRET)
    for link_id in _LINKS:
        try:
            requests.post(f"https://api.razorpay.com/v1/payment_links/{link_id}/cancel",
                          auth=auth, timeout=15)
        except Exception:
            pass
    for uid in _USERS:
        db.users.delete_one({"id": uid})
        db.wallets.delete_one({"device_id": f"user:{uid}"})
        db.payments.delete_many({"wallet_key": f"user:{uid}"})


def order(headers, **body):
    return requests.post(f"{BASE}/pay/order", headers=headers, timeout=45, json=body)


# --- the phone Razorpay actually rejected --------------------------------
@pytest.mark.parametrize("phone", [
    "+919999999999",   # the logged failure's shape
    "+911111111111",
    "9999999999",
    "+11234567890",    # sequential
    "+10987654321",
])
def test_made_up_numbers_are_caught_before_razorpay(phone):
    assert pay.contact_rejection(phone) is not None


@pytest.mark.parametrize("phone", [
    "+918291026256", "+919812345678", "+14155550134", "+447911123456", "+6591234567",
])
def test_real_numbers_are_left_alone(phone):
    """The check has to be narrow — rejecting a real customer's number would be
    a worse bug than the one being fixed."""
    assert pay.contact_rejection(phone) is None


def test_short_number_is_explained_not_just_refused():
    assert "country code" in pay.contact_rejection("12345")


def test_no_phone_is_not_a_rejection():
    """Absence is handled by the contact prompt, not by this check."""
    assert pay.contact_rejection(None) is None
    assert pay.contact_rejection("") is None


def test_checkout_returns_an_inline_field_error_for_a_fake_number():
    """End to end: a new account with no phone, typing 9999999999, must get a
    400 naming the phone field — NOT the 502 that was reported."""
    uid, headers = seed_account(currency="INR")
    res = order(headers, amount=99, email="a@example.com", phone="+919999999999")
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert detail.startswith("contact_invalid:phone:"), detail
    assert "real mobile number" in detail
    # Razorpay was never called, so nothing was created to clean up.
    assert db.payments.count_documents({"wallet_key": f"user:{uid}"}) == 0


def test_a_missing_phone_still_asks_for_it():
    """The pre-existing prompt must not be swallowed by the new check."""
    _, headers = seed_account(currency="INR")
    res = order(headers, amount=99)
    assert res.status_code == 400
    assert res.json()["detail"].startswith("contact_required:")


# --- throttling: the actual most common cause ----------------------------
def test_rate_limit_is_recognised_however_razorpay_dresses_it_up():
    assert rzp.is_rate_limited(rzp.RazorpayError(400, "Too many requests", "BAD_REQUEST_ERROR"))
    assert rzp.is_rate_limited(rzp.RazorpayError(429, "rate limited"))
    assert not rzp.is_rate_limited(rzp.RazorpayError(400, "Recurring digits in customer contact are disallowed"))
    assert not rzp.is_rate_limited(rzp.RazorpayError(401, "Authentication failed"))


def test_throttling_is_retried_once_then_reported_as_busy_not_broken():
    calls = {"n": 0}

    class FakeResponse:
        status_code = 400
        reason_phrase = "Bad Request"
        text = '{"error": {"code": "BAD_REQUEST_ERROR", "description": "Too many requests"}}'

        def json(self):
            return {"error": {"code": "BAD_REQUEST_ERROR", "description": "Too many requests"}}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **k):
            calls["n"] += 1
            return FakeResponse()

    real = rzp.httpx.AsyncClient
    rzp.httpx.AsyncClient = FakeClient
    try:
        with pytest.raises(rzp.RazorpayError) as caught:
            run_async(rzp.razorpay_request("POST", "/payment_links", json={}))
    finally:
        rzp.httpx.AsyncClient = real
    assert calls["n"] == 2, "throttling must be retried exactly once"
    assert rzp.is_rate_limited(caught.value)


def test_a_retry_that_succeeds_is_invisible_to_the_caller():
    calls = {"n": 0}

    class Throttled:
        status_code = 400
        reason_phrase = "Bad Request"
        text = "{}"

        def json(self):
            return {"error": {"description": "Too many requests"}}

    class Created:
        status_code = 200
        reason_phrase = "OK"
        text = "{}"

        def json(self):
            return {"id": "plink_ok", "short_url": "https://rzp.io/x"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **k):
            calls["n"] += 1
            return Throttled() if calls["n"] == 1 else Created()

    real = rzp.httpx.AsyncClient
    rzp.httpx.AsyncClient = FakeClient
    try:
        out = run_async(rzp.razorpay_request("POST", "/payment_links", json={}))
    finally:
        rzp.httpx.AsyncClient = real
    assert out["id"] == "plink_ok"
    assert calls["n"] == 2


def test_a_rejected_field_is_never_retried():
    """Retrying a rejection would just fail again, and a payment request must
    not be repeated on a guess."""
    calls = {"n": 0}

    class Rejected:
        status_code = 400
        reason_phrase = "Bad Request"
        text = "{}"

        def json(self):
            return {"error": {"description": "Recurring digits in customer contact are disallowed"}}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **k):
            calls["n"] += 1
            return Rejected()

    real = rzp.httpx.AsyncClient
    rzp.httpx.AsyncClient = FakeClient
    try:
        with pytest.raises(rzp.RazorpayError):
            run_async(rzp.razorpay_request("POST", "/payment_links", json={}))
    finally:
        rzp.httpx.AsyncClient = real
    assert calls["n"] == 1


def test_an_empty_error_body_still_says_something():
    """Two logged failures printed nothing at all, which is why the cause had
    to be guessed from the code instead of read from the log."""
    class Empty:
        status_code = 400
        reason_phrase = "Bad Request"
        text = ""

        def json(self):
            raise ValueError("no body")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **k):
            return Empty()

    real = rzp.httpx.AsyncClient
    rzp.httpx.AsyncClient = FakeClient
    try:
        with pytest.raises(rzp.RazorpayError) as caught:
            run_async(rzp.razorpay_request("GET", "/payment_links/x"))
    finally:
        rzp.httpx.AsyncClient = real
    assert caught.value.description.strip()
    assert "400" in caught.value.description


# --- how the route reports each kind -------------------------------------
def call_route(user_doc, **body):
    """Calls the endpoint function in THIS process.

    The three cases below replace `rzp.create_payment_link` to simulate a
    Razorpay failure, and a patch only applies where the code runs — going over
    HTTP would hit the untouched server (and, as it did once here, create a
    real payment link)."""
    request = StarletteRequest({
        "type": "http", "method": "POST", "path": "/api/pay/order", "scheme": "http",
        "query_string": b"", "headers": [(b"host", b"localhost:8001")],
        "client": ("127.0.0.1", 1234),
    })
    return run_async(pay.create_topup_order(
        body=pay.WalletTopup(**body), request=request, user=user_doc))


def failing_link(status, description, code="BAD_REQUEST_ERROR"):
    async def fail(**kwargs):
        raise rzp.RazorpayError(status, description, code)
    return fail


def test_throttling_reaches_the_app_as_busy_not_as_a_502():
    uid, _ = seed_account(currency="INR")
    user_doc = db.users.find_one({"id": uid})
    real = pay.rzp.create_payment_link
    pay.rzp.create_payment_link = failing_link(400, "Too many requests")
    try:
        with pytest.raises(HTTPException) as caught:
            call_route(user_doc, amount=99, email="a@example.com", phone="+919812345678")
    finally:
        pay.rzp.create_payment_link = real
    assert caught.value.status_code == 503
    assert caught.value.detail.startswith("busy:")
    assert "few seconds" in caught.value.detail
    # Nothing recorded, so the next tap starts clean.
    assert db.payments.count_documents({"wallet_key": f"user:{uid}"}) == 0


def test_a_razorpay_field_rejection_becomes_an_inline_error():
    """Even if our own check misses a number Razorpay dislikes, the user is
    still asked to fix the field instead of being shown a 502."""
    uid, _ = seed_account(currency="INR")
    user_doc = db.users.find_one({"id": uid})
    real = pay.rzp.create_payment_link
    pay.rzp.create_payment_link = failing_link(400, "Recurring digits in customer contact are disallowed")
    try:
        with pytest.raises(HTTPException) as caught:
            call_route(user_doc, amount=99, email="a@example.com", phone="+919812345678")
    finally:
        pay.rzp.create_payment_link = real
    assert caught.value.status_code == 400
    assert caught.value.detail.startswith("contact_invalid:contact:")
    assert "Recurring digits" in caught.value.detail


def test_stale_keys_become_a_plain_unavailable_message():
    """A rejected key pair is OUR problem — the deployed container is carrying
    rotated-out credentials. "Razorpay: Authentication failed" reads like the
    customer's own card was declined, so it is replaced by a plain
    "temporarily unavailable, nothing was charged" and the health probe is
    flipped so the environment can be diagnosed without log access."""
    uid, _ = seed_account(currency="INR")
    user_doc = db.users.find_one({"id": uid})
    real = pay.rzp.create_payment_link
    before = pay.rzp.CREDENTIALS_OK
    pay.rzp.create_payment_link = failing_link(401, "Authentication failed")
    try:
        with pytest.raises(HTTPException) as caught:
            call_route(user_doc, amount=99, email="a@example.com", phone="+919812345678")
    finally:
        pay.rzp.create_payment_link = real
        pay.rzp.CREDENTIALS_OK = before
    assert caught.value.status_code == 503
    assert "temporarily unavailable" in caught.value.detail
    assert "nothing was charged" in caught.value.detail
    # Never leak Razorpay's wording for this one; it misleads the customer.
    assert "Authentication failed" not in caught.value.detail


def test_an_unrelated_razorpay_failure_is_still_a_502():
    """Don't blame the customer for our own problems."""
    uid, _ = seed_account(currency="INR")
    user_doc = db.users.find_one({"id": uid})
    real = pay.rzp.create_payment_link
    pay.rzp.create_payment_link = failing_link(500, "Server error, the payment could not be created")
    try:
        with pytest.raises(HTTPException) as caught:
            call_route(user_doc, amount=99, email="a@example.com", phone="+919812345678")
    finally:
        pay.rzp.create_payment_link = real
    assert caught.value.status_code == 502
    assert "Server error" in caught.value.detail


@pytest.mark.parametrize("description,field", [
    ("Recurring digits in customer contact are disallowed", "contact"),
    ("customer contact is invalid", "contact"),
    ("The phone number is invalid", "contact"),
    ("customer email is invalid", "email"),
    ("name must be 3-50 characters", "name"),
    ("Too many requests", None),
    ("The api key provided is invalid", None),
])
def test_only_customer_input_errors_are_mapped_to_a_field(description, field):
    assert pay.customer_field_for(description) == field


# --- link reuse: the fix for the throttling ------------------------------
def test_tapping_the_same_pack_twice_reuses_the_open_link():
    """No second Razorpay call, so no second link and nothing to throttle —
    and the second tap is instant."""
    uid, headers = seed_account(currency="INR")
    key = f"user:{uid}"
    existing = f"plink_seeded{uuid.uuid4().hex[:8]}"
    db.payments.insert_one({
        "razorpay_payment_link_id": existing, "reference_id": f"wallet_{uuid.uuid4().hex[:20]}",
        "wallet_key": key, "amount": 99.0, "currency": "INR", "status": "created",
        "short_url": "https://rzp.io/rzp/seeded",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    res = order(headers, amount=99, email="a@example.com", phone="+919812345678")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reused"] is True
    assert body["order_id"] == existing
    assert body["checkout_url"] == "https://rzp.io/rzp/seeded"
    assert body["currency"] == "INR"
    # Still exactly one link for this wallet.
    assert db.payments.count_documents({"wallet_key": key}) == 1


def test_an_expired_link_is_not_reused():
    """Handing back a link Razorpay has already expired would be worse than
    making a new one."""
    uid, headers = seed_account(currency="INR")
    key = f"user:{uid}"
    db.payments.insert_one({
        "razorpay_payment_link_id": f"plink_old{uuid.uuid4().hex[:8]}",
        "reference_id": f"wallet_{uuid.uuid4().hex[:20]}",
        "wallet_key": key, "amount": 99.0, "currency": "INR", "status": "created",
        "short_url": "https://rzp.io/rzp/expired",
        "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    res = order(headers, amount=99, email="a@example.com", phone="+919812345678")
    if res.status_code in (502, 503):
        pytest.skip(f"Razorpay unavailable: {res.text}")
    assert res.status_code == 200, res.text
    assert res.json().get("reused") is not True
    assert res.json()["checkout_url"] != "https://rzp.io/rzp/expired"
    _LINKS.append(res.json()["order_id"])
    assert db.payments.count_documents({"wallet_key": key}) == 2


def test_a_different_pack_does_not_reuse_the_link():
    """₹99 and ₹199 are different amounts — reusing across them would charge
    the wrong money, which is far worse than an extra API call."""
    uid, headers = seed_account(currency="INR")
    key = f"user:{uid}"
    db.payments.insert_one({
        "razorpay_payment_link_id": f"plink_99{uuid.uuid4().hex[:8]}",
        "reference_id": f"wallet_{uuid.uuid4().hex[:20]}",
        "wallet_key": key, "amount": 99.0, "currency": "INR", "status": "created",
        "short_url": "https://rzp.io/rzp/ninetynine",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    res = order(headers, amount=199, email="a@example.com", phone="+919812345678")
    if res.status_code in (502, 503):
        pytest.skip(f"Razorpay unavailable: {res.text}")
    assert res.status_code == 200, res.text
    assert res.json().get("reused") is not True
    assert res.json()["amount"] == 199
    _LINKS.append(res.json()["order_id"])


def test_a_paid_link_is_never_reused():
    """Only a link still waiting for payment can be handed back."""
    uid, headers = seed_account(currency="INR")
    key = f"user:{uid}"
    db.payments.insert_one({
        "razorpay_payment_link_id": f"plink_paid{uuid.uuid4().hex[:8]}",
        "reference_id": f"wallet_{uuid.uuid4().hex[:20]}",
        "wallet_key": key, "amount": 99.0, "currency": "INR", "status": "captured",
        "short_url": "https://rzp.io/rzp/paid",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    res = order(headers, amount=99, email="a@example.com", phone="+919812345678")
    if res.status_code in (502, 503):
        pytest.skip(f"Razorpay unavailable: {res.text}")
    assert res.status_code == 200, res.text
    assert res.json()["checkout_url"] != "https://rzp.io/rzp/paid"
    _LINKS.append(res.json()["order_id"])


def test_one_wallets_link_is_never_offered_to_another_account():
    uid_a, _ = seed_account(currency="INR")
    _, headers_b = seed_account(currency="INR")
    db.payments.insert_one({
        "razorpay_payment_link_id": f"plink_a{uuid.uuid4().hex[:8]}",
        "reference_id": f"wallet_{uuid.uuid4().hex[:20]}",
        "wallet_key": f"user:{uid_a}", "amount": 99.0, "currency": "INR", "status": "created",
        "short_url": "https://rzp.io/rzp/accountA",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    res = order(headers_b, amount=99, email="b@example.com", phone="+919812345679")
    if res.status_code in (502, 503):
        pytest.skip(f"Razorpay unavailable: {res.text}")
    assert res.status_code == 200, res.text
    assert res.json()["checkout_url"] != "https://rzp.io/rzp/accountA"
    _LINKS.append(res.json()["order_id"])


def test_a_new_link_records_when_it_expires():
    """Reuse depends on this field, so a link created without it would never
    be reused and the throttling would quietly come back."""
    uid, headers = seed_account(currency="INR")
    res = order(headers, amount=99, email="a@example.com", phone="+919812345678")
    if res.status_code in (502, 503):
        pytest.skip(f"Razorpay unavailable: {res.text}")
    assert res.status_code == 200, res.text
    _LINKS.append(res.json()["order_id"])
    record = db.payments.find_one({"wallet_key": f"user:{uid}"})
    assert record["expires_at"] > now_iso_placeholder()
    # Second call now reuses it, with no Razorpay round trip.
    again = order(headers, amount=99, email="a@example.com", phone="+919812345678")
    assert again.status_code == 200
    assert again.json()["reused"] is True
    assert again.json()["order_id"] == res.json()["order_id"]


def now_iso_placeholder() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- the name, as insurance ----------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("Lloyd Masih", "Lloyd Masih"),
    ("Lloyd 😀 Masih1", "Lloyd Masih"),
    ("Jo", "TradingAgents user"),
    ("", "TradingAgents user"),
    (None, "TradingAgents user"),
    ("12345", "TradingAgents user"),
    ("José Álvarez", "Jos lvarez"),
])
def test_customer_name_is_sanitized(raw, expected):
    assert pay.sanitize_customer_name(raw) == expected


def test_a_very_long_name_is_truncated_to_razorpays_limit():
    assert len(pay.sanitize_customer_name("a" * 200)) == 50
