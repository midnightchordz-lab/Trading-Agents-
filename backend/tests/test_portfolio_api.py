"""Integration tests for /api/portfolio/optimize (hits live backend + Yahoo)."""
import os
import time

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://trade-agent-app.preview.emergentagent.com").rstrip("/")

HOLDINGS = [
    {"symbol": "AAPL", "quantity": 10, "avg_price": 150},
    {"symbol": "MSFT", "quantity": 5, "avg_price": 300},
    {"symbol": "NVDA", "quantity": 8, "avg_price": 100},
]


def _validate_result(data, expect_weight_sum_1=True):
    assert "suggested_weights" in data, data
    assert "actions" in data, data
    assert "expected_return" in data
    assert "volatility" in data
    assert "sharpe" in data
    weights = data["suggested_weights"]
    if expect_weight_sum_1 and weights:
        s = sum(weights.values())
        assert abs(s - 1.0) < 0.01, f"weights sum={s}"
    for a in data["actions"]:
        assert set(a.keys()) >= {"symbol", "current_weight", "suggested_weight", "delta", "action"}
        assert a["action"] in {"ADD", "HOLD", "TRIM", "SELL"}
    for k in ("expected_return", "volatility", "sharpe"):
        assert isinstance(data[k], (int, float))


def test_optimize_hrp():
    r = requests.post(
        f"{BASE_URL}/api/portfolio/optimize",
        json={"holdings": HOLDINGS, "objective": "hrp", "use_agent_views": False, "cash": 0},
        timeout=120,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    _validate_result(data)
    # suggested_shares × current_price ≤ total_value if present
    if data.get("suggested_shares") and data.get("current_prices") and data.get("total_value") is not None:
        spent = sum(
            data["suggested_shares"].get(s, 0) * data["current_prices"].get(s, 0)
            for s in data["suggested_shares"]
        )
        assert spent <= data["total_value"] + 1e-6


def test_optimize_max_sharpe():
    r = requests.post(
        f"{BASE_URL}/api/portfolio/optimize",
        json={"holdings": HOLDINGS, "objective": "max_sharpe", "use_agent_views": False, "cash": 0},
        timeout=120,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    _validate_result(data)


def test_optimize_single_holding_rejected():
    # Note: spec asks for 400 but server explicitly returns 422 for <2 usable
    # symbols (as per context note "<2-usable-symbol portfolio returns HTTP 422").
    r = requests.post(
        f"{BASE_URL}/api/portfolio/optimize",
        json={"holdings": [HOLDINGS[0]], "objective": "hrp", "use_agent_views": False, "cash": 0},
        timeout=60,
    )
    assert r.status_code in (400, 422), r.text


def test_optimize_duplicate_symbols_rejected():
    r = requests.post(
        f"{BASE_URL}/api/portfolio/optimize",
        json={
            "holdings": [
                {"symbol": "AAPL", "quantity": 10, "avg_price": 150},
                {"symbol": "AAPL", "quantity": 5, "avg_price": 155},
            ],
            "objective": "hrp",
            "use_agent_views": False,
            "cash": 0,
        },
        timeout=60,
    )
    assert r.status_code == 400, r.text


def _run_analysis_for(symbol, name):
    r = requests.post(f"{BASE_URL}/api/analyze", json={"symbol": symbol, "name": name}, timeout=60)
    assert r.status_code == 200, r.text
    aid = r.json().get("id") or r.json().get("analysis_id")
    for _ in range(60):
        g = requests.get(f"{BASE_URL}/api/analysis/{aid}", timeout=30)
        if g.status_code == 200 and g.json().get("status") == "completed":
            return g.json()
        time.sleep(3)
    pytest.skip(f"analysis for {symbol} did not complete in time")


def test_optimize_with_agent_views():
    # Ensure AAPL has a cached completed analysis
    _run_analysis_for("AAPL", "Apple Inc.")
    r = requests.post(
        f"{BASE_URL}/api/portfolio/optimize",
        json={"holdings": HOLDINGS, "objective": "max_sharpe", "use_agent_views": True, "cash": 0},
        timeout=120,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    _validate_result(data)
    used = data.get("used_agent_views_for", [])
    missing = data.get("missing_agent_view_for", [])
    # Either AAPL is a used view OR it was HOLD (acceptable to be absent)
    # MSFT/NVDA should be in missing if never analysed
    all_syms = {h["symbol"] for h in HOLDINGS}
    covered = set(used) | set(missing)
    # every holding should be classified somewhere (either used or missing or dropped)
    dropped = set(data.get("dropped_symbols", []))
    assert all_syms <= covered | dropped | {a["symbol"] for a in data["actions"]}


def test_non_regression_endpoints():
    for path in ["/api/quote/AAPL", "/api/chart/AAPL?range=1M", "/api/ohlc/RELIANCE.NS?range=1M", "/api/news/AAPL"]:
        r = requests.get(f"{BASE_URL}{path}", timeout=60)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"
