"""Portfolio report and mutual funds, offline: AMFI parsing, fund search, the
report rows and notes, and the endpoint with every upstream call faked."""
import asyncio
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mutual_funds as mf  # noqa: E402
import portfolio_report as preport  # noqa: E402
from routes import portfolio as proutes  # noqa: E402

NAVALL = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Large Cap Fund)

ICICI Prudential Mutual Fund

120586;INF109K016L0;-;ICICI Prudential Bluechip Fund - Direct Plan - Growth;118.2500;23-Oct-2026
108466;INF109K01BL4;-;ICICI Prudential Bluechip Fund - Growth;104.1000;23-Oct-2026
100001;INF000000001;-;Old Closed Plan - Growth;N.A.;01-Jan-2020

Open Ended Schemes(Equity Scheme - Mid Cap Fund)

Kotak Mahindra Mutual Fund

120166;INF174K01LS2;-;Kotak Emerging Equity Fund - Direct Plan - Growth;125.0000;23-Oct-2026

Open Ended Schemes(Debt Scheme - Liquid Fund)

HDFC Mutual Fund

119091;INF179KB1HP9;-;HDFC Liquid Fund - Direct Plan - Growth;4,950.1234;23-Oct-2026
"""

ADVICE = re.compile(r"\b(buy|sell|hold|add|exit|remove|trim|switch|should|recommend|multibagger|accumulate)\b", re.I)


# The live file's layout: Plan and Option get their own columns before NAV.
NAVALL_LIVE = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Flexi Cap Fund)

PPFAS Mutual Fund

122639;INF879O01027;-;Parag Parikh Flexi Cap Fund;Direct Plan;Growth;88.9695;23-Oct-2026
122640;INF879O01019;-;Parag Parikh Flexi Cap Fund;Regular Plan;Growth;80.9965;23-Oct-2026
153964;INF879O01AA1;-;Parag Parikh Flexi Cap Fund;Direct Plan;Monthly IDCW Payout;88.9699;23-Oct-2026
"""


# --- AMFI file ------------------------------------------------------------------

def test_the_live_eight_column_layout_parses():
    funds = mf.parse_navall(NAVALL_LIVE)
    f = funds["122639"]
    assert f["nav"] == pytest.approx(88.9695) and f["date"] == "23-Oct-2026"
    assert f["name"] == "Parag Parikh Flexi Cap Fund - Direct Plan - Growth"
    assert f["short_name"] == "Parag Parikh Flexi Cap Fund" and f["plan"] == "Direct · Growth"
    assert funds["153964"]["plan"] == "Direct · Monthly IDCW Payout"
    assert f["category"] == "Equity Scheme - Flexi Cap Fund"


def test_search_works_on_the_live_layout(monkeypatch):
    monkeypatch.setattr(mf, "all_funds", lambda: mf.parse_navall(NAVALL_LIVE))
    res = mf.search("parag parikh flexi")
    assert [r["code"] for r in res][:2] == ["122639", "153964"]   # Direct first, Growth first
    assert res[0]["short_name"] == "Parag Parikh Flexi Cap Fund" and res[0]["type"] == "Flexi-cap fund"


def test_six_column_names_are_split_too():
    f = mf.parse_navall(NAVALL)["120586"]
    assert f["short_name"] == "ICICI Prudential Bluechip Fund" and f["plan"] == "Direct · Growth"


@pytest.mark.parametrize("raw,clean", [
    ("The Tata Power Company Limited", "Tata Power Company"),
    ("Tata Consultancy Services Limited", "Tata Consultancy Services"),
    ("Infosys Ltd.", "Infosys"),
    ("Apple Inc.", "Apple"),
    ("Reliance Industries", "Reliance Industries"),
    (None, None),
])
def test_company_names_lose_legal_suffixes(raw, clean):
    assert preport.company_name(raw) == clean


