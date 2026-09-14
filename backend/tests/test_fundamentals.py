"""Unit tests for fundamentals.py (pure functions, no network)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fundamentals as fu


def _raw(v):
    return {"raw": v, "fmt": str(v)}


def _realistic_result():
    """Shaped like a real Yahoo quoteSummary result[0] object (wrapped raw/fmt)."""
    return {
        "defaultKeyStatistics": {
            "forwardPE": _raw(28.4),
            "pegRatio": _raw(1.8),
            "priceToBook": _raw(45.2),
        },
        "financialData": {
            "profitMargins": _raw(0.243),
            "operatingMargins": _raw(0.311),
            "grossMargins": _raw(0.702),
            "returnOnEquity": _raw(1.479),
            "returnOnAssets": _raw(0.223),
            "debtToEquity": _raw(140.968),
            "currentRatio": _raw(0.988),
            "revenueGrowth": _raw(0.061),
            "earningsGrowth": _raw(0.112),
            "targetMeanPrice": _raw(245.5),
            "recommendationKey": "buy",
        },
        "summaryDetail": {
            "trailingPE": _raw(34.1),
            "marketCap": _raw(3_450_000_000_000),
            "dividendYield": _raw(0.0044),
        },
        "price": {"marketCap": _raw(3_450_000_000_000)},
    }


def test_parses_a_realistic_result():
    f = fu.parse_fundamentals(_realistic_result())
    assert f["trailing_pe"] == 34.1
    assert f["forward_pe"] == 28.4
    assert f["market_cap"] == 3_450_000_000_000
    assert f["analyst_recommendation"] == "buy"


def test_none_input_returns_none():
    assert fu.parse_fundamentals(None) is None
    assert fu.parse_fundamentals({}) is None


def test_bare_numbers_instead_of_raw_wrapper():
    result = {"summaryDetail": {"trailingPE": 22.5}, "financialData": {}, "defaultKeyStatistics": {}}
    f = fu.parse_fundamentals(result)
    assert f["trailing_pe"] == 22.5


def test_missing_modules_dont_crash():
    result = {"financialData": {"profitMargins": _raw(0.1)}}
    f = fu.parse_fundamentals(result)
    assert f == {"profit_margin": 0.1}


def test_invalid_recommendation_key_omitted():
    result = {"financialData": {"recommendationKey": "none"}}
    assert fu.parse_fundamentals(result) is None
    result2 = {"financialData": {"recommendationKey": "garbage_value", "profitMargins": _raw(0.1)}}
    f = fu.parse_fundamentals(result2)
    assert "analyst_recommendation" not in f


def test_malformed_shapes_never_raise():
    assert fu.parse_fundamentals({"financialData": "not a dict"}) is None
    assert fu.parse_fundamentals({"financialData": {"profitMargins": "garbage"}}) is None
    assert fu.parse_fundamentals("not even a dict") is None


def test_summarize_none_on_empty():
    assert fu.summarize(None) is None
    assert fu.summarize({}) is None


def test_summarize_produces_readable_output():
    f = fu.parse_fundamentals(_realistic_result())
    s = fu.summarize(f)
    assert "Trailing P/E: 34.1" in s
    assert "forward P/E: 28.4" in s
    assert "Market cap: 3.45T" in s
    assert "revenue +6.1% YoY" in s
    assert "gross 70.2%" in s
    assert "BUY" in s
    assert "245.50" in s or "245.5" in s


def test_fmt_large_scales_correctly():
    assert fu._fmt_large(500) == "500"
    assert fu._fmt_large(2_500_000) == "2.50M"
    assert fu._fmt_large(7_200_000_000) == "7.20B"
    assert fu._fmt_large(1_100_000_000_000) == "1.10T"


def test_partial_data_still_summarizes():
    f = fu.parse_fundamentals({"summaryDetail": {"dividendYield": _raw(0.025)}})
    s = fu.summarize(f)
    assert s == "Dividend yield: 2.50%."
