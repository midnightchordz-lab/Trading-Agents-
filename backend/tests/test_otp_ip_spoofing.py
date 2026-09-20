"""The per-IP OTP ceiling trusted a header the client controls.

`client_ip()` read the LEFT-most `x-forwarded-for` entry. Every proxy APPENDS
to that header, so the left-most entry is whatever the caller typed — meaning a
pumping attack sends a different `X-Forwarded-For` on every request, never
reaches the 20/hour per-IP ceiling, and changes the identifier every time so
the 5/hour per-identifier limit never applies either. Unlimited SMS on our
Twilio balance, to strangers' phones, under our brand. The signup free-credit
throttle keyed on the same value.

Verified against the live ingress before writing any code: a request sending
`X-Forwarded-For: 1.2.3.4` reached the backend as
`1.2.3.4,<real client>,<cloudflare>,<load balancer>`, and a request sending
nothing reached it as `<real client>,<cloudflare>,<load balancer>`. So the
injected junk lands on the LEFT and the trusted hop count (2) from the RIGHT
always lands on the address our own edge observed.

These tests cover the parsing directly (the one thing that decides whether the
limit can be bypassed) plus the two whole-deployment budgets, which are the
only defence against a distributed source.
"""
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
from fastapi import HTTPException
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from routes import auth_routes as ar  # noqa: E402
from tests.async_loop import run_async  # noqa: E402

BASE_URL = (os.environ.get("EXPO_PUBLIC_BACKEND_URL") or "http://localhost:8001").rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "test_database")]


class FakeRequest:
    def __init__(self, xff=None, peer="10.0.0.9"):
        self.headers = {"x-forwarded-for": xff} if xff is not None else {}
        self.client = type("C", (), {"host": peer})()


# --- the parsing, which is the whole vulnerability -------------------------

def test_the_real_ingress_chain_yields_the_real_client():
    """Exactly what the live edge produces for an honest request."""
    assert ar.TRUSTED_PROXY_HOPS == 2
    ip = ar.client_ip(FakeRequest("34.16.56.64,104.22.64.124,34.160.159.238"))
    assert ip == "34.16.56.64"


def test_a_spoofed_entry_is_ignored():
    """The attack: pick a new address per request. The injected value lands to
    the LEFT of the real one, so it must not be what we count on."""
    chain = "1.2.3.4,34.16.56.64,104.22.64.124,34.160.159.238"
    assert ar.client_ip(FakeRequest(chain)) == "34.16.56.64"


def test_a_long_forged_chain_still_yields_the_real_client():
    """Padding the header with many entries must not shift the answer — the
    count is from the right, so the length of the forgery is irrelevant."""
    forged = ",".join(f"10.1.1.{n}" for n in range(1, 40))
    chain = f"{forged},34.16.56.64,104.22.64.124,34.160.159.238"
    assert ar.client_ip(FakeRequest(chain)) == "34.16.56.64"


def test_every_forged_value_maps_to_the_SAME_key():
    """The point of the fix, stated as the attacker's experience: 50 requests
    each claiming a different address all count against one bucket."""
    keys = {
        ar.client_ip(FakeRequest(f"203.0.113.{n},34.16.56.64,104.22.64.124,34.160.159.238"))
        for n in range(1, 51)
    }
    assert keys == {"34.16.56.64"}


def test_a_chain_shorter_than_the_trusted_hops_uses_the_socket_peer():
    """Not through the expected edge — a topology change, or a direct call.
    The left-most entry must NEVER be used, so the socket peer (which no caller
    can forge) is used instead."""
    assert ar.client_ip(FakeRequest("203.0.113.5", peer="10.0.0.9")) == "10.0.0.9"
    assert ar.client_ip(FakeRequest("203.0.113.5,198.51.100.2", peer="10.0.0.9")) == "10.0.0.9"


def test_a_loopback_caller_may_declare_its_address():
    """The in-container test path. Nothing outside the container can reach
    127.0.0.1 — ingress traffic arrives from the pod network (verified: peer
    10.79.x.x) — so trusting a declared address from a loopback peer is not an
    attack surface, and it is what lets the suite behave like many clients."""
    assert ar.client_ip(FakeRequest("203.0.113.5", peer="127.0.0.1")) == "203.0.113.5"
    assert ar.client_ip(FakeRequest("203.0.113.5", peer="::1")) == "203.0.113.5"
    # Even from loopback, a full chain is still read from the right.
    chain = "1.2.3.4,34.16.56.64,104.22.64.124,34.160.159.238"
    assert ar.client_ip(FakeRequest(chain, peer="127.0.0.1")) == "34.16.56.64"


def test_no_header_uses_the_socket_peer():
    assert ar.client_ip(FakeRequest(None, peer="10.0.0.9")) == "10.0.0.9"
    assert ar.client_ip(FakeRequest("", peer="10.0.0.9")) == "10.0.0.9"


def test_whitespace_and_empty_entries_dont_shift_the_count():
    ip = ar.client_ip(FakeRequest(" 1.2.3.4 , , 34.16.56.64 , 104.22.64.124 , 34.160.159.238 "))
    assert ip == "34.16.56.64"


def test_no_request_is_empty_not_an_exception():
    assert ar.client_ip(None) == ""


# --- the global budgets, the only defence against a distributed source -----