def test_the_nav_file_parses_with_categories_and_skips_unpriced_schemes():
    funds = mf.parse_navall(NAVALL)
    assert set(funds) == {"120586", "108466", "120166", "119091"}
    assert funds["120586"]["nav"] == 118.25 and funds["120586"]["date"] == "23-Oct-2026"
    assert funds["120586"]["category"] == "Equity Scheme - Large Cap Fund"
    assert funds["120166"]["amc"] == "Kotak Mahindra Mutual Fund"
    assert funds["119091"]["nav"] == pytest.approx(4950.1234)   # thousands separator


@pytest.mark.parametrize("category,label,kind", [
    ("Equity Scheme - Large Cap Fund", "Large-cap fund", "equity"),
    ("Equity Scheme - Large & Mid Cap Fund", "Large & mid-cap fund", "equity"),
    ("Equity Scheme - ELSS", "Tax-saving (ELSS) fund", "equity"),
    ("Debt Scheme - Liquid Fund", "Liquid fund", "debt"),
    ("Hybrid Scheme - Aggressive Hybrid Fund", "Aggressive hybrid fund", "hybrid"),
    ("Other Scheme - Index Funds", "Index fund", "equity"),
    ("Debt Scheme - Corporate Bond Fund", "Debt fund", "debt"),
])
def test_categories_get_plain_names(category, label, kind):
    assert mf.plain_type(category) == label
    assert mf.asset_kind(category) == kind


def test_fund_symbols():
    assert mf.symbol_for("120586") == "MF:120586"
    assert mf.code_from("MF:120586") == "120586" and mf.is_fund("mf:120586")
    for bad in ("RELIANCE.NS", "MF:", "MF:12a", "MF:123456789", ""):
        assert mf.code_from(bad) is None


def test_search_matches_every_word_and_puts_direct_growth_first(monkeypatch):
    monkeypatch.setattr(mf, "all_funds", lambda: mf.parse_navall(NAVALL))
    res = mf.search("icici bluechip")
    assert [r["code"] for r in res] == ["120586", "108466"]
    assert res[0]["symbol"] == "MF:120586" and res[0]["type"] == "Large-cap fund"
    assert mf.search("kotak liquid") == [] and mf.search("  ") == []


def test_one_year_change_from_newest_first_history():
    today = datetime(2026, 10, 23)
    hist = [{"date": (today - timedelta(days=d)).strftime("%d-%m-%Y"), "nav": str(100 + (400 - d) * 0.05)}
            for d in range(0, 400)]
    change = mf.one_year_change(hist)
    assert change == pytest.approx((100 + 400 * 0.05) / (100 + 35 * 0.05) - 1, rel=1e-3)
    young = hist[:200]
    assert mf.one_year_change(young) is None and mf.one_year_change([]) is None


def test_a_failed_refresh_keeps_the_last_good_list(monkeypatch):
    mf._nav.update(funds={"1": {"code": "1"}}, fetched=0.0)

    def boom():
        raise RuntimeError("AMFI down")
    monkeypatch.setattr(mf, "_download", boom)
    assert mf.all_funds() == {"1": {"code": "1"}}


# --- the report ------------------------------------------------------------------

def owner_example():
    """The owner's example portfolio, priced."""
    def stock(sym, name, typ, qty, avg, price, pm=None, outlook=None):
        return {"symbol": sym, "kind": "stock", "name": name, "type": typ, "quantity": qty, "avg_price": avg,
                "price": price, "currency": "INR", "profit_margin": pm, "outlook": outlook}

    def fund(code, name, typ, units, avg, nav, yr):
        return {"symbol": f"MF:{code}", "kind": "fund", "name": name, "type": typ, "quantity": units,
                "avg_price": avg, "price": nav, "currency": "INR", "asset_kind": "equity",
                "year_return": yr, "bench_year": 0.08, "bench_name": "Nifty 50"}
    return [
        fund("120586", "ICICI Pru Bluechip", "Large-cap fund", 1000, 100, 118, 0.06),
        fund("120166", "Kotak Equity Opportunities", "Mid-cap fund", 1000, 100, 125, 0.21),
        stock("TATAPOWER.NS", "Tata Power", "Large-cap · Utilities", 125, 400, 576,
              outlook={"label": "Bullish", "date": "2026-10-12"}),
        stock("GREENPOWER.NS", "GreenPower Ltd", "Small-cap · Utilities", 400, 100, 60, pm=-0.12,
              outlook={"label": "Bearish", "date": "2026-10-20"}),
        stock("OLAELEC.NS", "Ola Electric", "Mid-cap · Consumer Cyclical", 1000, 50, 35, pm=-0.4),
    ]


