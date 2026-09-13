"""Tests for the OTP email template and its guardrail gate."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mailer as m


def test_otp_template_contains_code_and_brand():
    html = m.otp_email_html("482913", 5)
    assert "482913" in html
    assert m.EMAIL_FROM_NAME in html
    assert "5 minutes" in html


def test_otp_template_passes_the_guardrail_gate():
    m._assert_safe_email(f"Your {m.EMAIL_FROM_NAME} sign-in code", m.otp_email_html("482913", 5))


def test_otp_template_has_no_forms_or_links():
    html = m.otp_email_html("482913", 5)
    for bad in ("<form", "<input", "href=", "src="):
        assert bad not in html


def test_gate_rejects_forms():
    with pytest.raises(ValueError):
        m._assert_safe_email("subject", "<form><input name='pw'></form>")


def test_gate_rejects_credential_asks():
    with pytest.raises(ValueError):
        m._assert_safe_email("subject", "<p>Please reply with the code we sent.</p>")


def test_gate_rejects_non_https_links():
    with pytest.raises(ValueError):
        m._assert_safe_email("subject", '<a href="http://example.com">click</a>')


def test_gate_rejects_misleading_anchor_text():
    with pytest.raises(ValueError):
        m._assert_safe_email("subject", '<a href="https://evil.example">paypal.com</a>')


def test_email_configured_reflects_key():
    assert m.email_configured() is bool(m.EMAIL_KEY)
