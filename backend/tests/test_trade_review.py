"""Trade Review: the three statement parsers, position building, the report,
its wording, and the upload endpoint — offline, on synthetic statement lines
laid out exactly like the real exports (no real statement is in the repo)."""
import asyncio
import os
import re
import sys
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trade_review as tr  # noqa: E402
import tradebook_parsers as tp  # noqa: E402
from routes import trade_review as tr_routes  # noqa: E402

# --- synthetic statements -------------------------------------------------------

UPSTOX = [
    "UPSTOX SECURITIES PRIVATE LIMITED",
    "UCC : XXXXXX",
    "Name : Test Person",
    "PAN : ABCDE1234F",
    "Date Company Amount Exchange Segment Scrip Code Instrument Type Strike Price Expiry Trade Num Trade Time Side Quantity Price",
    "16-10-2026 NIFTY 9711.0 FON FO NIFTY European Put 23000.0 19-10-2026 599615 09:20:12 Buy 65.0 149.4",
    "16-10-2026 NIFTY 6500.0 FON FO NIFTY European Put 23000.0 19-10-2026 599700 09:40:00 Sell 65.0 100.0",
    "14-10-2026 BSX 12843.0 FOB FO BSX European Put 72800.0 15-10-2026 12459570 11:09 Buy 60.0 214.05",
    "14-10-2026 BSX 15000.0 FOB FO BSX European Put 72800.0 15-10-2026 12459571 11:30 Sell 60.0 250.0",
    "06-10-2026 CRUDEOIL 75800.0 MCX CO CRUDEOIL European Put 8400.0 08-10-2026 1 17:37:42 Buy 1.0 758.0",
    "06-10-2026 CRUDEOIL 53500.0 MCX CO CRUDEOIL European Put 8400.0 08-10-2026 2 19:23:34 Sell 1.0 535.0",
    "05-10-2026 NIFTY BEES 4632.12 NSE EQ 590103 Equity 0.0 409961986 14:50:02 Buy 18.0 257.34",
]

KOTAK_TX = [
    "Trade Date Security Transaction Product Order",
    "ISIN Exchange Quantity Market Rate Total Charges STT/CTT",
    "Time Name Type Type Source",
    "OPTSTKTCS",
    "21/09/2026",
    "29SEP202 - NSEDERV Buy Cash Kotak Neo 450 34.8000 15660.00 18.38 0.00",
    "09:37:35",
    "6PE 2100.00",
    "OPTSTKTCS",
    "23/09/2026",
    "29SEP202 - NSEDERV Sell Cash Kotak Neo 450 33.3000 14985.00 18.09 22.48",
    "09:54:15",
    "6PE 2100.00",
    "OPTIDXBAN",
    "04/09/2026 KNIFTY 29SE",
    "- NSEDERV Buy Cash Kotak Neo 120 402.0000 48240.00 32.82 0.00",
    "09:57:12 P2026PE",
    "57000.00",
    "OPTIDXBAN",
    "04/09/2026 KNIFTY 29SE",
    "- NSEDERV Sell Cash Kotak Neo 120 339.3250 40719.00 29.84 61.08",
    "11:10:39 P2026PE",
    "57000.00",
]

KOTAK_GL = [
    "Derivatives",
    "Gain Loss Charges",
    "Security",
    "Scrip Name ISIN Qty. Buy Amt. Sell Amt.",
    "Type > 1",
    "Intraday < 1 Yr. Total GST Brokerage Misc. STT/CTT Total",
    "Yr.",
    "SENSEX",
    "IO 400.00 78,958.87 82,834.86 0.00 3,876.00 - 3,876.00 14.90 30.00 55.11 124.32 224.33",
    "02Apr26 CE 71800",
    "BSE 26May26",
    "OPTSTK 3,750.00 5,10,232.92 4,72,281.42 0.00 -37,951.50 - -37,951.50 66.61 20.00 364.89 708.97 1,160.47",
    "PE 3900",
    "Derivatives Total 4,150.00 5,89,191.79 5,55,116.28 0.00 -34,075.50 0.00 -34,075.50 81.51 50.00 420.00 833.29 1,384.80",
    "Commodity",
    "Gain Loss Charges",
    "GOLDM 29Jul26 PE",
    "OPTFUT 190.00 3,49,781.84 3,77,496.35 0.00 27,714.51 - 27,714.51 60.25 30.00 315.24 188.47 593.96",
    "135000",
    "Grand Total 1.00 1.00 1.00 0.00 -6,360.99 0.00 -6,360.99 141.76 80.00 735.24 1,021.76 1,978.76",
]


# --- parsers ------------------------------------------------------------------------

def test_formats_are_detected_and_unknown_ones_refused():
    assert tp.detect(UPSTOX) == "upstox_trades"
    assert tp.detect(KOTAK_TX) == "kotak_transactions"
    assert tp.detect(KOTAK_GL) == "kotak_gainloss"
    with pytest.raises(tp.TradebookError, match="supported"):
        tp.parse(["Some bank statement", "Opening balance 100.00"])


