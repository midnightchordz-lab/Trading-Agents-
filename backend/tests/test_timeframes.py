"""Unit tests for parse_timeframes / fallback_timeframes (pure functions)."""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test")

import types
_stub_llm = types.ModuleType("emergentintegrations.llm.chat")
class _LlmChat:
    def __init__(self, *a, **k): pass
class _UserMessage:
    def __init__(self, *a, **k): pass
_stub_llm.LlmChat = _LlmChat
_stub_llm.UserMessage = _UserMessage
sys.modules.setdefault("emergentintegrations", types.ModuleType("emergentintegrations"))
sys.modules.setdefault("emergentintegrations.llm", types.ModuleType("emergentintegrations.llm"))
sys.modules["emergentintegrations.llm.chat"] = _stub_llm

from pipeline import fallback_timeframes, parse_timeframes  # noqa: E402

GOOD = '''{"short_term": {"decision": "BUY", "confidence": 70, "target_price": 105, "stop_loss": 95, "thesis": "Momentum favors a bounce this week."},
"medium_term": {"decision": "HOLD", "confidence": 55, "target_price": null, "stop_loss": null, "thesis": "Range-bound until earnings."},
"long_term": {"decision": "SELL", "confidence": 60, "target_price": 80, "stop_loss": 110, "thesis": "Structural headwinds over the year."}}'''


def test_parses_all_three_horizons():
    tf = parse_timeframes(GOOD)
    assert set(tf.keys()) == {"short_term", "medium_term", "long_term"}
    assert tf["short_term"]["decision"] == "BUY"
    assert tf["short_term"]["target_price"] == 105.0
    assert tf["medium_term"]["decision"] == "HOLD"
    assert tf["medium_term"]["target_price"] is None
    assert tf["long_term"]["decision"] == "SELL"
    assert tf["long_term"]["label"] == "Long-term (6-12 months)"


def test_clamps_confidence_and_invalid_decision():
    raw = '''{"short_term": {"decision": "MAYBE", "confidence": 500, "target_price": 1, "stop_loss": 1, "thesis": "x"},
    "medium_term": {"decision": "BUY", "confidence": -5, "target_price": null, "stop_loss": null, "thesis": "x"},
    "long_term": {"decision": "HOLD", "confidence": 50, "target_price": null, "stop_loss": null, "thesis": "x"}}'''
    tf = parse_timeframes(raw)
    assert tf["short_term"]["decision"] == "HOLD"  # invalid -> HOLD
    assert tf["short_term"]["confidence"] == 100  # clamped
    assert tf["medium_term"]["confidence"] == 0  # clamped


def test_missing_horizon_returns_none():
    raw = '{"short_term": {"decision": "BUY", "confidence": 70, "target_price": 1, "stop_loss": 1, "thesis": "x"}}'
    assert parse_timeframes(raw) is None


def test_garbage_returns_none():
    assert parse_timeframes("not json at all") is None
    assert parse_timeframes("") is None
    assert parse_timeframes("{}") is None


def test_fallback_never_invents_prices():
    verdict = {"decision": "BUY", "confidence": 77, "target_price": 42, "stop_loss": 38}
    tf = fallback_timeframes(verdict)
    for key in ("short_term", "medium_term", "long_term"):
        assert tf[key]["decision"] == "BUY"
        assert tf[key]["confidence"] == 77
        assert tf[key]["target_price"] is None
        assert tf[key]["stop_loss"] is None
        assert "unavailable" in tf[key]["thesis"].lower()


def test_fallback_handles_missing_verdict_fields():
    tf = fallback_timeframes({})
    assert tf["short_term"]["decision"] == "HOLD"
    assert tf["short_term"]["confidence"] == 50
