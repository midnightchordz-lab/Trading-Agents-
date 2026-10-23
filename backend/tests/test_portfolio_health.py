"""Portfolio Health Check: classification, the statistics, the frontier, drift,
liquidity, the score, the wording, and the endpoint — offline, on synthetic
price history."""
import asyncio
import os
import re
import sys
from datetime import date, timedelta

import numpy as np
import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import portfolio_health as ph  # noqa: E402
from routes import health as health_routes  # noqa: E402

DAYS = [(date(2025, 10, 1) + timedelta(days=i)).isoformat() for i in range(400)]
WEEKDAYS = [d for d in DAYS if date.fromisoformat(d).weekday() < 5][:250]


def walk(seed, drift=0.0004, vol=0.012, start=100.0, market=None, beta=0.0, days=WEEKDAYS):
    rng = np.random.default_rng(seed)
    price, out = start, {}
    for i, d in enumerate(days):
        r = drift + vol * rng.standard_normal() + (beta * market[i] if market is not None else 0.0)
        price *= 1 + r
        out[d] = round(price, 4)
    return out


MARKET = np.random.default_rng(0).normal(0.0004, 0.01, len(WEEKDAYS))


def hist(closes, currency="INR", quote_type="EQUITY", name=None, volume=1_000_000):
    return {"symbol": "X", "currency": currency, "quote_type": quote_type, "name": name,
            "price": list(closes.values())[-1], "closes": closes, "volumes": {d: volume for d in closes}}


# --- classification ---------------------------------------------------------------

def test_asset_classes():
    assert ph.asset_bucket("GOLDBEES.NS", "ETF", "INR", "Nippon India ETF Gold BeES") == "gold"
    assert ph.asset_bucket("GS", "EQUITY", "USD", "The Goldman Sachs Group") == "us_equity"
    assert ph.asset_bucket("GFI", "EQUITY", "USD", "Gold Fields Limited") == "us_equity"
    assert ph.asset_bucket("SGBAUG28.NS", "EQUITY", "INR", None) == "gold"
    assert ph.asset_bucket("LIQUIDBEES.NS", "ETF", "INR", None) == "debt"
    assert ph.asset_bucket("TLT", "ETF", "USD", "iShares 20+ Year Treasury Bond ETF") == "debt"
    assert ph.asset_bucket("BTC-USD", "CRYPTOCURRENCY", "USD", "Bitcoin USD") == "crypto"
    assert ph.asset_bucket("RELIANCE.NS", "EQUITY", "INR", "Reliance") == "in_equity"
    assert ph.asset_bucket("VOD.L", "EQUITY", "GBP", "Vodafone") == "other_equity"


def test_base_currency_prefers_rupees():
    assert ph.base_currency(["INR", "USD"]) == "INR"
    assert ph.base_currency(["USD"]) == "USD"
    assert ph.base_currency(["GBP"]) == "USD"


def test_targets_are_validated():
    assert ph.parse_targets({"in_equity": 60, "gold": 10, "cash": 30}) == {"in_equity": 0.6, "gold": 0.1, "cash": 0.3}
    assert ph.parse_targets(None) is None and ph.parse_targets({"cash": 0}) is None
    for bad, msg in (({"stocks": 100}, "Unknown"), ({"cash": 120}, "between"), ({"cash": 50}, "add up to 100")):
        with pytest.raises(ValueError, match=msg):
            ph.parse_targets(bad)


# --- statistics ---------------------------------------------------------------------

def test_inner_join_skips_days_one_market_was_shut():
    a = {"2026-01-01": 100, "2026-01-02": 101, "2026-01-05": 102}
    b = {"2026-01-02": 50, "2026-01-05": 51}
    r = ph.daily_returns({"A": a, "B": b})
    assert list(r.index) == ["2026-01-05"]
    assert r.loc["2026-01-05", "A"] == pytest.approx(102 / 101 - 1)


def test_fx_conversion_carries_the_last_rate():
    out = ph.to_base({"2026-01-02": 10.0, "2026-01-05": 11.0}, {"2026-01-01": 80.0, "2026-01-02": 82.0})
    assert out == {"2026-01-02": 820.0, "2026-01-05": 902.0}


