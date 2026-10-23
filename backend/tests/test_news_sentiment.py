"""Tests for headline sentiment tagging on the news feed."""
import os
import sys
import requests

sys.path.insert(0, "/app/backend")
from market_data import classify_headline_keyword  # noqa: E402

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    from pathlib import Path
    p = Path("/app/frontend/.env")
    if p.exists():
        for line in p.read_text().splitlines():
            if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
                break

API = f"{BASE_URL}/api"
VALID = {"BULLISH", "BEARISH", "NEUTRAL"}


def test_keyword_bullish():
    assert classify_headline_keyword("Apple beats estimates as iPhone sales surge") == "BULLISH"


def test_keyword_bearish():
    assert classify_headline_keyword("Tesla plunges after analyst downgrade and lawsuit") == "BEARISH"


def test_keyword_neutral():
    assert classify_headline_keyword("Apple to hold annual shareholder meeting in March") == "NEUTRAL"


def test_news_endpoint_tags_every_headline():
    r = requests.get(f"{API}/news/AAPL", timeout=90)
    assert r.status_code == 200
    items = r.json()["results"]
    assert isinstance(items, list)
    for n in items:
        assert n.get("sentiment") in VALID, n
