"""The desk states an outlook (Bullish / Neutral / Bearish), never a buy/sell
instruction. Internally the verdict keeps the BUY/SELL/HOLD enum, because
stored analyses, the re-check cache, price alerts and builds already on
users' phones all read it; `outlook` is what gets shown."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pipeline as P  # noqa: E402


@pytest.mark.parametrize("raw,internal,outlook", [
    ('{"decision": "BULLISH", "confidence": 70}', "BUY", "BULLISH"),
    ('{"decision": "bearish", "confidence": 70}', "SELL", "BEARISH"),
    ('{"decision": "NEUTRAL", "confidence": 70}', "HOLD", "NEUTRAL"),
    # A model that still answers in the old vocabulary keeps working.
    ('{"decision": "BUY", "confidence": 70}', "BUY", "BULLISH"),
    ('{"decision": "SELL", "confidence": 70}', "SELL", "BEARISH"),
    # No JSON: fall back to the words in the text.
    ("The desk's outlook is BEARISH on weak guidance.", "SELL", "BEARISH"),
    ("Overall bullish.", "BUY", "BULLISH"),
    ("Nothing decisive here.", "HOLD", "NEUTRAL"),
])
def test_verdict_maps_outlook_to_the_internal_enum(raw, internal, outlook):
    v = P.parse_verdict(raw)
    assert v["decision"] == internal
    assert v["outlook"] == outlook


def test_timeframes_carry_an_outlook_too():
    raw = ('{"short_term": {"decision": "BULLISH", "confidence": 70, "target_price": 105, "stop_loss": 95, "thesis": "a"},'
           '"medium_term": {"decision": "NEUTRAL", "confidence": 55, "target_price": null, "stop_loss": null, "thesis": "b"},'
           '"long_term": {"decision": "BEARISH", "confidence": 60, "target_price": 80, "stop_loss": 110, "thesis": "c"}}')
    tf = P.parse_timeframes(raw)
    assert [tf[k]["decision"] for k in ("short_term", "medium_term", "long_term")] == ["BUY", "HOLD", "SELL"]
    assert [tf[k]["outlook"] for k in ("short_term", "medium_term", "long_term")] == ["BULLISH", "NEUTRAL", "BEARISH"]
    fb = P.fallback_timeframes({"decision": "SELL", "confidence": 60})
    assert all(h["outlook"] == "BEARISH" for h in fb.values())


def test_every_agent_that_concludes_is_told_not_to_advise():
    for prompt in (P.BULL_SYS, P.BEAR_SYS, P.RM_SYS, P.TRADER_SYS, P.RISK_SYS, P.PM_SYS,
                   P.DEBATE_SYS, P.TIMEFRAME_SYS):
        assert P.OUTLOOK_NOT_ADVICE in prompt


def test_prompts_no_longer_ask_for_buy_sell_calls():
    for prompt in (P.PM_SYS, P.TIMEFRAME_SYS):
        assert "BULLISH|NEUTRAL|BEARISH" in prompt
        assert "BUY|SELL|HOLD" not in prompt
    for prompt in (P.BULL_SYS, P.BEAR_SYS, P.RM_SYS, P.TRADER_SYS):
        assert "case to BUY" not in prompt and "case to SELL" not in prompt
        assert "recommended stance" not in prompt and "action (buy/sell/hold)" not in prompt