def test_concentration_and_effective_holdings():
    c = ph.concentration({"A": 50, "B": 25, "C": 25})
    assert c["effective_holdings"] == pytest.approx(2.7, abs=0.05)
    assert c["top"][0] == {"symbol": "A", "weight": 0.5}


def test_clusters_find_stocks_that_move_as_one():
    base = walk(1)
    twin = {d: v * 1.01 + (i % 3) * 0.01 for i, (d, v) in enumerate(base.items())}
    other = walk(2)
    r = ph.daily_returns({"HDFCBANK": base, "ICICIBANK": twin, "INFY": other})
    clusters = ph.correlation_clusters(r.corr(), {"HDFCBANK": 0.4, "ICICIBANK": 0.3, "INFY": 0.3})
    assert len(clusters) == 1 and set(clusters[0]["symbols"]) == {"HDFCBANK", "ICICIBANK"}
    assert clusters[0]["weight"] == pytest.approx(0.7)


def test_beta_and_r_squared_against_the_market():
    import pandas as pd
    bench = pd.Series(MARKET, index=WEEKDAYS)
    tracker = bench * 1.2 + np.random.default_rng(5).normal(0, 0.002, len(WEEKDAYS))
    m = ph.market_exposure(tracker, bench)
    assert m["beta"] == pytest.approx(1.2, abs=0.05) and m["r_squared"] > 0.9
    noise = pd.Series(np.random.default_rng(6).normal(0, 0.01, len(WEEKDAYS)), index=WEEKDAYS)
    assert ph.market_exposure(noise, bench)["r_squared"] < 0.1
    assert ph.market_exposure(noise[:10], bench) is None


def test_sharpe_and_drawdown():
    import pandas as pd
    r = pd.Series([0.001] * 252)
    rr = ph.risk_return(r, 0.065)
    assert rr["annual_return"] == pytest.approx(1.001 ** 252 - 1, abs=1e-4)
    assert rr["volatility"] < 1e-9 and rr["sharpe"] is None
    crash = pd.Series([0.0, -0.5, 0.2])
    assert ph.risk_return(crash, 0.0)["max_drawdown"] == pytest.approx(-0.5)


def test_frontier_reports_points_never_weights():
    series = {"A": walk(1, drift=0.001), "B": walk(2, drift=0.0002, vol=0.006), "C": walk(3, drift=-0.0003)}
    r = ph.daily_returns(series)
    f = ph.frontier(r, {"A": 0.1, "B": 0.1, "C": 0.8}, 0.065)
    assert f and len(f["curve"]) >= 5
    # The frontier is the best you could do with these holdings: a lopsided mix
    # heavy in the weak stock must sit below it.
    assert f["return_gap_at_same_risk"] > 0
    assert f["lowest_risk"]["volatility"] <= f["you"]["volatility"] + 1e-9
    def keys(o):
        if isinstance(o, dict):
            return set(o) | set().union(*(keys(v) for v in o.values()))
        if isinstance(o, list):
            return set().union(*(keys(v) for v in o)) if o else set()
        return set()
    assert "weights" not in keys(f)


def test_drift_flags_only_meaningful_gaps():
    rows = {r["bucket"]: r for r in ph.drift({"in_equity": 0.8, "gold": 0.06, "cash": 0.14},
                                             {"in_equity": 0.6, "gold": 0.1, "cash": 0.3})}
    assert rows["in_equity"]["flag"] and rows["in_equity"]["drift"] == pytest.approx(0.2)
    assert not rows["gold"]["flag"] and rows["cash"]["flag"]
    assert ph.drift({"in_equity": 1.0}, None) == []


def test_liquidity():
    closes = {d: 100.0 for d in WEEKDAYS[-30:]}
    thin = ph.liquidity("TINY.NS", 5_00_000, closes, {d: 1_000 for d in closes}, 1.0, "INR", "in_equity")
    assert thin["thinly_traded"] and thin["position_share_of_day"] == pytest.approx(5.0) and thin["flag"]
    deep = ph.liquidity("RELIANCE.NS", 5_00_000, closes, {d: 10_000_000 for d in closes}, 1.0, "INR", "in_equity")
    assert not deep["flag"]
    assert ph.liquidity("BTC-USD", 1, closes, {d: 1 for d in closes}, 1.0, "INR", "crypto") is None