def test_upstox_rows_and_personal_data_never_read():
    rows = tp.parse_upstox_trades(UPSTOX)
    assert len(rows) == 7
    assert rows[2]["underlying"] == "SENSEX" and rows[2]["segment"] == "index"       # BSX → SENSEX
    assert rows[2]["ts"] == datetime(2026, 10, 14, 11, 9)                             # time without seconds
    crude = rows[4]
    assert crude["segment"] == "commodity" and crude["amount"] == 75800.0             # contract multiplier kept
    assert rows[-1]["segment"] == "equity" and rows[-1]["underlying"] == "NIFTYBEES"
    flat = repr(rows)
    assert "ABCDE1234F" not in flat and "Test Person" not in flat


def test_kotak_transaction_rows_are_stitched_from_wrapped_lines():
    rows = tp.parse_kotak_transactions(KOTAK_TX)
    assert [r["contract"] for r in rows] == ["TCS|2026-09-29|PE|2100"] * 2 + ["BANKNIFTY|2026-09-29|PE|57000"] * 2
    assert rows[0]["segment"] == "stock" and rows[2]["segment"] == "index"
    assert rows[1]["charges"] == pytest.approx(18.09 + 22.48)
    assert rows[2]["ts"] == datetime(2026, 9, 4, 9, 57, 12)


def test_kotak_gainloss_charges_arithmetic():
    rows = tp.parse_kotak_gainloss(KOTAK_GL)
    assert [r["contract"] for r in rows] == ["SENSEX|2026-04-02|CE|71800", "BSE|2026-05-26|PE|3900",
                                             "GOLDM|2026-07-29|PE|135000"]
    s = rows[0]
    # "Total" already has GST + brokerage + misc taken off, not STT.
    assert s["gross_pnl"] == pytest.approx(3876.00 + 14.90 + 30.00 + 55.11)
    assert s["net_pnl"] == pytest.approx(3876.00 - 124.32)
    assert rows[1]["segment"] == "stock" and rows[2]["segment"] == "commodity"


def test_pdf_layer_refuses_non_pdfs_and_oversized_files(monkeypatch):
    with pytest.raises(tp.TradebookError, match="PDF"):
        tp.pdf_lines(b"PK\x03\x04 not a pdf")
    monkeypatch.setattr(tp, "MAX_FILE_BYTES", 10)
    with pytest.raises(tp.TradebookError, match="too large"):
        tp.pdf_lines(b"%PDF-1.7 " + b"0" * 100)


# --- positions --------------------------------------------------------------------

T0 = datetime(2026, 5, 4, 9, 20)


def fill(minutes, side, qty, price, contract="NIFTY|2026-05-07|CE|24000", charges=None, segment="index"):
    und, exp, opt, strike = contract.split("|")
    return {"ts": T0 + timedelta(minutes=minutes), "underlying": und, "segment": segment, "opt": opt,
            "strike": float(strike), "expiry": date.fromisoformat(exp), "side": side, "qty": qty, "price": price,
            "amount": qty * price, "charges": charges, "contract": contract}


def test_positions_close_when_flat_and_reopen_separately():
    fills = [fill(0, "Buy", 65, 100), fill(1, "Buy", 65, 102), fill(30, "Sell", 130, 90),
             fill(40, "Buy", 65, 80), fill(50, "Sell", 65, 120)]
    closed, still = tr.build_positions(fills)
    assert len(closed) == 2 and still == []
    assert closed[0]["pnl"] == pytest.approx(130 * 90 - 65 * 100 - 65 * 102) and closed[0]["entries"] == 2
    assert closed[1]["pnl"] == pytest.approx(65 * 40)


def test_short_first_and_open_positions():
    closed, still = tr.build_positions([fill(0, "Sell", 65, 50), fill(10, "Buy", 65, 30),
                                        fill(20, "Buy", 65, 10, contract="NIFTY|2026-05-04|PE|23000")])
    assert closed[0]["direction"] == "short" and closed[0]["pnl"] == pytest.approx(65 * 20)
    assert still[0]["status"] == "still open"
    _, settled = tr.build_positions([fill(0, "Buy", 65, 10, contract="NIFTY|2026-05-01|PE|23000"),
                                     fill(60 * 24 * 5, "Buy", 65, 10)])
    assert any(s["status"].startswith("settled at expiry") for s in settled)


# --- the report -------------------------------------------------------------------

def habit_fills():
    """Two weeks of trading: one small winner first thing each day, then larger
    losing trades re-entered right after a loss."""
    out = []
    for day in range(10):
        base = day * 24 * 60
        c = f"NIFTY|2026-05-14|CE|{24000 + day * 50}"
        out += [fill(base, "Buy", 65, 100, c, 20), fill(base + 20, "Sell", 65, 110, c, 20)]
        for k in range(3):
            c2 = f"NIFTY|2026-05-14|PE|{23000 + day * 50 + k}"
            start = base + 120 + k * 80
            q = 2600 if k == 2 else 650
            out += [fill(start, "Buy", q, 100, c2, 40), fill(start + 70, "Sell", q, 90, c2, 40)]
    return out


