"""Tests for the Twilio SMS helper (no network — delivery itself is not called)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sms


def test_e164_validation():
    assert sms.is_e164("+14155550134") is True
    assert sms.is_e164("+19064011655") is True
    assert sms.is_e164("4155550134") is False
    assert sms.is_e164("+0415555013") is False
    assert sms.is_e164("") is False
    assert sms.is_e164("+1 415 555 0134") is False


def test_message_contains_code_ttl_and_brand():
    body = sms.otp_message("482913", 5, "TradingAgents")
    assert "482913" in body
    assert "5 minutes" in body
    assert "TradingAgents" in body


def test_message_never_asks_for_the_code_back():
    body = sms.otp_message("482913", 5, "TradingAgents").lower()
    for bad in ("reply with", "send us", "share this code"):
        assert bad not in body


def test_configured_reflects_env():
    assert sms.sms_configured() is bool(
        sms.TWILIO_ACCOUNT_SID and sms.TWILIO_AUTH_TOKEN and sms.TWILIO_FROM_NUMBER
    )


def test_bad_number_is_rejected_before_any_network_call():
    import asyncio

    delivered, err = asyncio.run(sms.send_otp_sms("4155550134", "123456", 5, "TradingAgents"))
    assert delivered is False
    assert "country code" in err
