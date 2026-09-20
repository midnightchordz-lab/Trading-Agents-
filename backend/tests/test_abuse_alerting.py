"""Abuse alerting: the owner hears about a budget breach instead of a log file.

The three whole-deployment budgets (SMS, email codes, free credits) fire during
an attack — which is to say at 3am, while money is being spent. They already
log an ERROR; now they also send one email.

What matters in these tests is not that an email is pretty, but that alerting
can never make things worse: it must not slow or fail a request, must not flood
the owner's inbox (an attack trips a budget on EVERY request), and must not
leak an address, phone or IP.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alerts  # noqa: E402
import mailer  # noqa: E402
import rate_limit as rl  # noqa: E402
from routes import auth_routes as ar  # noqa: E402
from tests.async_loop import run_async  # noqa: E402


@pytest.fixture
def captured(monkeypatch):
    """Intercept the send, so no real email leaves during tests."""
    sent = []

    async def fake_send(*, to, subject, html):
        sent.append({"to": to, "subject": subject, "html": html})
        return "fake-id"

    monkeypatch.setattr(mailer, "send_email", fake_send)
    monkeypatch.setattr(mailer, "email_configured", lambda: True)
    monkeypatch.setattr(alerts, "alert_recipient", lambda: "owner@example.com")
    return sent


def fire(kind, captured, lines=None):
    run_async(alerts.send_alert(kind, "Budget reached", lines or ["<b>999</b> in the last hour."]))
    return captured


def test_an_alert_is_sent_when_a_budget_is_reached(captured):
    kind = f"test-{uuid.uuid4().hex[:8]}"
    fire(kind, captured)
    assert len(captured) == 1
    assert captured[0]["to"] == "owner@example.com"
    assert "TradingAgents" in captured[0]["subject"]


def test_only_one_email_per_hour_per_kind(captured):
    """An attack trips the budget on every request. Ten thousand identical
    emails is its own denial of service — of the owner's inbox, and of the
    sending domain's reputation."""
    kind = f"test-{uuid.uuid4().hex[:8]}"
    for _ in range(25):
        fire(kind, captured)
    assert len(captured) == 1


def test_different_budgets_alert_independently(captured):
    """Hitting the SMS budget must not silence the free-credit one."""
    a, b = f"test-a-{uuid.uuid4().hex[:6]}", f"test-b-{uuid.uuid4().hex[:6]}"
    fire(a, captured)
    fire(b, captured)
    assert len(captured) == 2


def test_a_failing_email_provider_never_raises(monkeypatch):
    """Alerting sits inside a path that is already refusing something. It must
    not also turn that into a 500."""
    async def boom(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(mailer, "send_email", boom)
    monkeypatch.setattr(mailer, "email_configured", lambda: True)
    monkeypatch.setattr(alerts, "alert_recipient", lambda: "owner@example.com")
    run_async(alerts.send_alert(f"test-{uuid.uuid4().hex[:8]}", "t", ["x"]))  # must not raise


def test_nothing_is_sent_when_no_recipient_is_configured(monkeypatch, captured):
    monkeypatch.setattr(alerts, "alert_recipient", lambda: None)
    fire(f"test-{uuid.uuid4().hex[:8]}", captured)
    assert captured == []


def test_the_email_carries_no_personal_data(captured):
    """Same rule the logs follow: counts, caps and the alert kind only."""
    kind = f"test-{uuid.uuid4().hex[:8]}"
    fire(kind, captured, lines=["<b>412</b> text messages were requested in the last hour (cap 60)."])
    body = f"{captured[0]['subject']} {captured[0]['html']}"
    for pii in ("@gmail.com", "+9198", "203.0.113", "device:"):
        assert pii not in body, f"the alert leaked {pii}"


def test_the_alert_passes_the_email_safety_guard(captured):
    """Every send path is screened for forms, credential asks and misleading
    links. An alert is no exception."""
    fire(f"test-{uuid.uuid4().hex[:8]}", captured)
    mailer._assert_safe_email(captured[0]["subject"], captured[0]["html"])


def test_raise_alert_does_not_block_the_caller():
    """It schedules and returns; a request never waits on an email provider."""
    import inspect
    source = inspect.getsource(alerts.raise_alert)
    assert "create_task" in source
    # And outside a running loop it is a no-op rather than an error.
    alerts.raise_alert(f"test-{uuid.uuid4().hex[:8]}", "t", ["x"])


def test_all_three_budgets_are_wired_to_an_alert():
    """The point of the feature: none of the three may stay silent."""
    import inspect
    signup = inspect.getsource(ar.signup_free_credits)
    otp = inspect.getsource(ar.auth_otp_request)
    assert 'alerts.raise_alert("free_credit_budget"' in signup
    assert 'alerts.raise_alert("otp_sms_budget"' in otp
    assert 'alerts.raise_alert("otp_email_budget"' in otp
    # The ERROR logs stay — the email is in addition to them, not instead.
    assert "GLOBAL FREE-CREDIT BUDGET REACHED" in signup
    assert "GLOBAL SMS OTP BUDGET REACHED" in otp
    assert "GLOBAL EMAIL OTP BUDGET REACHED" in otp


def test_the_hourly_cap_uses_the_shared_counter():
    """So the cap survives a restart and is shared across instances, like
    every other limit in the app."""
    import inspect
    assert "rl.hit(" in inspect.getsource(alerts.send_alert)
    assert rl.env_int("ALERT_MAX_PER_HOUR", 1) >= 1
