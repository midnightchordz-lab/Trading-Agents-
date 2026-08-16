"""Focused tests for this iteration:
- /api/chart range parameter (1D/1W/1M/1Y + fallback for unknown)
- /api/markets/{category} valid + unknown -> 404
- POST /api/analyze -> poll -> verify `debate` object AND existing `verdict`+12 messages
"""
import os
import time
import pytest
import requests
from pathlib import Path

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
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
    s.headers.update({"Content-Type": "application/json"})
    return s


# --- /api/chart ---
class TestChart:
    @pytest.mark.parametrize("rng", ["1D", "1W", "1M", "1Y"])
    def test_chart_ranges(self, client, rng):
        r = client.get(f"{API}/chart/AAPL", params={"range": rng})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["range"] == rng
        assert isinstance(j.get("points"), list) and len(j["points"]) >= 1
        assert j.get("last") is not None
        assert "change" in j
        assert "changePercent" in j

    def test_chart_unknown_range_falls_back_to_1M(self, client):
        r = client.get(f"{API}/chart/AAPL", params={"range": "zzz"})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["range"] == "1M"
        assert isinstance(j.get("points"), list) and len(j["points"]) >= 1


# --- /api/markets/{category} ---
class TestMarkets:
    @pytest.mark.parametrize("cat", ["trending", "stocks", "crypto", "commodities"])
    def test_markets_categories(self, client, cat):
        r = client.get(f"{API}/markets/{cat}")
        assert r.status_code == 200
        j = r.json()
        assert isinstance(j.get("results"), list)
        assert len(j["results"]) >= 4

    def test_markets_unknown_category_404(self, client):
        r = client.get(f"{API}/markets/nonsense")
        assert r.status_code == 404


# --- Analysis with debate ---
class TestAnalysisDebate:
    created_id = None

    def test_kickoff_analyze(self, client):
        r = client.post(f"{API}/analyze", json={"symbol": "AAPL"})
        assert r.status_code == 200
        j = r.json()
        assert j["status"] == "running"
        assert j.get("id")
        # verify new field exists in schema
        assert "debate" in j and j["debate"] is None
        TestAnalysisDebate.created_id = j["id"]

    def test_debate_populated_on_completion(self, client):
        aid = TestAnalysisDebate.created_id
        assert aid
        deadline = time.time() + 180
        j = None
        while time.time() < deadline:
            r = client.get(f"{API}/analysis/{aid}")
            assert r.status_code == 200
            j = r.json()
            if j["status"] in ("completed", "error"):
                break
            time.sleep(3)
        assert j and j["status"] == "completed", f"did not complete: {j and j.get('status')}"

        # existing checks still hold
        msgs = j.get("messages") or []
        assert len(msgs) == 12
        v = j.get("verdict") or {}
        assert v.get("decision") in ("BUY", "SELL", "HOLD")
        assert 0 <= v.get("confidence", -1) <= 100
        assert isinstance(v.get("key_risks"), list)

        # NEW: debate object populated
        d = j.get("debate")
        assert isinstance(d, dict), f"debate missing/not dict: {d}"
        for k in ("bull", "bear", "fundamentals", "recommendation"):
            assert d.get(k) and isinstance(d[k], str) and len(d[k]) > 5, f"debate.{k} weak: {d.get(k)!r}"
        assert isinstance(d.get("agreements"), list) and len(d["agreements"]) >= 1
        assert isinstance(d.get("disagreements"), list) and len(d["disagreements"]) >= 1

    def test_cleanup(self, client):
        aid = TestAnalysisDebate.created_id
        if not aid:
            pytest.skip("no id")
        r = client.delete(f"{API}/analysis/{aid}")
        assert r.status_code == 200
