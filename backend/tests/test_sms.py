"""Tests for the Twilio SMS helper (no network — delivery itself is not called)."""
import asyncio
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


class _TwilioLikeError(Exception):
    """Shape of twilio.base.exceptions.TwilioRestException: a numeric .code and
    a str() that carries the raw API text, including a docs URL."""

    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


def _send_failing_with(monkeypatch, exc):
    monkeypatch.setattr(sms, "sms_configured", lambda: True)

    def boom(to, body):
        raise exc

    monkeypatch.setattr(sms, "_send_sync", boom)
    return asyncio.run(sms.send_otp_sms("+919876543210", "123456", 5, "TradingAgents"))


RAW = ("HTTP 400 error: Unable to create record: Permission to send an SMS has not been enabled "
       "for the region indicated by the 'To' number: +2136751. "
       "More information: https://www.twilio.com/docs/errors/21408")


def test_twilio_text_and_urls_never_reach_the_user(monkeypatch):
    for code in (21211, 21408, 21614, 21265, 21610, 21608, 99999):
        delivered, err = _send_failing_with(monkeypatch, _TwilioLikeError(code, RAW))
        assert delivered is False
        assert "http" not in err.lower(), err
        assert "twilio.com" not in err.lower(), err
        assert "unable to create record" not in err.lower(), err


def test_region_not_enabled_points_to_email(monkeypatch):
    _, err = _send_failing_with(monkeypatch, _TwilioLikeError(21408, RAW))
    assert "email" in err and err in sms.INPUT_ERRORS


def test_invalid_number_is_an_input_error(monkeypatch):
    _, err = _send_failing_with(monkeypatch, _TwilioLikeError(21211, "HTTP 400 error: ... is not a valid phone number"))
    assert err in sms.INPUT_ERRORS


def test_code_found_in_text_when_exception_has_no_code_attr(monkeypatch):
    _, err = _send_failing_with(monkeypatch, RuntimeError(RAW))
    assert err in sms.INPUT_ERRORS and "http" not in err.lower()


def test_provider_outage_is_not_blamed_on_the_user(monkeypatch):
    _, err = _send_failing_with(monkeypatch, _TwilioLikeError(20500, "HTTP 500 error: internal"))
    assert err not in sms.INPUT_ERRORS and "http" not in err.lower()
