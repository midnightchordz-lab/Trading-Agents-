"""Unit tests for auth.py (pure functions, no DB/network)."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth


def test_normalize_email():
    t, v = auth.normalize_identifier("  User@Example.com ")
    assert t == "email" and v == "user@example.com"


def test_normalize_phone():
    t, v = auth.normalize_identifier("+1 (415) 555-0134")
    assert t == "phone" and v == "+14155550134"


def test_normalize_garbage_returns_none():
    t, v = auth.normalize_identifier("not an identifier")
    assert t is None and v is None
    t2, v2 = auth.normalize_identifier("")
    assert t2 is None and v2 is None


def test_generate_otp_is_six_digits():
    otp = auth.generate_otp()
    assert len(otp) == 6
    assert otp.isdigit()


def test_generate_otp_is_reasonably_random():
    otps = {auth.generate_otp() for _ in range(50)}
    assert len(otps) > 40  # extremely unlikely to collide this much by chance


def test_hash_and_verify_otp_roundtrip():
    otp = "482913"
    hashed = auth.hash_otp(otp)
    assert hashed != otp
    assert auth.verify_otp_code(otp, hashed) is True
    assert auth.verify_otp_code("000000", hashed) is False


def test_otp_expiry():
    now = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
    assert auth.is_otp_expired(now, ttl_seconds=300) is False
    assert auth.is_otp_expired(old, ttl_seconds=300) is True
    assert auth.is_otp_expired("garbage") is True


def test_rate_limiting():
    now = datetime.now(timezone.utc)
    recent = [(now - timedelta(minutes=i)).isoformat() for i in range(5)]
    assert auth.is_rate_limited(recent, window_seconds=3600, max_requests=5) is True
    assert auth.is_rate_limited(recent[:2], window_seconds=3600, max_requests=5) is False
    old = [(now - timedelta(hours=5)).isoformat() for _ in range(10)]
    assert auth.is_rate_limited(old, window_seconds=3600, max_requests=5) is False


def test_session_token_roundtrip():
    token = auth.create_session_token("user123", secret="testsecret")
    payload = auth.decode_session_token(token, secret="testsecret")
    assert payload is not None
    assert payload["sub"] == "user123"


def test_session_token_wrong_secret_fails():
    token = auth.create_session_token("user123", secret="testsecret")
    payload = auth.decode_session_token(token, secret="wrongsecret")
    assert payload is None


def test_session_token_garbage_fails():
    assert auth.decode_session_token("not.a.token", secret="testsecret") is None


def test_placeholders_return_none():
    assert auth.send_otp_stub("+14155550134", "phone", "123456") is None
    assert auth.verify_google_id_token_stub("fake", "client-id") is None
    assert auth.verify_apple_id_token_stub("fake", "service-id") is None
