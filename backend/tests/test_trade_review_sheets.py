"""Trade Review from Excel / CSV: symbol styles, column matching across
brokers, commodity lots, date-only files, contract P&L sheets and the hostile-
file guards — synthetic sheets only."""
import asyncio
import io
import os
import sys
import zipfile
from datetime import date, datetime

import openpyxl
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trade_review as tr  # noqa: E402
import tradebook_parsers as tp  # noqa: E402
import tradebook_tabular as tt  # noqa: E402


def csv_bytes(rows):
    return "\n".join(",".join(str(c) for c in r) for r in rows).encode()


def xlsx_bytes(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --- contract symbols ---------------------------------------------------------------

@pytest.mark.parametrize("sym,expiry,want", [
    ("NIFTY24OCT24500CE", date(2024, 10, 31), ("NIFTY", date(2024, 10, 31), "CE", 24500.0)),
    ("NIFTY24O1024500CE", None, ("NIFTY", date(2024, 10, 10), "CE", 24500.0)),
    ("BANKNIFTY24D1152000PE", None, ("BANKNIFTY", date(2024, 12, 11), "PE", 52000.0)),
    ("OPTIDXNIFTY22SEP2026PE", None, ("NIFTY", date(2026, 9, 22), "PE", None)),
    ("FUTSTKRELIANCE30OCT2026FF", None, ("RELIANCE", date(2026, 10, 30), "FUT", None)),
    ("NIFTY 23000 PE 19-10-2026", None, ("NIFTY", date(2026, 10, 19), "PE", 23000.0)),
    ("NIFTY 19 OCT 2026 23000 PE", None, ("NIFTY", date(2026, 10, 19), "PE", 23000.0)),
    ("CRUDEOIL25NOVFUT", date(2025, 11, 19), ("CRUDEOIL", date(2025, 11, 19), "FUT", None)),
])
def test_symbol_styles(sym, expiry, want):
    assert tt.resolve_contract(sym, expiry) == want


def test_shares_are_not_contracts():
    assert tt.resolve_contract("RELIANCE") is None and tt.resolve_contract("NIFTYBEES") is None


def test_commodity_lots_only_when_not_already_units():
    assert tt._commodity_units("CRUDEOIL", 1) == (100, True)
    assert tt._commodity_units("CRUDEOIL", 300) == (300, False)
    assert tt._commodity_units("GOLDM", 130) == (130, False)
    assert tt._commodity_units("NATURALGAS", 2) == (2500, True)


# --- trade lists --------------------------------------------------------------------

ZERODHA = [
    ["symbol", "isin", "trade_date", "exchange", "segment", "series", "trade_type", "auction", "quantity", "price",
     "trade_id", "order_id", "order_execution_time", "expiry_date"],
    ["NIFTY24O1024500CE", "", "2024-10-07", "NFO", "FO", "", "buy", "false", "75", "120.5", "1", "1", "2024-10-07T09:20:12", "2024-10-10"],
    ["NIFTY24O1024500CE", "", "2024-10-07", "NFO", "FO", "", "sell", "false", "75", "100", "2", "2", "2024-10-07T10:05:00", "2024-10-10"],
    ["CRUDEOIL24OCT6500PE", "", "2024-10-08", "MCX", "COM", "", "buy", "false", "1", "150", "3", "3", "2024-10-08T19:00:00", "2024-10-16"],
    ["CRUDEOIL24OCT6500PE", "", "2024-10-08", "MCX", "COM", "", "sell", "false", "1", "180", "4", "4", "2024-10-08T20:30:00", "2024-10-16"],
    ["RELIANCE", "INE002A01018", "2024-10-08", "NSE", "EQ", "EQ", "buy", "false", "5", "2800", "5", "5", "2024-10-08T11:00:00", ""],
]


def test_zerodha_tradebook_csv():
    p = tt.parse_any(csv_bytes(ZERODHA))
    assert p["kind"] == "fills" and len(p["rows"]) == 5 and p["lots_assumed"] == 2
    nifty, crude = p["rows"][0], p["rows"][2]
    assert nifty["contract"] == "NIFTY|2024-10-10|CE|24500" and nifty["ts"] == datetime(2024, 10, 7, 9, 20, 12)
    assert nifty["segment"] == "index" and nifty["timed"] is True
    assert crude["segment"] == "commodity" and crude["qty"] == 100 and crude["amount"] == 15000
    assert p["rows"][-1]["segment"] == "equity"
    rep = tr.build_report([p])
    assert rep["money"]["gross"] == pytest.approx(75 * (100 - 120.5) + 100 * (180 - 150))
    assert any("lots" in n for n in rep["notes"]) and any("share/ETF" in n for n in rep["notes"])


UPSTOX_XLSX = [
    ["Trade report"], ["Client: (hidden)"], [],
    ["Date", "Company", "Amount", "Exchange", "Segment", "Scrip Code", "Instrument Type", "Strike Price", "Expiry",
     "Trade Num", "Trade Time", "Side", "Quantity", "Price"],
    ["16-10-2026", "NIFTY", 9711.0, "FON", "FO", "NIFTY", "European Put", 23000.0, "19-10-2026", 1, "09:20:12", "Buy", 65, 149.4],
    ["16-10-2026", "NIFTY", 6500.0, "FON", "FO", "NIFTY", "European Put", 23000.0, "19-10-2026", 2, "09:40:00", "Sell", 65, 100.0],
    ["06-10-2026", "CRUDEOIL", 75800.0, "MCX", "CO", "CRUDEOIL", "European Put", 8400.0, "08-10-2026", 3, "17:37:42", "Buy", 1, 758.0],
]


def test_upstox_style_xlsx_with_title_rows_and_separate_columns():
    p = tt.parse_any(xlsx_bytes(UPSTOX_XLSX))
    assert [r["contract"] for r in p["rows"]] == ["NIFTY|2026-10-19|PE|23000"] * 2 + ["CRUDEOIL|2026-10-08|PE|8400"]
    assert p["rows"][2]["amount"] == 75800.0 and p["lots_assumed"] == 0    # the Amount column wins


def test_date_only_files_drop_the_timing_tables():
    rows = [["Date", "Symbol", "Side", "Qty", "Price"]]
    for d in range(1, 11):
        rows += [[f"{d:02d}-05-2026", "NIFTY 23000 PE 14-05-2026", "Buy", 65, 100],
                 [f"{d:02d}-05-2026", "NIFTY 23000 PE 14-05-2026", "Sell", 65, 90 + d]]
    p = tt.parse_any(csv_bytes(rows))
    assert all(r["timed"] is False for r in p["rows"])
    rep = tr.build_report([p])
    ids = {t["id"] for t in rep["tables"]}
    assert "per_day" in ids and not ({"nth", "after_loss", "hold", "time"} & ids)
    assert any("no times" in n for n in rep["notes"])


# --- contract P&L sheets ---------------------------------------------------------------

def test_kotak_gainloss_sheet_matches_the_pdf_arithmetic():
    rows = [["Scrip Name", "Security Type", "ISIN", "Qty.", "Buy Amt.", "Sell Amt.", "Intraday", "< 1 Yr.", "> 1 Yr.",
             "Total", "GST", "Brokerage", "Misc.", "STT/CTT", "Total"],
            ["SENSEX 02Apr26 CE 71800", "IO", "", "400", "78958.87", "82834.86", "0", "3876.00", "-", "3876.00",
             "14.90", "30.00", "55.11", "124.32", "224.33"],
            ["Derivatives Total", "", "", "", "1", "1", "", "", "", "", "", "", "", "", ""]]
    p = tt.parse_any(csv_bytes(rows))
    (c,) = p["rows"]
    assert c["contract"] == "SENSEX|2026-04-02|CE|71800"
    assert c["gross_pnl"] == pytest.approx(3876 + 14.90 + 30 + 55.11) and c["net_pnl"] == pytest.approx(3876 - 124.32)
    assert c["charges"] == pytest.approx(224.33)


def test_zerodha_pnl_sheet_without_charges():
    rows = [["P&L Statement"], ["Summary"], ["Charges", "1234"], [],
            ["Symbol", "ISIN", "Quantity", "Buy Value", "Sell Value", "Realized P&L"],
            ["NIFTY24OCT24500CE", "", "75", "9037.5", "7500", "-1537.5"],
            ["BANKNIFTY24D1152000PE", "", "30", "6000", "9000", "3000"],
            ["INFY", "INE009A01021", "10", "15000", "16000", "1000"]]
    p = tt.parse_any(xlsx_bytes(rows))
    assert p["kind"] == "contracts" and len(p["rows"]) == 2      # the share row is left out
    rep = tr.build_report([p])
    assert rep["money"]["net"] == pytest.approx(1462.5) and rep["money"]["charges_known"] is False


# --- hostile and unreadable files ------------------------------------------------------

def test_old_xls_macros_and_junk_are_refused():
    with pytest.raises(tp.TradebookError, match="xls"):
        tt.parse_any(b"\xd0\xcf\x11\xe0" + b"\0" * 100)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/vbaProject.bin", b"x")
    with pytest.raises(tp.TradebookError, match="Macro"):
        tt.parse_any(buf.getvalue())
    with pytest.raises(tp.TradebookError, match="columns"):
        tt.parse_any(b"hello,world\n1,2\n")
    with pytest.raises(tp.TradebookError, match="empty"):
        tt.parse_any(b"")


def test_endpoint_accepts_a_csv(monkeypatch):
    from test_trade_review import USER, multipart
    from routes import trade_review as tr_routes
    rep = asyncio.run(tr_routes.trade_review(multipart([("tradebook.csv", csv_bytes(ZERODHA))]), USER))
    assert rep["sources"][0]["format"] == "spreadsheet_trades"
