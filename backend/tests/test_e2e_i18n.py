"""E2E: live LLM analyze runs — English is no-op, Hindi returns Devanagari
free-text but keeps JSON keys and BUY/SELL/HOLD enums in English."""
import os
import re
import time
import requests

from auth_helper import AUTH_HEADERS

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "https://trade-agent-app.preview.emergentagent.com").rstrip("/")

DEVANAGARI = re.compile(r"[\u0900-\u097F]")

REQUIRED_TOP_KEYS = {"verdict", "debate", "timeframes", "language"}
REQUIRED_VERDICT_KEYS = {"decision", "confidence", "target_price", "stop_loss", "summary", "key_risks", "time_horizon"}


def _start_and_poll(lang=None, timeout=180):
    payload = {"symbol": "AAPL", "name": "Apple Inc."}
    if lang is not None:
        payload["language"] = lang
    r = requests.post(f"{BASE_URL}/api/analyze", json=payload, headers=AUTH_HEADERS, timeout=30)
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    deadline = time.time() + timeout
    doc = None
    while time.time() < deadline:
        g = requests.get(f"{BASE_URL}/api/analysis/{aid}", timeout=15)
        assert g.status_code == 200
        doc = g.json()
        if doc.get("status") == "completed":
            return doc
        if doc.get("status") == "error":
            raise AssertionError(f"pipeline errored: {doc.get('error')}")
        time.sleep(3)
    raise AssertionError(f"timed out waiting for {aid}; last status={doc and doc.get('status')}")


def test_english_no_lang_field_is_default_and_completes():
    """English (no language field sent) — verdict shape + enum in English."""
    doc = _start_and_poll(lang=None)
    assert doc["language"] == "en"
    v = doc["verdict"]
    assert REQUIRED_VERDICT_KEYS.issubset(set(v.keys()))
    assert v["decision"] in ("BUY", "SELL", "HOLD")
    for k, tf in doc["timeframes"].items():
        assert tf["decision"] in ("BUY", "SELL", "HOLD")


def test_hindi_keeps_keys_and_enums_english_but_translates_free_text():
    doc = _start_and_poll(lang="hi")
    assert doc["language"] == "hi"

    # 1) TOP-LEVEL JSON KEYS UNCHANGED (English)
    top_keys = set(doc.keys())
    assert REQUIRED_TOP_KEYS.issubset(top_keys), f"missing top-level english keys: {REQUIRED_TOP_KEYS - top_keys}"

    v = doc["verdict"]
    # 2) VERDICT KEYS UNCHANGED (English) — never translated
    assert REQUIRED_VERDICT_KEYS.issubset(set(v.keys())), f"verdict keys not English: {set(v.keys())}"

    # 3) VERDICT ENUM IN ENGLISH
    assert v["decision"] in ("BUY", "SELL", "HOLD"), f"verdict.decision translated? got {v['decision']!r}"

    # 4) EACH TIMEFRAME'S decision IS ENGLISH ENUM
    tfs = doc["timeframes"]
    for horizon in ("short_term", "medium_term", "long_term"):
        assert horizon in tfs, f"timeframes.{horizon} missing"
        tf = tfs[horizon]
        assert tf["decision"] in ("BUY", "SELL", "HOLD"), f"{horizon}.decision translated? got {tf['decision']!r}"

    # 5) FREE-TEXT VALUES CONTAIN DEVANAGARI (Hindi script)
    # Combine summary + a debate argument + a timeframe thesis. If ANY of them
    # contain Devanagari characters the language directive was honored.
    haystack_parts = [
        v.get("summary") or "",
        (doc.get("debate") or {}).get("bull") or "",
        (doc.get("debate") or {}).get("bear") or "",
        (doc.get("debate") or {}).get("fundamentals") or "",
        tfs["short_term"].get("thesis") or "",
        tfs["medium_term"].get("thesis") or "",
        tfs["long_term"].get("thesis") or "",
    ]
    joined = "\n".join(haystack_parts)
    assert DEVANAGARI.search(joined), f"no Devanagari found in free-text values; sample: {joined[:400]!r}"