def test_the_owner_example_adds_up():
    rep = preport.build(owner_example())
    by = {r["name"]: r for r in rep["rows"]}
    assert by["Tata Power"]["invested"] == 50000 and by["Tata Power"]["value"] == 72000
    assert by["Tata Power"]["gain_pct"] == pytest.approx(0.44)
    assert by["GreenPower Ltd"]["gain_pct"] == pytest.approx(-0.40)
    t = rep["totals"]
    assert t["invested"] == 340000 and t["value"] == 374000 and t["gain_pct"] == pytest.approx(0.10)
    assert rep["summary"] == {"up": 3, "down": 2, "count": 5, "funds": 2}
    # Biggest first.
    assert [r["name"] for r in rep["rows"]][:2] == ["Kotak Equity Opportunities", "ICICI Pru Bluechip"]


def test_the_what_we_see_notes_state_facts():
    by = {r["name"]: r for r in preport.build(owner_example())["rows"]}
    assert "Your biggest gainer" in by["Tata Power"]["notes"]
    assert "Your biggest fall" in by["GreenPower Ltd"]["notes"]
    assert "The company is losing money (latest results)" in by["Ola Electric"]["notes"]
    assert "33 of every 100 of your money" in by["Kotak Equity Opportunities"]["notes"]
    assert "Past year +21% · Nifty 50 +8%" in by["Kotak Equity Opportunities"]["notes"]
    assert by["Tata Power"]["outlook"] == {"label": "Bullish", "date": "2026-10-12"}


def test_nothing_in_the_report_tells_anyone_what_to_do():
    rep = preport.build(owner_example())
    for r in rep["rows"]:
        for text in r["notes"] + [r["type"], r["name"]]:
            assert not ADVICE.search(text), text


def test_debt_funds_are_not_compared_with_the_nifty():
    item = {"symbol": "MF:119091", "kind": "fund", "name": "HDFC Liquid", "type": "Liquid fund", "quantity": 10,
            "avg_price": 4800, "price": 4950, "currency": "INR", "asset_kind": "debt", "year_return": 0.068,
            "bench_year": 0.08}
    row = preport.build([item])["rows"][0]
    assert row["notes"] == ["Past year +6.8%"]


def test_an_unpriced_row_is_shown_but_left_out_of_the_totals():
    items = owner_example()
    items[4]["price"] = None
    rep = preport.build(items)
    ola = next(r for r in rep["rows"] if r["name"] == "Ola Electric")
    assert ola["value"] is None and ola["notes"] == ["Price not available right now"]
    assert rep["totals"]["invested"] == 290000 and rep["rows"][-1]["name"] == "Ola Electric"


def test_mixed_currencies_give_no_total():
    items = owner_example()[:2] + [{"symbol": "AAPL", "kind": "stock", "name": "Apple", "type": "Stock",
                                    "quantity": 1, "avg_price": 100, "price": 120, "currency": "USD"}]
    rep = preport.build(items)
    assert rep["totals"] is None and rep["mixed_currencies"] is True


@pytest.mark.parametrize("cap,cur,label", [(2e12, "INR", "Large-cap"), (5e11, "INR", "Mid-cap"),
                                           (1e10, "INR", "Small-cap"), (5e9, "USD", "Mid-cap"),
                                           (None, "INR", None), (1e12, "GBP", None)])
