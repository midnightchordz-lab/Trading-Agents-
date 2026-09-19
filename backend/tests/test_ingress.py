"""The public path, on purpose.

The rest of the suite now calls the backend on the loopback (see conftest.py)
because it is the same process and 70x closer. The one thing that skips is the
ingress: `/api/*` reaching port 8001, headers surviving the proxy, TLS. A
deployed app only ever talks to the backend this way, so it gets its own file
rather than being assumed.
"""
import os

import requests

PUBLIC_BASE = (
    os.environ.get("TEST_PUBLIC_BASE_URL")
    or "https://trade-agent-app.preview.emergentagent.com"
).rstrip("/")


def test_api_prefix_reaches_the_backend_through_the_ingress():
    res = requests.get(f"{PUBLIC_BASE}/api/pay/health", timeout=30)
    assert res.status_code == 200, res.text
    assert "razorpay" in res.json()


def test_market_data_works_through_the_ingress():
    res = requests.get(f"{PUBLIC_BASE}/api/quote/AAPL", timeout=30)
    assert res.status_code == 200, res.text
    assert res.json()["symbol"] == "AAPL"


def test_forwarded_headers_survive_the_proxy():
    """The signup-abuse guard and the OTP per-IP cap both key off the client
    address, which only arrives as an X-Forwarded-For through the ingress."""
    res = requests.get(
        f"{PUBLIC_BASE}/api/pay/health",
        headers={"X-Forwarded-For": "203.0.113.7"},
        timeout=30,
    )
    assert res.status_code == 200
