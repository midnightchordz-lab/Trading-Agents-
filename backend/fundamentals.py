"""Fundamental data for agent context — additive, no new agent roles or steps.

The Fundamental Analyst's prompt already asks it to judge "valuation, growth,
margins, balance sheet strength and competitive moat" — but until now it was
only ever given price and a 52-week range, and reasoned about fundamentals
entirely from GPT's training-data memory of the company. That's a
"computed vs. fabricated" gap: a confident, out-of-date P/E is worse than a
vague one, because nothing downstream checks it.

This module fetches real numbers from Yahoo Finance's quoteSummary endpoint
(the same undocumented API family already used for /quote, /chart, /ohlc)
and turns them into a plain-language block appended to the shared context.

Coverage varies by symbol — smallcaps, newly-listed names, and some Indian
listings have thin or missing fundamentals data on Yahoo. This module fails
open: any missing field is simply omitted from the summary, never guessed
or estimated.
"""
from __future__ import annotations

from typing import Optional

# Modules requested from Yahoo's quoteSummary endpoint.
FUNDAMENTALS_MODULES = "defaultKeyStatistics,financialData,summaryDetail,price"

_VALID_RECOMMENDATIONS = {"strong_buy", "buy", "hold", "sell", "strong_sell", "underperform", "overperform", "none"}


def _unwrap(value):
    """Yahoo wraps most numeric fields as {"raw": ..., "fmt": "..."}; some
    API responses return a bare number instead. Handle both, and never
    raise on unexpected shapes."""
    if isinstance(value, dict):
        return value.get("raw")
    if isinstance(value, (int, float)):
        return value
    return None


def parse_fundamentals(quote_summary_result: Optional[dict]) -> Optional[dict]:
    """quote_summary_result: the `result[0]` object from Yahoo's
    quoteSummary response (the caller performs the HTTP fetch). Returns a
    flat dict of only the fields that were actually present and numeric,
    or None if nothing usable came back at all."""
    if not quote_summary_result or not isinstance(quote_summary_result, dict):
        return None

    dks = quote_summary_result.get("defaultKeyStatistics")
    fd = quote_summary_result.get("financialData")
    sd = quote_summary_result.get("summaryDetail")
    price_mod = quote_summary_result.get("price")
    dks = dks if isinstance(dks, dict) else {}
    fd = fd if isinstance(fd, dict) else {}
    sd = sd if isinstance(sd, dict) else {}
    price_mod = price_mod if isinstance(price_mod, dict) else {}

    out: dict = {}

    def add(key: str, *candidates):
        for c in candidates:
            v = _unwrap(c)
            if v is not None:
                out[key] = v
                return

    add("trailing_pe", sd.get("trailingPE"), dks.get("trailingPE"))
    add("forward_pe", dks.get("forwardPE"))
    add("peg_ratio", dks.get("pegRatio"))
    add("price_to_book", dks.get("priceToBook"))
    add("market_cap", sd.get("marketCap"), price_mod.get("marketCap"))
    add("dividend_yield", sd.get("dividendYield"))
    add("profit_margin", fd.get("profitMargins"), dks.get("profitMargins"))
    add("operating_margin", fd.get("operatingMargins"))
    add("gross_margin", fd.get("grossMargins"))
    add("return_on_equity", fd.get("returnOnEquity"))
    add("return_on_assets", fd.get("returnOnAssets"))
    add("debt_to_equity", fd.get("debtToEquity"))
    add("current_ratio", fd.get("currentRatio"))
    add("revenue_growth", fd.get("revenueGrowth"))
    add("earnings_growth", fd.get("earningsGrowth"))
    add("target_mean_price", fd.get("targetMeanPrice"))

    rec = fd.get("recommendationKey")
    if isinstance(rec, str) and rec.lower() in _VALID_RECOMMENDATIONS and rec.lower() != "none":
        out["analyst_recommendation"] = rec.lower()

    return out if out else None


def _fmt_large(n: float) -> str:
    n = float(n)
    if n >= 1e12:
        return f"{n / 1e12:.2f}T"
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:.2f}M"
    return f"{n:,.0f}"


def summarize(f: Optional[dict]) -> Optional[str]:
    """Plain-language block for the agent context. Returns None if there's
    nothing usable to say — the caller omits the section entirely rather
    than showing an empty header."""
    if not f:
        return None
    lines = []

    if "trailing_pe" in f:
        line = f"Trailing P/E: {f['trailing_pe']:.1f}"
        if "forward_pe" in f:
            line += f", forward P/E: {f['forward_pe']:.1f}"
        lines.append(line + ".")
    elif "forward_pe" in f:
        lines.append(f"Forward P/E: {f['forward_pe']:.1f}.")

    if "peg_ratio" in f:
        lines.append(f"PEG ratio: {f['peg_ratio']:.2f}.")
    if "price_to_book" in f:
        lines.append(f"Price/Book: {f['price_to_book']:.2f}.")
    if "market_cap" in f:
        lines.append(f"Market cap: {_fmt_large(f['market_cap'])}.")

    growth_parts = []
    if "revenue_growth" in f:
        growth_parts.append(f"revenue {f['revenue_growth'] * 100:+.1f}% YoY")
    if "earnings_growth" in f:
        growth_parts.append(f"earnings {f['earnings_growth'] * 100:+.1f}% YoY")
    if growth_parts:
        lines.append("Growth: " + ", ".join(growth_parts) + ".")

    margin_parts = []
    if "gross_margin" in f:
        margin_parts.append(f"gross {f['gross_margin'] * 100:.1f}%")
    if "operating_margin" in f:
        margin_parts.append(f"operating {f['operating_margin'] * 100:.1f}%")
    if "profit_margin" in f:
        margin_parts.append(f"net {f['profit_margin'] * 100:.1f}%")
    if margin_parts:
        lines.append("Margins: " + ", ".join(margin_parts) + ".")

    if "return_on_equity" in f:
        lines.append(f"Return on equity: {f['return_on_equity'] * 100:.1f}%.")
    if "debt_to_equity" in f:
        lines.append(f"Debt/Equity: {f['debt_to_equity']:.1f}.")
    if "current_ratio" in f:
        lines.append(f"Current ratio: {f['current_ratio']:.2f}.")
    if "dividend_yield" in f:
        lines.append(f"Dividend yield: {f['dividend_yield'] * 100:.2f}%.")
    if "target_mean_price" in f and "analyst_recommendation" in f:
        lines.append(
            f"Sell-side consensus: {f['analyst_recommendation'].replace('_', ' ').upper()}, "
            f"mean target {f['target_mean_price']:.2f}."
        )

    return "\n".join(lines) if lines else None