def test_score_rewards_breadth_and_independence():
    narrow = ph.diversification_score(1.5, 0.85, 0.9, 1)
    broad = ph.diversification_score(14, 0.2, 0.2, 3)
    assert narrow["score"] < 30 and narrow["label"] == "Concentrated"
    assert broad["score"] == 100 and broad["label"] == "Well spread"


# --- the whole report -----------------------------------------------------------------

def sample_report(targets=None, cash=0.0):
    banks = walk(11, market=MARKET, beta=1.0, vol=0.004)
    histories = {
        "HDFCBANK.NS": hist(banks),
        "ICICIBANK.NS": hist({d: v * 1.3 for d, v in banks.items()}),
        "TCS.NS": hist(walk(12, market=MARKET, beta=0.6)),
        "GOLDBEES.NS": hist(walk(13, vol=0.006), quote_type="ETF", name="Nippon India ETF Gold BeES"),
        "AAPL": hist(walk(14, start=200), currency="USD"),
        "TINY.NS": hist(walk(15, start=10), volume=100),
        "DEAD.NS": None,
    }
    holdings = [{"symbol": s, "quantity": q} for s, q in
                (("HDFCBANK.NS", 100), ("ICICIBANK.NS", 80), ("TCS.NS", 20), ("GOLDBEES.NS", 300),
                 ("AAPL", 10), ("TINY.NS", 1000), ("DEAD.NS", 5))]
    sectors = {"HDFCBANK.NS": "Financial Services", "ICICIBANK.NS": "Financial Services", "TCS.NS": "Technology",
               "AAPL": "Technology", "TINY.NS": None}
    fx = {"USD": {d: 85.0 for d in WEEKDAYS}}
    bench = {"closes": {d: 20000 * float(np.prod(1 + MARKET[: i + 1])) for i, d in enumerate(WEEKDAYS)}}
    return ph.build_report(holdings, cash, targets, histories, sectors, fx, bench, "INR")


def test_report_end_to_end():
    rep = sample_report(targets={"in_equity": 0.6, "us_equity": 0.1, "gold": 0.2, "cash": 0.1}, cash=10_000)
    assert rep["base_currency"] == "INR" and rep["skipped"] == [{"symbol": "DEAD.NS", "reason": "No price history"}]
    aapl = next(h for h in rep["holdings"] if h["symbol"] == "AAPL")
    assert aapl["value"] == pytest.approx(10 * list(walk(14, start=200).values())[-1] * 85, rel=1e-6)
    assert {b["bucket"] for b in rep["asset_mix"]} == {"in_equity", "us_equity", "gold", "cash"}
    assert any(set(c["symbols"]) >= {"HDFCBANK.NS", "ICICIBANK.NS"} for c in rep["clusters"])
    assert rep["market"]["beta"] > 0 and rep["benchmark"]["name"] == "Nifty 50"
    assert rep["risk_return"]["sharpe"] is not None and rep["benchmark_risk_return"] is not None
    assert rep["frontier"]["curve"] and rep["drift"]
    assert any(l["symbol"] == "TINY.NS" and l["flag"] for l in rep["liquidity"])
    assert 0 <= rep["diversification"]["score"] <= 100
    fin = next(s for s in rep["sectors"] if s["sector"] == "Financial Services")
    banks = sum(h["weight"] for h in rep["holdings"] if h["symbol"] in ("HDFCBANK.NS", "ICICIBANK.NS"))
    assert fin["weight"] == pytest.approx(banks, abs=1e-3)
    assert next(s for s in rep["sectors"] if s["sector"] == "Gold")


ADVICE = re.compile(r"\b(buy|sell|add|trim|exit|switch|rebalance|should|recommend|reduce|increase)\b", re.I)


def test_observations_describe_and_never_advise():
    rep = sample_report(targets={"in_equity": 0.5, "gold": 0.5})
    assert len(rep["observations"]) >= 4
    for line in rep["observations"]:
        assert not ADVICE.search(line.replace("exiting all at once", "")), line


