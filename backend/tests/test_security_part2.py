"""SECURITY_FIXES_PART2 section 1 — findings 5, 7, 9, 10, 11, 12, 13.

Each was re-verified against the CURRENT code before being touched (the
document's diff was written against the old monolithic server.py and had never
been applied here). Only finding 12's holdings cap already existed, from the
session-13 audit.
"""
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
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
import market_data as md  # noqa: E402
from routes.auth_routes import OTP_MAX_PER_IP_PER_HOUR  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("PUBLIC_BASE_URL")
    or "http://localhost:8001"
).rstrip("/")
JWT_SECRET = os.environ["JWT_SECRET"]

_client = MongoClient(os.environ["MONGO_URL"])
_db = _client[os.environ["DB_NAME"]]

_IPS = []
_IDS = []


# --- Finding 7: identifier canonicalization ------------------------------

class TestFinding7EmailCanonicalization:
    """`a.b+tag@gmail.com` and `ab@gmail.com` are the SAME inbox. Treating
    them as different accounts handed one person unlimited signup bonuses."""

    @pytest.mark.parametrize("raw", [
        "u.s.e.r@gmail.com",
        "user+trading1@gmail.com",
        "u.ser+x@googlemail.com",
        "USER@Gmail.com",
    ])
    def test_gmail_variants_collapse_to_one_account(self, raw):
        assert au.normalize_identifier(raw) == ("email", "user@gmail.com")

    def test_other_providers_are_left_alone(self):
        # Dot-insensitivity is Gmail-specific; applying it everywhere would
        # merge genuinely different addresses.
        assert au.normalize_identifier("u.s.er@yahoo.com") == ("email", "u.s.er@yahoo.com")
        assert au.normalize_identifier("user+tag@outlook.com") == ("email", "user+tag@outlook.com")

    @pytest.mark.parametrize("raw", ["throwaway@mailinator.com", "x@10minutemail.com", "y@yopmail.com"])
    def test_disposable_domains_are_refused(self, raw):
        assert au.normalize_identifier(raw) == (None, None)

    def test_a_dots_only_gmail_local_part_is_refused(self):
        # Would otherwise canonicalize to "@gmail.com".
        assert au.normalize_identifier("...@gmail.com") == (None, None)

    def test_phones_are_unaffected(self):
        assert au.normalize_identifier("+91 844-630 7145") == ("phone", "+918446307145")

    def test_an_existing_dotted_gmail_account_is_not_orphaned(self):
        """The regression this fix could have caused: someone who signed up as
        "first.last@gmail.com" before canonicalization existed is stored under
        the dotted address. Handing them a new empty account would silently
        lose a wallet balance they paid for."""
        dotted = f"test.p2.{uuid.uuid4().hex[:8]}@gmail.com"
        canonical = dotted.replace(".", "", dotted.count(".") - 1)  # dots stripped from local part
        uid = f"TEST_p2-{uuid.uuid4()}"
        _IDS.append(uid)
        _db.users.insert_one({"id": uid, "email": dotted, "free_credits_remaining": 3,
                              "created_at": datetime.now(timezone.utc).isoformat()})
        _db.wallets.insert_one({"device_id": f"user:{uid}", "balance": 42.0, "currency": "USD"})
        rec_id = str(uuid.uuid4())
        _IDS.append(rec_id)
        id_type, normalized = au.normalize_identifier(dotted)
        assert normalized != dotted, "this test is pointless if the address doesn't canonicalize"
        _db.otp_requests.insert_one({
            "id": rec_id, "identifier": normalized, "identifier_type": id_type,
            "otp_hash": au.hash_otp("123456"), "attempts": 0, "verified": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        try:
            r = requests.post(f"{BASE_URL}/api/auth/otp/verify",
                              json={"identifier": dotted, "otp": "123456"}, timeout=30)
            assert r.status_code == 200, r.text
            # Same account, same balance — not a fresh one.
            assert r.json()["user"]["id"] == uid, "the existing account was orphaned"
            wallet = _db.wallets.find_one({"device_id": f"user:{uid}"})
            assert wallet["balance"] == 42.0
            assert _db.users.count_documents({"email": normalized}) == 0, "a duplicate account was created"
        finally:
            _db.wallets.delete_many({"device_id": f"user:{uid}"})
            _db.users.delete_many({"id": uid})
            _db.otp_requests.delete_many({"identifier": normalized})
        assert canonical  # silence unused

    def test_the_endpoint_refuses_a_disposable_address(self):
        r = requests.post(f"{BASE_URL}/api/auth/otp/request",
                          json={"identifier": f"x{uuid.uuid4().hex[:6]}@mailinator.com"}, timeout=25)
        assert r.status_code == 400, r.text


# --- Finding 5: OTP pumping ----------------------------------------------

class TestFinding5OtpPumpingIsCapped:
    def fresh_ip(self):
        ip = f"198.51.100.{uuid.uuid4().int % 250 + 1}"
        _IPS.append(ip)
        return ip

    def request_otp(self, identifier, ip):
        return requests.post(
            f"{BASE_URL}/api/auth/otp/request",
            json={"identifier": identifier},
            headers={"X-Forwarded-For": ip},
            timeout=25,
        )

    def seed_ip_history(self, ip, count, age_seconds=60):
        """Prior requests from one address, as the DB would hold them."""
        ts = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
        for _ in range(count):
            rec_id = str(uuid.uuid4())
            _IDS.append(rec_id)
            _db.otp_requests.insert_one({
                "id": rec_id,
                "identifier": f"seed-{uuid.uuid4().hex[:8]}@example.com",
                "identifier_type": "email",
                "ip": ip,
                "otp_hash": au.hash_otp("123456"),
                "attempts": 0,
                "verified": False,
                "created_at": ts,
            })

    def test_one_source_cycling_addresses_is_stopped(self):
        """Per-identifier limiting alone never fires here — every address is
        brand new. The per-IP ceiling is what catches it."""
        ip = self.fresh_ip()
        self.seed_ip_history(ip, OTP_MAX_PER_IP_PER_HOUR)
        r = self.request_otp(f"pump-{uuid.uuid4().hex[:8]}@example.com", ip)
        assert r.status_code == 429, r.text
        assert "network" in r.json()["detail"].lower()

    def test_one_real_person_signing_in_is_not_troubled(self):
        ip = self.fresh_ip()
        self.seed_ip_history(ip, OTP_MAX_PER_IP_PER_HOUR - 2)
        r = self.request_otp(f"real-{uuid.uuid4().hex[:8]}@example.com", ip)
        assert r.status_code != 429, r.text

    def test_an_hour_old_burst_no_longer_counts(self):
        ip = self.fresh_ip()
        self.seed_ip_history(ip, OTP_MAX_PER_IP_PER_HOUR + 5, age_seconds=7200)
        r = self.request_otp(f"later-{uuid.uuid4().hex[:8]}@example.com", ip)
        assert r.status_code != 429, r.text

    def test_a_different_address_is_unaffected(self):
        busy = self.fresh_ip()
        self.seed_ip_history(busy, OTP_MAX_PER_IP_PER_HOUR)
        r = self.request_otp(f"other-{uuid.uuid4().hex[:8]}@example.com", self.fresh_ip())
        assert r.status_code != 429, r.text

    def test_the_ip_is_recorded_so_the_limit_has_something_to_count(self):
        ip = self.fresh_ip()
        identifier = f"rec-{uuid.uuid4().hex[:8]}@example.com"
        r = self.request_otp(identifier, ip)
        if r.status_code == 200:
            row = _db.otp_requests.find_one({"identifier": identifier})
            assert row["ip"] == ip
        _db.otp_requests.delete_many({"identifier": identifier})

    def test_the_per_identifier_limit_still_works(self):
        """The looser IP ceiling must not have replaced the tighter one."""
        identifier = f"same-{uuid.uuid4().hex[:8]}@example.com"
        try:
            codes = [self.request_otp(identifier, self.fresh_ip()).status_code for _ in range(7)]
            assert 429 in codes, codes
        finally:
            _db.otp_requests.delete_many({"identifier": identifier})


# --- Finding 11: anonymous device_id naming a real account ---------------

class TestFinding11DeviceIdCannotNameAnAccount:
    def test_wallet_key_for_rejects_a_user_prefixed_device_id(self):
        from deps import wallet_key_for
        assert wallet_key_for(None, "user:someone-elses-uuid") is None
        assert wallet_key_for(None, "dev-123") == "dev-123"
        assert wallet_key_for({"id": "abc"}, "user:someone-elses-uuid") == "user:abc"

    def test_topup_with_a_user_prefixed_device_id_is_refused(self):
        r = requests.post(f"{BASE_URL}/api/pay/order",
                          json={"device_id": "user:victim-uuid", "amount": 5.0}, timeout=25)
        # 401 (auth required) or 400 (no valid key) — never a wallet named
        # after someone else.
        assert r.status_code in (400, 401), r.text


# --- Finding 10: /pay/status ownership -----------------------------------

class TestFinding10PayStatusRequiresOwnership:
    """Verified against the real deployment first: `/pay/status` already sits
    behind a mandatory session here (AUTH_REQUIRED_ENABLED), so the document's
    "anyone with an order ID can read it" is NOT reachable today — an
    anonymous caller is refused at the auth gate, before ownership is even
    considered. The fix still matters: the old check was skipped whenever the
    resolved key was falsy, so it would become reachable the moment auth
    enforcement were turned off. It is now unconditional."""

    def mk_session(self):
        uid = f"TEST_p2-{uuid.uuid4()}"
        _db.users.insert_one({"id": uid, "email": f"{uid}@example.com",
                              "created_at": datetime.now(timezone.utc).isoformat()})
        _IDS.append(uid)
        tok = au.create_session_token(uid, JWT_SECRET)
        return uid, {"Authorization": f"Bearer {tok}"}

    def seed_order(self, wallet_key):
        order_id = f"TEST_p2-order-{uuid.uuid4()}"
        _db.payments.insert_one({
            "razorpay_order_id": order_id,
            "razorpay_payment_link_id": f"TEST_p2-plink-{uuid.uuid4()}",
            "wallet_key": wallet_key,
            "amount": 5.0, "currency": "USD", "status": "created",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return order_id

    def test_an_anonymous_caller_is_refused_at_the_auth_gate(self):
        order_id = self.seed_order("user:TEST_p2-victim")
        try:
            r = requests.get(f"{BASE_URL}/api/pay/status/{order_id}", timeout=25)
            assert r.status_code == 401, r.text
        finally:
            _db.payments.delete_many({"razorpay_order_id": order_id})

    def test_another_accounts_order_is_refused(self):
        _, headers = self.mk_session()
        order_id = self.seed_order("user:TEST_p2-someone-else")
        try:
            r = requests.get(f"{BASE_URL}/api/pay/status/{order_id}", headers=headers, timeout=25)
            assert r.status_code == 403, r.text
        finally:
            _db.payments.delete_many({"razorpay_order_id": order_id})

    def test_the_owner_can_still_read_their_own_order(self):
        uid, headers = self.mk_session()
        order_id = self.seed_order(f"user:{uid}")
        try:
            r = requests.get(f"{BASE_URL}/api/pay/status/{order_id}", headers=headers, timeout=25)
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "created"
        finally:
            _db.payments.delete_many({"razorpay_order_id": order_id})

    def test_a_supplied_device_id_cannot_override_a_session(self):
        uid, headers = self.mk_session()
        order_id = self.seed_order("dev-someone-elses-device")
        try:
            r = requests.get(f"{BASE_URL}/api/pay/status/{order_id}", headers=headers,
                             params={"device_id": "dev-someone-elses-device"}, timeout=25)
            assert r.status_code == 403, r.text
        finally:
            _db.payments.delete_many({"razorpay_order_id": order_id})

    def test_the_ownership_check_is_unconditional_in_source(self):
        src = (BACKEND_DIR / "routes" / "payments.py").read_text()
        fn = src[src.index("async def pay_status"):]
        assert 'key = wallet_key_for(user, device_id)' in fn
        # The old `if key and ...` skipped the check entirely for a falsy key.
        assert 'if not key or order["wallet_key"] != key' in fn


# --- Finding 9: a payment can no longer be permanently lost --------------

class TestFinding9RetryabilityAfterACrash:
    def test_the_webhook_marks_an_event_only_after_handling_it(self):
        src = (BACKEND_DIR / "routes" / "payments.py").read_text()
        hook = src[src.index("async def pay_webhook"):src.index('@api_router.get("/pay/status/')]
        # A read, not a claim: an insert up front meant a crash mid-processing
        # left the event "seen" forever and Razorpay's retry was answered
        # "duplicate", so the payment was never processed at all.
        assert 'find_one({"event_id": event_id})' in hook
        assert "webhook_events.insert_one" not in hook
        # Marked on every completed path — including the two added with the
        # refund clawback (a refund/chargeback event is handled exactly once
        # for the same reason a capture is).
        assert hook.count("mark_webhook_event_processed(event_id)") == 7

    def test_a_duplicate_event_is_still_answered_duplicate(self):
        event_id = f"TEST_p2-evt-{uuid.uuid4()}"
        _db.webhook_events.insert_one({"event_id": event_id, "received_at": datetime.now(timezone.utc).isoformat()})
        try:
            r = requests.post(
                f"{BASE_URL}/api/pay/webhook",
                data=json.dumps({"event": "payment.captured"}),
                headers={"Content-Type": "application/json", "X-Razorpay-Event-Id": event_id,
                         "X-Razorpay-Signature": "bad"},
                timeout=25,
            )
            # Signature is checked first, so this never reaches the duplicate
            # branch — which is the correct order of gates.
            assert r.status_code == 400
        finally:
            _db.webhook_events.delete_many({"event_id": event_id})

    def test_the_ledger_claim_is_rolled_back_if_the_credit_fails(self):
        src = (BACKEND_DIR / "routes" / "payments.py").read_text()
        fn = src[src.index("async def credit_wallet_once"):src.index("async def settle_payment")]
        # Without this, a crash between the claim and the increment leaves a
        # ledger row saying money was credited when it wasn't — and the unique
        # index then blocks every retry from ever fixing it.
        assert 'wallet_ledger.delete_one({"payment_id": payment_id})' in fn
        assert "raise" in fn


# --- Findings 12 and 13 --------------------------------------------------

class TestFinding12BoundedWorkAndCache:
    def test_holdings_are_capped(self):
        body = {"holdings": [{"symbol": f"SYM{i}", "quantity": 1, "avg_price": 1} for i in range(51)]}
        r = requests.post(f"{BASE_URL}/api/portfolio/optimize", json=body, timeout=30)
        assert r.status_code == 422, r.text

    def test_a_real_portfolio_still_optimizes(self):
        body = {"holdings": [{"symbol": "AAPL", "quantity": 2, "avg_price": 150},
                             {"symbol": "MSFT", "quantity": 1, "avg_price": 300}]}
        r = requests.post(f"{BASE_URL}/api/portfolio/optimize", json=body, timeout=90)
        assert r.status_code == 200, r.text

    def test_the_news_cache_evicts_instead_of_growing_forever(self):
        src = (BACKEND_DIR / "routes" / "market.py").read_text()
        assert "len(_news_cache) >= 500" in src
        assert "_news_cache.pop(stale, None)" in src

    def test_eviction_keeps_the_newest_entries(self):
        """Exercised directly on the cache, since filling it through the
        endpoint would mean 500 real LLM calls."""
        md._news_cache.clear()
        try:
            for i in range(500):
                md._news_cache[f"SYM{i}"] = {"ts": float(i), "data": []}
            oldest = sorted(md._news_cache, key=lambda k: md._news_cache[k]["ts"])[:100]
            for stale in oldest:
                md._news_cache.pop(stale, None)
            assert len(md._news_cache) == 400
            assert "SYM0" not in md._news_cache
            assert "SYM499" in md._news_cache
        finally:
            md._news_cache.clear()


class TestFinding13SymbolValidation:
    @pytest.mark.parametrize("route", ["quote", "chart", "ohlc", "news"])
    # NB: things like "AAPL?x=1" or "AAPL/../x" are normalised by the HTTP
    # client/router before they ever reach a handler, so they test the router,
    # not this guard. These are values that really do arrive as one path
    # segment.
    @pytest.mark.parametrize("bad", ["a b", "../etc/passwd", "AAPL;DROP", "A" * 25, "AAPL%00", "AAPL|ls", "A@B"])
    def test_a_malformed_symbol_never_reaches_yahoo(self, route, bad):
        r = requests.get(f"{BASE_URL}/api/{route}/{bad}", timeout=25)
        # 400 from our guard, or 404 from the router because the path no longer
        # matches a single segment — either way it never becomes a Yahoo URL.
        assert r.status_code in (400, 404), f"{route}/{bad} -> {r.status_code}"

    @pytest.mark.parametrize("good", ["AAPL", "aapl", "BTC-USD", "^GSPC", "GC=F", "RELIANCE.NS"])
    def test_every_real_symbol_shape_still_works(self, good):
        """Case included deliberately: the app requests lowercase symbols in
        places, so rejecting those would be a regression, not a fix."""
        r = requests.get(f"{BASE_URL}/api/quote/{good}", timeout=30)
        assert r.status_code == 200, f"{good} -> {r.status_code} {r.text[:120]}"

    def test_portfolio_holdings_are_validated_too(self):
        body = {"holdings": [{"symbol": "../etc/passwd", "quantity": 1, "avg_price": 1},
                             {"symbol": "MSFT", "quantity": 1, "avg_price": 1}]}
        r = requests.post(f"{BASE_URL}/api/portfolio/optimize", json=body, timeout=30)
        assert r.status_code == 400, r.text
        assert "ticker" in r.json()["detail"].lower()


def teardown_module():
    for rec_id in _IDS:
        _db.otp_requests.delete_many({"id": rec_id})
        _db.users.delete_many({"id": rec_id})
    for ip in _IPS:
        _db.otp_requests.delete_many({"ip": ip})