def _iso(minutes_ago=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture
def clean_otp_window():
    """Each budget test owns the whole last hour, because the thing under test
    IS a global count. Only synthetic rows are removed."""
    tag = f"budget-{uuid.uuid4().hex[:8]}"
    yield tag
    db.otp_requests.delete_many({"identifier": {"$regex": "^budget-"}})


def seed(n, identifier_type, tag, minutes_ago=1):
    db.otp_requests.insert_many([{
        "id": str(uuid.uuid4()),
        "identifier": f"{tag}-{i}",
        "identifier_type": identifier_type,
        # 198.18.0.0/15 (benchmark range), deliberately NOT 198.51.100.0/24:
        # that /24 is what test_security_part2 draws its own addresses
        # from, and seeding rows there made ITS tests hit the per-IP
        # ceiling — a 429 in a test that had nothing to do with this one.
        "ip": f"198.18.{i // 250 % 250}.{i % 250}",
        "otp_hash": "x",
        "attempts": 0,
        "verified": False,
        "created_at": _iso(minutes_ago),
    } for i in range(n)])


def request_otp(identifier, ip="203.0.113.77"):
    return requests.post(
        f"{BASE_URL}/api/auth/otp/request",
        json={"identifier": identifier},
        # A fresh address per request — the exact bypass this fix removes, and
        # over the loopback it still lands in the lenient branch, so the
        # per-IP ceiling genuinely cannot catch these. Only the global budget
        # can, which is what makes this the honest test of it.
        headers={"X-Forwarded-For": ip},
        timeout=30,
    )


class FakeRoutedRequest:
    def __init__(self, ip):
        self.headers = {"x-forwarded-for": ip}
        self.client = type("C", (), {"host": "127.0.0.1"})()


def call_otp_request(identifier, ip):
    """The route function directly, rather than over HTTP.

    A whole-deployment budget is whole-deployment: the first version of these
    tests seeded 1000 real `otp_requests` rows to reach the cap, which made
    every OTHER test running in parallel see an exhausted budget and fail with
    a 429 that had nothing to do with them (confirmed in the backend log:
    "GLOBAL EMAIL OTP BUDGET REACHED: 1001 codes"). Calling in-process lets the
    cap itself be lowered for the length of one test instead, so only a couple
    of rows exist and nothing else is disturbed.
    """
    from routes.auth_routes import OtpRequest, auth_otp_request
    return run_async(auth_otp_request(OtpRequest(identifier=identifier), FakeRoutedRequest(ip)))


def ambient(identifier_type):
    """Codes already sent in the last hour by whatever else is running. The cap
    has to be set RELATIVE to this: a fixed low cap is already exceeded by the
    suite's own traffic, and a fixed high one needs 1000 seeded rows, which is
    what disturbed other tests in the first place."""
    return db.otp_requests.count_documents(
        {"identifier_type": identifier_type, "created_at": {"$gt": _iso(60)}})


def cap_just_above_ambient(monkeypatch, attr, identifier_type, headroom):
    cap = ambient(identifier_type) + headroom
    monkeypatch.setattr(ar, attr, cap)
    return cap


def exhaust(monkeypatch, attr, identifier_type, tag, rows=2):
    """Put the budget exactly at its cap, using a couple of rows instead of a
    thousand."""
    cap_just_above_ambient(monkeypatch, attr, identifier_type, rows)
    seed(rows, identifier_type, tag)


def test_sms_budget_stops_a_distributed_pump_but_leaves_email_working(clean_otp_window, monkeypatch):
    tag = clean_otp_window
    exhaust(monkeypatch, "OTP_SMS_GLOBAL_MAX_PER_HOUR", "phone", tag)
    with pytest.raises(HTTPException) as caught:
        call_otp_request("+919812345678", "203.0.113.31")
    assert caught.value.status_code == 503
    assert "email" in caught.value.detail.lower()

    # The app must stay usable: the SMS budget does not touch email sign-in,
    # so nobody is locked out by it. A 502 here is the email provider refusing
    # a made-up address, which is not what this test is about — a 429/503 would
    # be.
    cap_just_above_ambient(monkeypatch, "OTP_EMAIL_GLOBAL_MAX_PER_HOUR", "email", 50)
    try:
        call_otp_request(f"{uuid.uuid4().hex[:10]}@gmail.com", "203.0.113.32")
    except HTTPException as e:
        assert e.status_code not in (429, 503), e.detail


def test_the_email_budget_stops_email_pumping_too(clean_otp_window, monkeypatch):
    tag = clean_otp_window
    exhaust(monkeypatch, "OTP_EMAIL_GLOBAL_MAX_PER_HOUR", "email", tag)
    with pytest.raises(HTTPException) as caught:
        call_otp_request(f"{uuid.uuid4().hex[:10]}@gmail.com", "203.0.113.33")
    assert caught.value.status_code == 429


def test_an_hour_old_burst_does_not_count(clean_otp_window, monkeypatch):
    """A budget that never forgets would take sign-in offline permanently after
    one attack."""
    tag = clean_otp_window
    cap_just_above_ambient(monkeypatch, "OTP_EMAIL_GLOBAL_MAX_PER_HOUR", "email", 5)
    seed(50, "email", tag, minutes_ago=61)  # an hour-old burst, far over the cap
    try:
        call_otp_request(f"{uuid.uuid4().hex[:10]}@gmail.com", "203.0.113.34")
    except HTTPException as e:
        assert e.status_code not in (429, 503), f"an expired burst still counted: {e.detail}"


def test_normal_traffic_is_nowhere_near_the_budget(clean_otp_window):
    """Sized as a backstop, not a throttle: one real person signing in must
    never see it."""
    assert ar.OTP_SMS_GLOBAL_MAX_PER_HOUR >= 30
    assert ar.OTP_EMAIL_GLOBAL_MAX_PER_HOUR >= 100
    assert ar.OTP_EMAIL_GLOBAL_MAX_PER_HOUR > ar.OTP_SMS_GLOBAL_MAX_PER_HOUR
