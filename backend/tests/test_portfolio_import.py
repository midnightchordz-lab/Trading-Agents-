"""Spreadsheet import for the Portfolio tab — parsing and ticker choice.

Pure unit tests: no backend, no network. The route itself only adds Yahoo
search on top of these two functions.
"""
import io
import os
import sys
import zipfile

import openpyxl
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import portfolio_import as pi  # noqa: E402


def xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def good(rows):
    return [r for r in rows if r["error"] is None]


# --- Header detection ---------------------------------------------------------
def test_plain_template_xlsx():
    rows = pi.parse_holdings_file("holdings.xlsx", xlsx([
        ["Name", "Quantity", "Avg Price"],
        ["Reliance Industries", 10, 2450.5],
        ["TCS", 5, 3800],
    ]))
    assert [(r["input"], r["quantity"], r["avg_price"]) for r in rows] == [
        ("Reliance Industries", 10.0, 2450.5), ("TCS", 5.0, 3800.0)]
    assert rows[0]["row"] == 2  # 1-based sheet row, for "row 2" messages


def test_zerodha_style_export_with_title_rows_csv():
    csv = ("Holdings statement,,,\n,,,\n"
           "Instrument,Qty.,Avg. cost,LTP\n"
           "INFY,12,\"1,450.25\",1500\n"
           "HDFCBANK,3,1600,1650\n").encode()
    rows = pi.parse_holdings_file("holdings.csv", csv)
    assert [(r["input"], r["quantity"], r["avg_price"]) for r in good(rows)] == [
        ("INFY", 12.0, 1450.25), ("HDFCBANK", 3.0, 1600.0)]


def test_groww_style_headers_and_currency_symbols():
    rows = pi.parse_holdings_file("g.xlsx", xlsx([
        ["Stock Name", "ISIN", "Quantity", "Average buy price"],
        ["Tata Motors", "INE155A01022", "20", "\u20b9 912.40"],
    ]))
    assert good(rows)[0]["avg_price"] == 912.40


def test_symbol_column_beats_name_column():
    rows = pi.parse_holdings_file("s.csv", b"Company Name,Symbol,Qty,Price\nApple Inc,AAPL,2,180\n")
    assert rows[0]["input"] == "AAPL"


def test_semicolon_csv_with_bom():
    data = "\ufeffName;Quantity;Avg Price\nWipro;7;450\n".encode("utf-8")
    assert good(pi.parse_holdings_file("x.csv", data))[0]["input"] == "Wipro"


def test_missing_columns_is_a_clear_file_error():
    with pytest.raises(pi.HoldingsFileError, match="Name, Quantity, Avg Price"):
        pi.parse_holdings_file("x.csv", b"Stock,Price\nTCS,3800\n")


# --- Row validation -----------------------------------------------------------
def test_bad_rows_are_kept_with_a_reason_and_blanks_and_totals_skipped():
    rows = pi.parse_holdings_file("x.xlsx", xlsx([
        ["Name", "Qty", "Avg Price"],
        ["ITC", 0, 400],
        ["", 5, 100],
        [None, None, None],
        ["SBIN", "abc", 600],
        ["LT", 2, -1],
        ["ITC", 3, 410],
        ["Total", 99, 9999],
    ]))
    reasons = {r["row"]: r["error"] for r in rows}
    assert reasons == {
        2: "Quantity must be a number above 0",
        3: "No stock name",
        5: "Quantity must be a number above 0",
        6: "Average price must be a number above 0",
        7: None,  # ITC again — row 2 was invalid, so this is the first usable one
    }


def test_duplicates_after_first_usable_row_are_flagged():
    rows = pi.parse_holdings_file("x.csv", b"Name,Qty,Price\nTCS,1,10\ntcs,2,20\n")
    assert [r["error"] for r in rows] == [None, "Listed twice \u2014 only the first row is used"]


def test_too_many_holdings_rejected():
    body = "".join(f"S{i},1,1\n" for i in range(pi.MAX_HOLDINGS + 1))
    with pytest.raises(pi.HoldingsFileError, match="up to 50"):
        pi.parse_holdings_file("x.csv", ("Name,Qty,Price\n" + body).encode())


def test_formulas_are_not_evaluated_only_cached_values_read():
    # openpyxl-written files carry no cached value for a formula, so the cell
    # reads as None (an invalid row) instead of the formula text or a result.
    rows = pi.parse_holdings_file("f.xlsx", xlsx([["Name", "Qty", "Price"], ["TCS", "=1+1", 10]]))
    assert rows[0]["error"] == "Quantity must be a number above 0"


