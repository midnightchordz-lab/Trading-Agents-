"""E2E test for PHASE 8 Multi-Horizon Desk timeframes.
Runs the real /api/analyze pipeline (~30-60s) against the public preview URL
and asserts the new additive 'timeframes' shape and existing verdict shape.
"""
import os
import time
import requests
import pytest

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://trade-agent-app.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def analysis():
    r = requests.post(f"{BASE_URL}/api/analyze", json={"symbol": "AAPL", "name": "Apple Inc."}, timeout=30)
    assert r.status_code == 200, f"POST /api/analyze failed: {r.status_code} {r.text}"
    aid = r.json().get("id")
    assert aid, "No id in analyze response"

    deadline = time.time() + 180
    doc = None
    while time.time() < deadline:
        g = requests.get(f"{BASE_URL}/api/analysis/{aid}", timeout=30)
        assert g.status_code == 200, f"GET analysis failed: {g.status_code}"
        doc = g.json()
        if doc.get("status") == "completed":
            break
        time.sleep(3)
    assert doc and doc.get("status") == "completed", f"Analysis didn't complete: status={doc.get('status') if doc else 'None'}"
    return doc


def test_total_steps_is_13(analysis):
    assert analysis.get("total_steps") == 13, f"total_steps={analysis.get('total_steps')} (expected 13)"


def test_messages_length_and_last_agent(analysis):
    messages = analysis.get("messages") or []
    assert len(messages) == 13, f"messages count={len(messages)} (expected 13)"
    last = messages[-1]
    assert last.get("agent") == "Multi-Horizon Desk", f"Last agent = {last.get('agent')}"


def test_timeframes_top_level_shape(analysis):
    tf = analysis.get("timeframes")
    assert isinstance(tf, dict), "Missing timeframes object"
    for key in ("short_term", "medium_term", "long_term"):
        assert key in tf, f"Missing horizon: {key}"
        h = tf[key]
        assert isinstance(h, dict), f"{key} not a dict"
        assert h.get("decision") in ("BUY", "SELL", "HOLD"), f"{key}.decision invalid: {h.get('decision')}"
        conf = h.get("confidence")
        assert isinstance(conf, int) and 0 <= conf <= 100, f"{key}.confidence invalid: {conf}"
        # target/stop can be number or null
        tp = h.get("target_price")
        sl = h.get("stop_loss")
        assert tp is None or isinstance(tp, (int, float)), f"{key}.target_price wrong type"
        assert sl is None or isinstance(sl, (int, float)), f"{key}.stop_loss wrong type"
        # thesis string or null
        th = h.get("thesis")
        assert th is None or isinstance(th, str), f"{key}.thesis wrong type"
        # horizon + label present
        assert "horizon" in h, f"{key} missing horizon"
        assert isinstance(h.get("label"), str) and h.get("label"), f"{key} missing label"


def test_verdict_shape_unchanged(analysis):
    v = analysis.get("verdict")
    assert isinstance(v, dict), "verdict missing"
    for k in ("decision", "confidence", "target_price", "stop_loss", "summary", "key_risks", "time_horizon"):
        assert k in v, f"verdict missing key: {k}"
    assert v["decision"] in ("BUY", "SELL", "HOLD")


def test_debate_shape_unchanged(analysis):
    d = analysis.get("debate")
    assert isinstance(d, dict), "debate missing"


def test_non_regression_quote():
    r = requests.get(f"{BASE_URL}/api/quote/AAPL", timeout=30)
    assert r.status_code == 200, f"/api/quote/AAPL -> {r.status_code}"


def test_non_regression_chart():
    r = requests.get(f"{BASE_URL}/api/chart/AAPL?range=1M", timeout=30)
    assert r.status_code == 200, f"/api/chart/AAPL -> {r.status_code}"


def test_non_regression_ohlc_ns():
    r = requests.get(f"{BASE_URL}/api/ohlc/RELIANCE.NS?range=1M", timeout=30)
    assert r.status_code == 200, f"/api/ohlc/RELIANCE.NS -> {r.status_code}"


def test_non_regression_news():
    r = requests.get(f"{BASE_URL}/api/news/AAPL", timeout=30)
    assert r.status_code == 200, f"/api/news/AAPL -> {r.status_code}"
