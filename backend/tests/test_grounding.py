"""Unit tests for the verdict grounding gate (pure function, no server needed)."""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test")

spec = importlib.util.spec_from_file_location("server", os.path.join(HERE, "server.py"))
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
ground_verdict = server.ground_verdict

Q = {"price": 15.0, "fiftyTwoWeekLow": 6.6, "fiftyTwoWeekHigh": 19.2, "currency": "INR", "dayLow": 14.8, "dayHigh": 15.3}


def test_buy_consistent_is_grounded():
    r = ground_verdict({"decision": "BUY", "target_price": 18, "stop_loss": 13.5}, Q)
    assert r["status"] == "grounded"
    assert r["evidence"]["price"] == 15.0


def test_buy_target_below_price_fails():
    r = ground_verdict({"decision": "BUY", "target_price": 12, "stop_loss": 13.5}, Q)
    assert r["status"] == "failed"
    assert any(c["id"] == "target_direction" and not c["ok"] for c in r["checks"])


def test_sell_stop_below_price_fails():
    r = ground_verdict({"decision": "SELL", "target_price": 13, "stop_loss": 14}, Q)
    assert r["status"] == "failed"


def test_implausible_magnitude_warns():
    r = ground_verdict({"decision": "BUY", "target_price": 60, "stop_loss": 14}, Q)
    assert r["status"] == "warning"


def test_poor_risk_reward_warns():
    r = ground_verdict({"decision": "BUY", "target_price": 15.5, "stop_loss": 12}, Q)
    assert r["status"] == "warning"
    assert any(c["id"] == "risk_reward" and not c["ok"] for c in r["checks"])


def test_hold_without_levels_is_only_warning():
    r = ground_verdict({"decision": "HOLD", "target_price": None, "stop_loss": None}, Q)
    assert r["status"] == "warning"


def test_buy_without_levels_fails():
    r = ground_verdict({"decision": "BUY", "target_price": None, "stop_loss": 13}, Q)
    assert r["status"] == "failed"


def test_no_quote_is_unverified():
    r = ground_verdict({"decision": "BUY", "target_price": 18, "stop_loss": 13}, None)
    assert r["status"] == "unverified"
    assert r["evidence"] is None


def test_verdict_is_never_mutated():
    v = {"decision": "BUY", "target_price": 12, "stop_loss": 13.5, "confidence": 70}
    before = dict(v)
    ground_verdict(v, Q)
    assert v == before