def test_only_the_first_columns_are_read():
    wide = ["Name", "Qty", "Price"] + [f"c{i}" for i in range(pi.MAX_COLUMNS + 100)]
    rows = pi.parse_holdings_file("w.csv", (",".join(wide) + "\nTCS,1,10" + ",x" * (pi.MAX_COLUMNS + 100) + "\n").encode())
    assert good(rows)[0]["input"] == "TCS"


# --- Hostile / unsupported files ----------------------------------------------
@pytest.mark.parametrize("name", ["a.xls", "a.xlsm", "a.xlsb", "a.ods", "a.numbers"])
def test_legacy_and_macro_formats_refused(name):
    with pytest.raises(pi.HoldingsFileError, match=".xlsx or CSV"):
        pi.parse_holdings_file(name, b"whatever")


def test_unknown_extension_refused():
    with pytest.raises(pi.HoldingsFileError, match="Only .xlsx and .csv"):
        pi.parse_holdings_file("a.pdf", b"%PDF")


def test_empty_and_oversized_files_refused():
    with pytest.raises(pi.HoldingsFileError, match="empty"):
        pi.parse_holdings_file("a.csv", b"")
    with pytest.raises(pi.HoldingsFileError, match="1 MB"):
        pi.parse_holdings_file("a.csv", b"x" * (pi.MAX_FILE_BYTES + 1))


def test_not_really_an_xlsx():
    with pytest.raises(pi.HoldingsFileError, match="valid .xlsx"):
        pi.parse_holdings_file("a.xlsx", b"Name,Qty,Price\n")


def test_zip_bomb_rejected_before_parsing():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", b"0" * (pi.MAX_UNZIPPED_BYTES + 1))
    assert len(buf.getvalue()) < pi.MAX_FILE_BYTES  # small on disk, huge unpacked
    with pytest.raises(pi.HoldingsFileError, match="too large"):
        pi.parse_holdings_file("bomb.xlsx", buf.getvalue())


def test_macro_payload_inside_xlsx_refused():
    data = xlsx([["Name", "Qty", "Price"], ["TCS", 1, 1]])
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(buf, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item))
        dst.writestr("xl/vbaProject.bin", b"")
    with pytest.raises(pi.HoldingsFileError, match="Macro-enabled"):
        pi.parse_holdings_file("sneaky.xlsx", buf.getvalue())


def test_openpyxl_parses_xml_through_defusedxml():
    """openpyxl switches to defusedxml (entity-expansion / external-entity
    protection) only when it is installed; requirements.txt pins it. If this
    fails, defusedxml is missing and uploaded .xlsx files are parsed unsafely."""
    from openpyxl.xml import DEFUSEDXML
    assert DEFUSEDXML is True


# --- Choosing a ticker from search results -------------------------------------
def c(symbol, name, type_="EQUITY", exch="NSE"):
    return {"symbol": symbol, "name": name, "type": type_, "exchange": exch}


def test_exact_symbol_and_bare_nse_symbol_are_ok():
    assert pi.choose_symbol("AAPL", [c("AAPL", "Apple Inc.", exch="NASDAQ")])[:2] == ("ok", "AAPL")
    found = [c("RELIANCE.NS", "Reliance Industries Limited"), c("RELIANCE.BO", "Reliance Industries Limited", exch="BSE")]
    assert pi.choose_symbol("reliance", found)[:2] == ("ok", "RELIANCE.NS")


def test_company_name_prefers_nse_listing():
    found = [c("TATAMOTORS.BO", "Tata Motors Limited", exch="BSE"), c("TATAMOTORS.NS", "Tata Motors Limited")]
    assert pi.choose_symbol("Tata Motors", found)[:2] == ("ok", "TATAMOTORS.NS")


def test_a_lone_search_hit_with_a_different_name_needs_confirming():
    # A typo can still return exactly one — wrong — company.
    status, symbol, _ = pi.choose_symbol("Relaince", [c("RELAXO.NS", "Relaxo Footwears Limited")])
    assert (status, symbol) == ("check", "RELAXO.NS")


def test_ambiguous_name_suggests_but_asks():
    found = [c("HDFCBANK.NS", "HDFC Bank Limited"), c("HDFCLIFE.NS", "HDFC Life Insurance Company Limited"),
             c("HDFCAMC.NS", "HDFC Asset Management Company Limited")]
    status, symbol, cands = pi.choose_symbol("HDFC", found)
    assert status == "check" and symbol == "HDFCBANK.NS" and len(cands) == 3


def test_non_tradeable_results_ignored():
    assert pi.choose_symbol("xyz", [c("XYZ", "Some Option", type_="OPTION")])[:2] == ("not_found", None)
    assert pi.choose_symbol("xyz", [])[:2] == ("not_found", None)


def test_a_nameless_search_hit_is_never_auto_accepted():
    status, symbol, _ = pi.choose_symbol("Relaince", [c("XYZ.NS", None)])
    assert status == "check" and symbol == "XYZ.NS"
