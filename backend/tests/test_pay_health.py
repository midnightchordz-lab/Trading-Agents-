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


def test_reports_whether_the_keys_actually_authenticate():
    """The failure that caused real customers' "couldn't start checkout" was a
    DEPLOYED container holding a rotated-out key pair: `razorpay` was true
    (both env vars present) while every top-up 502'd. Configured and accepted
    are different questions, so health answers both."""
    body = health()
    assert body["razorpay_credentials_ok"] in (True, False, None)
    if rzp.payments_configured():
        # The startup probe runs on this backend, so by the time tests run it
        # must have reached a verdict.
        assert body["razorpay_credentials_ok"] is not None


def test_key_tail_identifies_the_environment_without_exposing_the_key():
    body = health()
    tail = body["razorpay_key_tail"]
    assert tail == (rzp.KEY_ID[-4:] if rzp.KEY_ID else "")
    if rzp.KEY_ID:
        # 4 chars can't be walked back to a key id, and the key id is public
        # anyway (the app receives it) — but the full value still never appears.
        assert len(tail) == 4
        assert rzp.KEY_ID not in requests.get(f"{BASE}/pay/health", timeout=20).text


def test_env_files_are_not_git_ignored():
    """Three separate production payment outages traced back to the ROOT
    .gitignore excluding `.env` / `.env.*` / `*.env`: the deploy build context
    is the repo, so the container shipped with a stale environment and kept the
    old (deactivated) Razorpay keys while the preview had the new ones. The
    pattern has regenerated twice, so it is asserted rather than remembered."""
    import subprocess
    root = Path(__file__).parent.parent.parent
    for rel in ("backend/.env", "frontend/.env"):
        done = subprocess.run(
            ["git", "check-ignore", "-v", rel], cwd=root, capture_output=True, text=True
        )
        assert done.returncode != 0, (
            f"{rel} is git-ignored by {done.stdout.strip()} — a deploy will ship "
            "without it and payments will fail with 'Authentication failed'."
        )
