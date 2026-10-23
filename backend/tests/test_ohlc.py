"""Additive tests for the OHLC endpoint used by the in-app fallback chart (NSE/BSE)."""
import os
import requests

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


def test_ohlc_shape():
    r = requests.get(f"{API}/ohlc/RELIANCE.NS", params={"range": "1M"}, timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert d["range"] == "1M"
    assert isinstance(d["bars"], list) and len(d["bars"]) > 5
    b = d["bars"][-1]
    for k in ("time", "open", "high", "low", "close", "volume"):
        assert k in b
    assert b["low"] <= b["close"] <= b["high"]


def test_ohlc_unknown_range_falls_back_to_1m():
    r = requests.get(f"{API}/ohlc/AAPL", params={"range": "9Z"}, timeout=30)
    assert r.status_code == 200
    assert r.json()["range"] == "1M"


def test_ohlc_malformed_symbol():
    # Symbols are now validated against the same ticker pattern /analyze uses
    # BEFORE the value is interpolated into a Yahoo URL, so a string that
    # isn't a ticker shape at all (underscores, 23 chars) is rejected as a bad
    # request rather than looked up and reported missing.
    r = requests.get(f"{API}/ohlc/THIS_DOES_NOT_EXIST_XYZ", timeout=30)
    assert r.status_code == 400


def test_ohlc_unknown_but_wellformed_symbol():
    # The original intent of the test above: a plausible ticker that simply
    # doesn't exist must still come back 404, not 400.
    r = requests.get(f"{API}/ohlc/ZZZZQQ", timeout=30)
    assert r.status_code == 404
