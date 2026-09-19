"""The deployed build said "payments aren't switched on yet" when they were.

The wallet card swallowed a failed `/wallet/balance` call (`catch {}` with the
comment "fail quietly"), and a null wallet renders identically to a wallet from
a server with no payment config: no balance, no packs, no price. So a network
or auth failure on a deployed build was reported to the user — and to me — as a
deliberate configuration state, which cost a long time to diagnose because
every endpoint that could have answered "are payments configured here" needed a
session token that can't be minted for someone else's environment.

`/api/pay/health` answers it for any environment with no session and no
secrets.
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import razorpay_pay as rzp  # noqa: E402
import wallet as wal  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")
BASE = "http://localhost:8001/api"


def health():
    res = requests.get(f"{BASE}/pay/health", timeout=20)
    assert res.status_code == 200, res.text
    return res.json()


def test_no_session_required():
    """The whole point: it has to work from outside, against any deployment."""
    res = requests.get(f"{BASE}/pay/health", timeout=20)
    assert res.status_code == 200
    assert "razorpay" in res.json()


def test_reports_whether_this_environment_can_take_money():
    body = health()
    assert body["razorpay"] is rzp.payments_configured()
    assert body["razorpay_webhook_secret_set"] is bool(rzp.WEBHOOK_SECRET)
    assert isinstance(body["apple_iap"], bool)
    assert body["currencies"] == list(wal.SUPPORTED_CURRENCIES)
    assert isinstance(body["wallet_enforcement"], bool)


def test_reports_live_vs_test_mode_without_revealing_the_key():
    body = health()
    assert body["razorpay_mode"] in ("live", "test", None)
    if rzp.KEY_ID:
        assert body["razorpay_mode"] == ("live" if rzp.KEY_ID.startswith("rzp_live_") else "test")


def test_leaks_no_credential():
    """It exists to be curl-able by anyone, so it must carry nothing secret."""
    raw = requests.get(f"{BASE}/pay/health", timeout=20).text
    from dotenv import dotenv_values
    for name, value in dotenv_values(Path(__file__).parent.parent / ".env").items():
        if value and len(value) >= 12 and name not in ("MONGO_URL", "DB_NAME", "PUBLIC_BASE_URL"):
            assert value not in raw, f"{name} exposed by /pay/health"
    # Not even a key id, which is public but still an account identifier.
    assert "rzp_live_" not in raw and "rzp_test_" not in raw
