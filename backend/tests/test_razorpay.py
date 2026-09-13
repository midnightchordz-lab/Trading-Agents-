"""Tests for the Razorpay helper (signatures, HTML, config) — no live calls."""
import hashlib
import hmac
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import razorpay_pay as rzp
import wallet as w


def test_configured_and_live_mode():
    assert rzp.payments_configured() is bool(rzp.KEY_ID and rzp.KEY_SECRET)
    if rzp.KEY_ID:
        assert rzp.is_live_mode() is rzp.KEY_ID.startswith("rzp_live_")


def test_topup_packs_validation():
    assert w.is_valid_topup(5.0) is True
    assert w.is_valid_topup(25.0) is True
    assert w.is_valid_topup(7.0) is False
    assert w.is_valid_topup(0.0) is False
    assert w.is_valid_topup(-5.0) is False
    assert w.is_valid_topup(2500.0) is False


def test_prices_are_inr():
    assert w.CURRENCY == "INR"
    assert w.CURRENCY_SYMBOL == "₹"
    assert w.PRICES["full_analysis"] == 20.0


def test_checkout_signature_accepts_the_real_one():
    order_id, payment_id = "order_ABC123", "pay_XYZ789"
    good = hmac.new(
        rzp.KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()
    assert rzp.verify_checkout_signature(order_id, payment_id, good) is True


def test_checkout_signature_rejects_tampering():
    order_id, payment_id = "order_ABC123", "pay_XYZ789"
    good = hmac.new(
        rzp.KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()
    assert rzp.verify_checkout_signature(order_id, payment_id, good[:-1] + "0") is False
    assert rzp.verify_checkout_signature("order_OTHER", payment_id, good) is False
    assert rzp.verify_checkout_signature(order_id, "pay_OTHER", good) is False
    assert rzp.verify_checkout_signature(order_id, payment_id, "") is False


def test_webhook_signature_requires_a_secret():
    raw = b'{"event":"payment.captured"}'
    if not rzp.WEBHOOK_SECRET:
        # No secret configured: every webhook must be rejected, never trusted.
        assert rzp.verify_webhook_signature(raw, "anything") is False
    else:
        good = hmac.new(rzp.WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        assert rzp.verify_webhook_signature(raw, good) is True
        assert rzp.verify_webhook_signature(raw, good[:-1] + "0") is False
        assert rzp.verify_webhook_signature(b'{"event":"other"}', good) is False


def test_checkout_html_never_leaks_the_secret():
    html = rzp.checkout_html(
        order_id="order_ABC", amount_paise=500,
        callback_url="https://example.com/api/pay/callback", brand="TradingAgents",
    )
    assert rzp.KEY_SECRET not in html
    assert "order_ABC" in html
    assert "500" in html
    assert 'currency: "INR"' in html


def test_checkout_html_escapes_the_brand():
    html = rzp.checkout_html(
        order_id="order_ABC", amount_paise=500,
        callback_url="https://example.com/cb", brand='Evil"</script>',
    )
    assert '"</script>' not in html.split("checkout.js")[1].split("window.onload")[0]