def test_report_from_fills_has_habit_tables_and_tax_view():
    rep = tr.build_report([{"format": "kotak_transactions", "broker": "Kotak Neo", "kind": "fills", "rows": habit_fills()}])
    ids = {t["id"] for t in rep["tables"]}
    assert {"size", "per_day", "nth", "after_loss", "hold", "time", "dte", "segment"} <= ids
    m = rep["money"]
    assert m["gross"] == pytest.approx(10 * (650 - 2 * 6500 - 26000)) and m["charges"] == pytest.approx(10 * (40 + 3 * 80))
    assert m["net"] == pytest.approx(m["gross"] - m["charges"])
    assert rep["tax"]["turnover"] == pytest.approx(10 * (650 + 6500 + 6500 + 26000))
    assert rep["positions"] == 40 and rep["active_days"] == 10
    nth = {r["label"]: r for r in next(t for t in rep["tables"] if t["id"] == "nth")["rows"]}
    assert nth["1st"]["win"] == 1.0 and nth["2nd"]["win"] == 0.0


ADVICE = re.compile(r"\b(buy|sell|should|stop|avoid|recommend|switch|reduce|increase|must|try)\b", re.I)


def test_observations_describe_and_never_advise():
    rep = tr.build_report([{"format": "x", "broker": "x", "kind": "fills", "rows": habit_fills()}])
    assert len(rep["observations"]) >= 5
    for line in rep["observations"]:
        assert not ADVICE.search(line), line
    assert "9 out of 10" in rep["risk_line"]
    joined = " ".join(rep["observations"])
    assert "within 30 minutes of a losing exit" in joined and "largest 25%" in joined


def test_contract_statement_supplies_exact_money_when_both_are_given():
    contracts = tp.parse_kotak_gainloss(KOTAK_GL)
    fills = tp.parse_kotak_transactions(KOTAK_TX)
    rep = tr.build_report([{"format": "kotak_transactions", "broker": "Kotak Neo", "kind": "fills", "rows": fills},
                           {"format": "kotak_gainloss", "broker": "Kotak Neo", "kind": "contracts", "rows": contracts}])
    assert rep["money"]["counted_in"] == "contracts" and rep["money"]["charges_known"] is True
    assert rep["money"]["net"] == pytest.approx(sum(c["net_pnl"] for c in contracts))
    assert any("gain/loss statement" in n for n in rep["notes"])


def test_contracts_only_says_timing_needs_the_trade_file():
    rep = tr.build_report([{"format": "kotak_gainloss", "broker": "Kotak Neo", "kind": "contracts",
                            "rows": tp.parse_kotak_gainloss(KOTAK_GL)}])
    assert rep["positions"] == 0 and any("no trade times" in o for o in rep["observations"])


def test_upstox_without_charges_is_flagged_and_equity_left_out():
    rep = tr.build_report([tp.parse(UPSTOX)])
    assert rep["money"]["charges_known"] is False
    assert any("no charges" in n for n in rep["notes"]) and any("share/ETF" in n for n in rep["notes"])
    assert rep["money"]["gross"] == pytest.approx((6500 - 9711) + (15000 - 12843) + (53500 - 75800))


def test_rupee_formatting():
    assert tr._rupees(-1141788.84) == "−₹11.42 lakh"
    assert tr._rupees(91776.66) == "+₹91,777"
    assert tr._rupees(-25_000_000) == "−₹2.50 crore"


# --- the endpoint -------------------------------------------------------------------

def multipart(files: list[tuple[str, bytes]]):
    boundary = "XBOUNDARYX"
    body = b""
    for name, data in files:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{name}\"\r\n"
                 f"Content-Type: application/pdf\r\n\r\n").encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {"type": "http", "method": "POST", "path": "/api/trade-review", "headers": [
        (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
        (b"content-length", str(len(body)).encode())]}
    return Request(scope, receive)


USER = {"id": "u1"}


@pytest.fixture
def fake_pdfs(monkeypatch):
    pages = {b"%PDF-upstox": UPSTOX, b"%PDF-ktx": KOTAK_TX, b"%PDF-kgl": KOTAK_GL, b"%PDF-other": ["hello"]}
    import tradebook_tabular
    monkeypatch.setattr(tradebook_tabular, "pdf_lines", lambda data: pages[data])


def test_endpoint_reviews_several_files(fake_pdfs):
    rep = asyncio.run(tr_routes.trade_review(multipart([("a.pdf", b"%PDF-ktx"), ("b.pdf", b"%PDF-kgl")]), USER))
    assert [s["format"] for s in rep["sources"]] == ["kotak_transactions", "kotak_gainloss"]


def test_endpoint_refuses_non_pdfs_and_unknown_statements(fake_pdfs):
    with pytest.raises(HTTPException) as e:
        asyncio.run(tr_routes.trade_review(multipart([("a.xlsx", b"PK\x03\x04")]), USER))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        asyncio.run(tr_routes.trade_review(multipart([("a.pdf", b"%PDF-other")]), USER))
    assert e.value.status_code == 400 and "supported" in e.value.detail


def test_endpoint_needs_a_file(fake_pdfs):
    with pytest.raises(HTTPException) as e:
        asyncio.run(tr_routes.trade_review(multipart([]), USER))
    assert e.value.status_code == 400
