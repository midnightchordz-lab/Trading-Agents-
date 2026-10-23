"""The agents' shared context carries the computed technicals, and the agents
that use them are told never to invent indicator values."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pipeline as P  # noqa: E402

QUOTE = {"name": "Reliance Industries", "price": 2450.0, "currency": "INR", "changePercent": 0.8,
         "fiftyTwoWeekLow": 2200, "fiftyTwoWeekHigh": 3200, "sparkline": [2400, 2450], "exchange": "NSE"}


def test_context_includes_the_computed_block():
    ctx = P.build_context("RELIANCE.NS", QUOTE, "P/E: 24", "RSI(14): 61.2 (neutral)")
    assert "COMPUTED TECHNICALS (calculated from the last year of daily prices" in ctx
    assert "RSI(14): 61.2 (neutral)" in ctx


def test_context_says_unavailable_instead_of_leaving_room_to_guess():
    ctx = P.build_context("RELIANCE.NS", QUOTE, None, None)
    assert "COMPUTED TECHNICALS: unavailable for this asset — do not estimate indicator values." in ctx


def test_old_three_argument_calls_still_work():
    assert "TICKER: AAPL" in P.build_context("AAPL", QUOTE, None)


def test_number_hungry_agents_are_told_not_to_invent():
    for prompt in (P.TECH_SYS, P.TRADER_SYS, P.RISK_SYS):
        assert P.NO_INVENTED_NUMBERS in prompt
        assert prompt.endswith(P.STYLE)
    assert "ATR" in P.TRADER_SYS and "VaR" in P.RISK_SYS
