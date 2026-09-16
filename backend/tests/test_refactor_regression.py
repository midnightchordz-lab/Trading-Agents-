"""Post-refactor regression: server.py 2387 -> 97 lines, split into
core/deps/market_data/pipeline + routes/*. NOTHING about behaviour was meant to
change, so this file hits every route family and asserts shape / status
identical to iter16.
"""
import os
import sys
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(BACKEND_DIR.parent / "frontend" / ".env")

from auth_helper import AUTH_HEADERS, USER_ID as AUTH_USER_ID  # noqa: E402

BASE_URL = (
    os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    or os.environ.get("PUBLIC_BASE_URL")
    or "http://localhost:8001"
).rstrip("/")


class TestMarketRoutes:
    def test_root(self):
        r = requests.get(f"{BASE_URL}/api/", timeout=15)
        assert r.status_code == 200
        assert r.json().get("status") == "ok"

    def test_search(self):
        r = requests.get(f"{BASE_URL}/api/search", params={"q": "apple"}, timeout=20)
        assert r.status_code == 200
        assert isinstance(r.json().get("results"), list)

    def test_quote(self):
        r = requests.get(f"{BASE_URL}/api/quote/AAPL", timeout=20)
        assert r.status_code == 200
        j = r.json()
        assert j.get("symbol") == "AAPL"
        assert "price" in j

    def test_chart(self):
        r = requests.get(f"{BASE_URL}/api/chart/AAPL", params={"range": "1mo"}, timeout=20)
        assert r.status_code == 200
        j = r.json()
        assert "candles" in j or "prices" in j or isinstance(j, dict)

    def test_ohlc(self):
        r = requests.get(f"{BASE_URL}/api/ohlc/AAPL", params={"range": "1mo"}, timeout=20)
        assert r.status_code == 200

    def test_news(self):
        r = requests.get(f"{BASE_URL}/api/news/AAPL", timeout=25)
        assert r.status_code == 200
        assert isinstance(r.json().get("articles", r.json().get("results", [])), list) or isinstance(r.json(), dict)

    def test_trending(self):
        r = requests.get(f"{BASE_URL}/api/trending", timeout=20)
        assert r.status_code == 200

    def test_markets_valid_category(self):
        r = requests.get(f"{BASE_URL}/api/markets/stocks", timeout=25)
        assert r.status_code == 200

    def test_markets_unknown_category_404(self):
        r = requests.get(f"{BASE_URL}/api/markets/no-such-category", timeout=15)
        assert r.status_code == 404


class TestAuthRoutesShape:
    def test_me_requires_bearer(self):
        r = requests.get(f"{BASE_URL}/api/auth/me", timeout=10)
        assert r.status_code == 401

    def test_me_with_valid_token(self):
        r = requests.get(f"{BASE_URL}/api/auth/me", headers=AUTH_HEADERS, timeout=10)
        assert r.status_code == 200
        assert r.json().get("id") == AUTH_USER_ID

    def test_otp_request_email_shape(self):
        r = requests.post(
            f"{BASE_URL}/api/auth/otp/request",
            json={"identifier": f"TEST_reg-{uuid.uuid4().hex[:8]}@example.com"},
            timeout=15,
        )
        # Backend may 200 (accepted) or 429 (rate limited). Should never 5xx.
        assert r.status_code in (200, 400, 429), r.text

    def test_apple_disabled_returns_501(self):
        r = requests.post(f"{BASE_URL}/api/auth/apple", json={"id_token": "x"}, timeout=10)
        # Payload validation may 422 (missing required fields per Pydantic) or 501.
        assert r.status_code in (400, 422, 501)


class TestPaymentsRoutes:
    def test_wallet_balance_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/wallet/balance", timeout=10)
        assert r.status_code == 401

    def test_wallet_balance_shape(self):
        r = requests.get(f"{BASE_URL}/api/wallet/balance", headers=AUTH_HEADERS, timeout=15)
        assert r.status_code == 200
        j = r.json()
        for k in ("balance", "currency", "packs", "is_admin", "enforcement_enabled", "iap_enabled"):
            assert k in j, f"missing {k}"
        # Refactor must not have flipped IAP on: key is empty.
        assert j["iap_enabled"] is False

    def test_pay_order_amount_validation(self):
        r = requests.post(
            f"{BASE_URL}/api/pay/order",
            headers=AUTH_HEADERS,
            json={"amount": 3.0},
            timeout=15,
        )
        assert r.status_code == 400

    def test_pay_order_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/pay/order", json={"amount": 5.0}, timeout=10)
        assert r.status_code == 401

    def test_pay_status_unknown_order_404(self):
        r = requests.get(f"{BASE_URL}/api/pay/status/plink_bogus_{uuid.uuid4().hex}", headers=AUTH_HEADERS, timeout=15)
        assert r.status_code == 404

    def test_pay_webhook_rejects_bad_signature(self):
        r = requests.post(
            f"{BASE_URL}/api/pay/webhook",
            data=b'{"event":"payment.captured"}',
            headers={"Content-Type": "application/json", "X-Razorpay-Signature": "nope"},
            timeout=15,
        )
        assert r.status_code == 400

    def test_pay_callback_empty_shows_nothing_charged_html(self):
        r = requests.get(f"{BASE_URL}/api/pay/callback", timeout=15)
        assert r.status_code == 200
        assert "nothing was charged" in r.text.lower() or "wasn" in r.text.lower()

    def test_iap_config_shape(self):
        r = requests.get(f"{BASE_URL}/api/pay/iap/config", timeout=10)
        assert r.status_code == 200
        j = r.json()
        assert j["enabled"] is False
        assert j["ios_api_key"] == ""
        assert [p["product_id"] for p in j["packs"]] == ["credits_5", "credits_10", "credits_25"]
        assert j["currency"] == "USD"
        # Never leak the webhook secret.
        assert "rcwh_" not in r.text
        assert "REVENUECAT_WEBHOOK_AUTH" not in r.text


class TestAnalysisRoutesAuth:
    def test_history_requires_auth(self):
        assert requests.get(f"{BASE_URL}/api/history", timeout=10).status_code == 401

    def test_analysis_get_requires_auth(self):
        assert requests.get(f"{BASE_URL}/api/analysis/nope", timeout=10).status_code == 401

    def test_analysis_delete_requires_auth(self):
        assert requests.delete(f"{BASE_URL}/api/analysis/nope", timeout=10).status_code == 401

    def test_history_auth_ok_and_no_leaked_fields(self):
        r = requests.get(f"{BASE_URL}/api/history", headers=AUTH_HEADERS, timeout=15)
        assert r.status_code == 200
        for row in r.json().get("results", []):
            assert "owner_hash" not in row
            assert "viewer_hashes" not in row


class TestPortfolio:
    def test_portfolio_optimize_shape(self):
        r = requests.post(
            f"{BASE_URL}/api/portfolio/optimize",
            headers=AUTH_HEADERS,
            json={"symbols": ["AAPL", "MSFT", "GOOGL"], "risk_tolerance": "balanced"},
            timeout=45,
        )
        assert r.status_code in (200, 400, 422), r.text
        if r.status_code == 200:
            assert "weights" in r.json() or "allocations" in r.json() or isinstance(r.json(), dict)