def test_too_little_history_says_so():
    short = WEEKDAYS[:30]
    histories = {"A.NS": hist(walk(1, days=short)), "B.NS": hist(walk(2, days=short))}
    rep = ph.build_report([{"symbol": "A.NS", "quantity": 1}, {"symbol": "B.NS", "quantity": 1}], 0, None,
                          histories, {}, {}, None, "INR")
    assert rep["frontier"] is None and rep["market"] is None and rep["risk_return"] is None
    assert "enough shared price history" in rep["observations"][0]


def test_pence_are_converted_to_pounds():
    closes = walk(3, start=7000)
    rep = ph.build_report([{"symbol": "VOD.L", "quantity": 100}], 0, None,
                          {"VOD.L": hist(closes, currency="GBp")}, {}, {"GBP": {d: 1.25 for d in WEEKDAYS}}, None, "USD")
    assert rep["holdings"][0]["value"] == pytest.approx(100 * list(closes.values())[-1] * 0.01 * 1.25, rel=1e-6)


def test_missing_fx_skips_the_holding_with_a_reason():
    rep = ph.build_report([{"symbol": "AAPL", "quantity": 1}, {"symbol": "TCS.NS", "quantity": 1}], 0, None,
                          {"AAPL": hist(walk(1), currency="USD"), "TCS.NS": hist(walk(2))}, {}, {"USD": None}, None, "INR")
    assert rep["skipped"] == [{"symbol": "AAPL", "reason": "No USD→INR exchange rate"}]


# --- the endpoint ---------------------------------------------------------------------

@pytest.fixture
def fake_yahoo(monkeypatch):
    calls = []
    data = {"TCS.NS": hist(walk(12, market=MARKET, beta=0.6)), "AAPL": hist(walk(14, start=200), currency="USD"),
            "USDINR=X": hist({d: 85.0 for d in WEEKDAYS}),
            "^NSEI": hist({d: 20000 * float(np.prod(1 + MARKET[: i + 1])) for i, d in enumerate(WEEKDAYS)})}

    def history(sym):
        calls.append(sym)
        if sym not in data:
            raise RuntimeError("404")
        return data[sym]
    monkeypatch.setattr(health_routes, "fetch_dated_history_sync", history)
    monkeypatch.setattr(health_routes, "fetch_sector_sync", lambda s: {"TCS.NS": "Technology", "AAPL": "Technology"}.get(s))
    return calls


USER = {"id": "u1"}


def test_endpoint_builds_a_report_with_fx_and_benchmark(fake_yahoo):
    body = health_routes.HealthRequest(holdings=[{"symbol": "tcs.ns", "quantity": 10}, {"symbol": "AAPL", "quantity": 2}],
                                       cash=5000, targets={"in_equity": 70, "us_equity": 20, "cash": 10})
    rep = asyncio.run(health_routes.portfolio_health(body, USER))
    assert rep["base_currency"] == "INR" and "USDINR=X" in fake_yahoo and "^NSEI" in fake_yahoo
    assert {h["symbol"] for h in rep["holdings"]} == {"TCS.NS", "AAPL"} and rep["drift"]


def test_endpoint_rejects_bad_input_before_fetching(fake_yahoo):
    for holdings, targets in (([{"symbol": "A", "quantity": 1}, {"symbol": "a", "quantity": 2}], None),
                              ([{"symbol": "BAD SYMBOL", "quantity": 1}], None),
                              ([{"symbol": "TCS.NS", "quantity": 1}], {"cash": 40})):
        with pytest.raises(HTTPException) as e:
            asyncio.run(health_routes.portfolio_health(
                health_routes.HealthRequest(holdings=holdings, targets=targets), USER))
        assert e.value.status_code == 400
    assert fake_yahoo == []


def test_endpoint_502_when_nothing_could_be_fetched(fake_yahoo):
    with pytest.raises(HTTPException) as e:
        asyncio.run(health_routes.portfolio_health(
            health_routes.HealthRequest(holdings=[{"symbol": "NOPE.NS", "quantity": 1}]), USER))
    assert e.value.status_code == 502
