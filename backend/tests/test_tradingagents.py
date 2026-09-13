"""Backend regression tests for the TradingAgents API."""
import os
import time
import pytest
import requests

from auth_helper import AUTH_HEADERS

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # fallback: read from frontend .env
    from pathlib import Path
    p = Path("/app/frontend/.env")
    if p.exists():
        for line in p.read_text().splitlines():
            if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
                break

API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update(AUTH_HEADERS)
    return s


# --- health ---
class TestHealth:
    def test_root(self, client):
        r = client.get(f"{API}/")
        assert r.status_code == 200
        j = r.json()
        assert j.get("status") == "ok"


# --- quote ---
class TestQuote:
    def test_quote_aapl(self, client):
        r = client.get(f"{API}/quote/AAPL")
        assert r.status_code == 200
        j = r.json()
        assert j["symbol"] == "AAPL"
        assert j["price"] is not None
        assert isinstance(j.get("sparkline"), list) and len(j["sparkline"]) > 0
        assert "changePercent" in j

    def test_quote_reliance_ns(self, client):
        r = client.get(f"{API}/quote/RELIANCE.NS")
        assert r.status_code == 200
        j = r.json()
        assert j["symbol"].startswith("RELIANCE")
        assert j["price"] is not None

    def test_quote_btc(self, client):
        r = client.get(f"{API}/quote/BTC-USD")
        assert r.status_code == 200
        j = r.json()
        assert j["price"] is not None
        assert isinstance(j.get("sparkline"), list)


# --- search ---
class TestSearch:
    def test_search_apple(self, client):
        r = client.get(f"{API}/search", params={"q": "apple"})
        assert r.status_code == 200
        j = r.json()
        assert isinstance(j.get("results"), list) and len(j["results"]) > 0
        item = j["results"][0]
        assert "symbol" in item and "name" in item


# --- trending ---
class TestTrending:
    def test_trending(self, client):
        r = client.get(f"{API}/trending")
        assert r.status_code == 200
        j = r.json()
        assert isinstance(j.get("results"), list)
        assert len(j["results"]) >= 6  # allow a couple of yahoo failures
        for q in j["results"]:
            assert "symbol" in q and "price" in q


# --- analyze pipeline ---
class TestAnalyze:
    created_id = None

    def test_analyze_invalid_symbol(self, client):
        r = client.post(f"{API}/analyze", json={"symbol": "!!"})
        assert r.status_code == 400

    def test_analyze_creates_running(self, client):
        r = client.post(f"{API}/analyze", json={"symbol": "NVDA"})
        assert r.status_code == 200
        j = r.json()
        # With usage-based pricing on, an unchanged re-check is served from
        # cache (completed, free) instead of starting a fresh run.
        assert j["status"] in ("running", "completed")
        assert j["total_steps"] == 13
        assert j["symbol"] == "NVDA"
        assert j.get("id")
        TestAnalyze.created_id = j["id"]

    def test_analysis_polling_completes(self, client):
        aid = TestAnalyze.created_id
        assert aid, "no analysis id from previous test"
        deadline = time.time() + 120
        status = None
        j = None
        while time.time() < deadline:
            r = client.get(f"{API}/analysis/{aid}")
            assert r.status_code == 200
            j = r.json()
            status = j["status"]
            if status in ("completed", "error"):
                break
            time.sleep(2)
        assert status == "completed", f"analysis did not complete in time (last status={status})"
        msgs = j.get("messages") or []
        assert len(msgs) == 13, f"expected 13 messages, got {len(msgs)}"
        phases = {m["phase"] for m in msgs}
        # at least these phases must appear
        for p in ("analysis", "debate", "trade", "risk", "decision"):
            assert p in phases, f"phase {p} missing from messages"
        for m in msgs:
            assert set(["agent", "tag", "phase", "content"]).issubset(m.keys())
        v = j.get("verdict") or {}
        assert v.get("decision") in ("BUY", "SELL", "HOLD")
        assert 0 <= v.get("confidence", -1) <= 100
        assert isinstance(v.get("key_risks"), list)


# --- history + delete (runs after analyze) ---
class TestHistoryDelete:
    def test_history_excludes_messages(self, client):
        r = client.get(f"{API}/history")
        assert r.status_code == 200
        j = r.json()
        assert isinstance(j.get("results"), list)
        if j["results"]:
            first = j["results"][0]
            assert "messages" not in first
            assert "symbol" in first and "status" in first

    def test_delete_unknown_returns_404(self, client):
        r = client.delete(f"{API}/analysis/does-not-exist-xyz")
        assert r.status_code == 404

    def test_delete_created_analysis(self, client):
        aid = TestAnalyze.created_id
        if not aid:
            pytest.skip("no analysis id to delete")
        r = client.delete(f"{API}/analysis/{aid}")
        assert r.status_code == 200
        # confirm gone
        r2 = client.get(f"{API}/analysis/{aid}")
        assert r2.status_code == 404