def test_company_size(cap, cur, label):
    assert preport.cap_size(cap, cur) == label


def test_stock_type_label():
    assert preport.stock_type(2e12, "INR", "Energy") == "Large-cap · Energy"
    assert preport.stock_type(None, "INR", None) == "Stock"


def test_outlook_needs_to_be_recent():
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    assert preport.outlook_from({"decision": "SELL"}, "2026-10-20T10:00:00+00:00", now) == \
        {"label": "Bearish", "date": "2026-10-20"}
    assert preport.outlook_from({"outlook": "BULLISH"}, "2026-10-01T00:00:00", now)["label"] == "Bullish"
    assert preport.outlook_from({"decision": "BUY"}, "2026-08-01T00:00:00+00:00", now) is None
    assert preport.outlook_from(None, "2026-10-20T00:00:00", now) is None


# --- the endpoint -------------------------------------------------------------------

class FakeAnalyses:
    def __init__(self, docs):
        self.docs = docs

    async def find_one(self, flt, projection=None, sort=None):
        return self.docs.get(flt["symbol"])


@pytest.fixture
def fake_upstream(monkeypatch):
    recent = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(proutes, "db", type("DB", (), {"analyses": FakeAnalyses(
        {"TATAPOWER.NS": {"verdict": {"decision": "BUY"}, "updated_at": recent}})})())
    monkeypatch.setattr(proutes, "_stock_item_sync", lambda s: {
        "kind": "stock", "price": 576.0, "currency": "INR", "name": "Tata Power", "type": "Large-cap · Utilities"})
    monkeypatch.setattr(proutes, "_fund_item_sync", lambda c: {
        "kind": "fund", "price": 125.0, "currency": "INR", "name": "Kotak Emerging Equity", "type": "Mid-cap fund",
        "asset_kind": "equity", "year_return": 0.21})
    monkeypatch.setattr(proutes, "_year_change", lambda s: 0.08)


def test_the_endpoint_prices_stocks_and_funds(fake_upstream):
    body = proutes.ReportRequest(holdings=[
        proutes.PortfolioHolding(symbol="tatapower.ns", quantity=125, avg_price=400),
        proutes.PortfolioHolding(symbol="MF:120166", quantity=1000, avg_price=100)])
    rep = asyncio.run(proutes.portfolio_report(body))
    by = {r["symbol"]: r for r in rep["rows"]}
    assert by["TATAPOWER.NS"]["value"] == 72000 and by["TATAPOWER.NS"]["outlook"]["label"] == "Bullish"
    assert by["MF:120166"]["value"] == 125000 and "Past year +21% · Nifty 50 +8%" in by["MF:120166"]["notes"]
    assert rep["totals"]["gain"] == 47000


@pytest.mark.parametrize("holdings,status", [
    ([("BAD SYMBOL!", 1, 1)], 400),
    ([("MF:abc", 1, 1)], 400),
    ([("INFY.NS", 1, 1), ("infy.ns", 1, 1)], 400),
    ([("INFY.NS", 0, 1)], 400),
])
def test_bad_requests_are_refused(fake_upstream, holdings, status):
    body = proutes.ReportRequest(holdings=[proutes.PortfolioHolding(symbol=s, quantity=q, avg_price=a)
                                           for s, q, a in holdings])
    with pytest.raises(HTTPException) as e:
        asyncio.run(proutes.portfolio_report(body))
    assert e.value.status_code == status


def test_fund_search_endpoint(monkeypatch):
    monkeypatch.setattr(mf, "all_funds", lambda: mf.parse_navall(NAVALL))
    out = asyncio.run(proutes.mf_search("kotak emerging"))
    assert out["results"][0]["symbol"] == "MF:120166"
    assert asyncio.run(proutes.mf_search("k")) == {"results": []}
