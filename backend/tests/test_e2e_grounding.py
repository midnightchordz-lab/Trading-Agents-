"""E2E verification: analyze endpoint returns grounding field + regression on existing endpoints."""
import os
import time
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://trade-agent-app.preview.emergentagent.com").rstrip("/")


def test_quote_aapl_ok():
    r = requests.get(f"{BASE_URL}/api/quote/AAPL", timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "price" in body


def test_chart_aapl_ok():
    r = requests.get(f"{BASE_URL}/api/chart/AAPL", params={"range": "1M"}, timeout=30)
    assert r.status_code == 200, r.text


def test_ohlc_reliance_ok():
    r = requests.get(f"{BASE_URL}/api/ohlc/RELIANCE.NS", params={"range": "1M"}, timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, (list, dict))


def test_news_aapl_ok():
    r = requests.get(f"{BASE_URL}/api/news/AAPL", timeout=30)
    assert r.status_code == 200, r.text


def test_existing_analysis_has_grounding():
    """Check pre-existing analysis referenced in the review request."""
    aid = "6dbf6223-256e-486f-a23e-855d4c3e1930"
    r = requests.get(f"{BASE_URL}/api/analysis/{aid}", timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "completed"
    assert "verdict" in body and body["verdict"] is not None
    v = body["verdict"]
    for k in ("decision", "confidence", "target_price", "stop_loss", "time_horizon", "summary", "key_risks"):
        assert k in v, f"verdict missing key {k}"
    assert "grounding" in body and body["grounding"] is not None, "grounding field missing"
    g = body["grounding"]
    assert g["status"] in ("grounded", "warning", "failed", "unverified")
    assert isinstance(g.get("checks"), list) and len(g["checks"]) > 0
    for c in g["checks"]:
        assert "id" in c and "ok" in c and "severity" in c and "message" in c
    assert "evidence" in g


def test_analyze_end_to_end_grounding():
    """Fire a fresh analyze and poll until completed; grounding must be present."""
    r = requests.post(f"{BASE_URL}/api/analyze", json={"symbol": "AAPL", "name": "Apple Inc."}, timeout=30)
    assert r.status_code == 200, r.text
    aid = r.json().get("id") or r.json().get("analysis_id")
    assert aid, r.text

    deadline = time.time() + 120
    body = None
    while time.time() < deadline:
        gr = requests.get(f"{BASE_URL}/api/analysis/{aid}", timeout=30)
        assert gr.status_code == 200
        body = gr.json()
        if body.get("status") == "completed":
            break
        time.sleep(3)
    assert body and body.get("status") == "completed", f"did not complete in time: {body and body.get('status')}"
    assert "grounding" in body and body["grounding"] is not None
    g = body["grounding"]
    assert g["status"] in ("grounded", "warning", "failed", "unverified")
    assert isinstance(g.get("checks"), list) and len(g["checks"]) > 0
