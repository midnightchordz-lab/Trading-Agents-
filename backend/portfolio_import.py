"""Portfolio holdings import — turns an uploaded spreadsheet into rows.

Pure parsing: bytes in, plain dicts out. No network, no database, so it is
unit-testable on its own; resolving a stock NAME to a ticker happens in the
route (routes/portfolio.py), which needs Yahoo search.

Accepted: .xlsx and .csv with a header row containing a name/symbol column, a
quantity column and an average-price column. Header names are matched loosely
so a broker's own export works as-is (Zerodha: "Instrument, Qty., Avg. cost";
Groww: "Stock Name, Quantity, Average buy price"; plain "Name, Quantity, Avg
Price").

Everything here treats the file as hostile: size, row count and the xlsx
archive's unpacked size are capped before parsing, macro-enabled and legacy
binary formats are refused, openpyxl runs read-only with cached values only
(no formulas evaluated), and defusedxml is installed so openpyxl's XML parsing
is protected against entity-expansion attacks.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from typing import Optional

MAX_FILE_BYTES = 1_000_000          # a 50-holding sheet is a few KB
MAX_UNZIPPED_BYTES = 20_000_000     # xlsx is a zip: cap what it can expand to
MAX_DATA_ROWS = 200                 # rows scanned below the header
MAX_COLUMNS = 40                    # columns read per row; holdings sheets are narrow
MAX_HOLDINGS = 50                   # same cap as /portfolio/optimize
HEADER_SCAN_ROWS = 15               # broker exports put a few title rows first


class HoldingsFileError(ValueError):
    """A problem with the whole file; its message is safe to show the user."""


def _norm(s: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


# Matched against the normalized header. Order matters within each list: an
# exact symbol/ticker column wins over a free-text name column when a sheet has
# both (e.g. "Symbol" and "Company Name").
SYMBOL_HEADERS = ["symbol", "ticker", "trading symbol", "tradingsymbol", "stock symbol",
                  "nse symbol", "bse symbol", "scrip code", "instrument", "scrip"]
NAME_HEADERS = ["stock name", "stock", "name", "company", "company name", "security",
                "security name", "scrip name", "share", "share name", "holding"]
QTY_HEADERS = ["quantity", "qty", "shares", "units", "no of shares", "number of shares",
               "quantity available", "net qty", "net quantity", "holding quantity"]
PRICE_HEADERS = ["avg price", "average price", "avg cost", "average cost", "avg buy price",
                 "average buy price", "buy price", "purchase price", "cost price",
                 "buy avg", "avg", "price", "rate"]


def _find(headers: list[str], wanted: list[str]) -> Optional[int]:
    for w in wanted:
        for i, h in enumerate(headers):
            if h == w:
                return i
    return None


def _locate_header(rows: list[list[object]]) -> tuple[int, int, int, int]:
    """(header_row_index, label_col, qty_col, price_col) or raise."""
    for r, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        headers = [_norm(c) for c in row]
        label = _find(headers, SYMBOL_HEADERS)
        if label is None:
            label = _find(headers, NAME_HEADERS)
        qty = _find(headers, QTY_HEADERS)
        price = _find(headers, PRICE_HEADERS)
        if label is not None and qty is not None and price is not None:
            return r, label, qty, price
    raise HoldingsFileError(
        "Couldn't find the columns. The first row should have headings for the stock "
        "name (or symbol), the quantity and the average price — for example: "
        "Name, Quantity, Avg Price."
    )


_NUM_JUNK = re.compile(r"[\u20b9$\u20ac\xa3,\s]|rs\.?|inr|usd", re.IGNORECASE)


def _number(v: object) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    else:
        s = _NUM_JUNK.sub("", str(v))
        if not s:
            return None
        try:
            f = float(s)
        except ValueError:
            return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _read_csv(data: bytes) -> list[list[object]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for row in csv.reader(io.StringIO(text), dialect):
        rows.append(row[:MAX_COLUMNS])
        if len(rows) > HEADER_SCAN_ROWS + MAX_DATA_ROWS:
            break
    return rows


def _read_xlsx(data: bytes) -> list[list[object]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED_BYTES:
                raise HoldingsFileError("That spreadsheet is too large to import.")
            if any(i.filename.lower().endswith("vbaproject.bin") for i in z.infolist()):
                raise HoldingsFileError("Macro-enabled spreadsheets aren't accepted. Save it as a normal .xlsx or CSV.")
    except zipfile.BadZipFile:
        raise HoldingsFileError("That file isn't a valid .xlsx spreadsheet.")

    import openpyxl  # imported here so the CSV path never needs it

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise HoldingsFileError("Couldn't open that spreadsheet. Try saving it again as .xlsx or CSV.")
    try:
        ws = wb.worksheets[0]
        rows = []
        for row in ws.iter_rows(max_col=MAX_COLUMNS, values_only=True):
            rows.append(list(row))
            if len(rows) > HEADER_SCAN_ROWS + MAX_DATA_ROWS:
                break
        return rows
    finally:
        wb.close()


def parse_holdings_file(filename: str, data: bytes) -> list[dict]:
    """Rows as [{row, input, quantity, avg_price, error}]. `row` is the
    1-based sheet row so the app can say "row 7". `error` is None for a usable
    row; otherwise a short reason and the row is shown but not importable.
    Raises HoldingsFileError for a problem with the whole file."""
    if not data:
        raise HoldingsFileError("That file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise HoldingsFileError("That file is too large (1 MB max).")

    ext = (filename or "").lower().rsplit(".", 1)[-1] if "." in (filename or "") else ""
    if ext == "csv":
        rows = _read_csv(data)
    elif ext == "xlsx":
        rows = _read_xlsx(data)
    elif ext in ("xls", "xlsm", "xlsb", "ods", "numbers"):
        raise HoldingsFileError("Please save the sheet as .xlsx or CSV and upload that.")
    else:
        raise HoldingsFileError("Only .xlsx and .csv files can be imported.")

    header_at, label_col, qty_col, price_col = _locate_header(rows)
    out: list[dict] = []
    seen: set[str] = set()
    body = rows[header_at + 1: header_at + 1 + MAX_DATA_ROWS]
    for offset, row in enumerate(body):
        cells = list(row) + [None] * (max(label_col, qty_col, price_col) + 1 - len(row))
        label = str(cells[label_col] or "").strip()
        qty, price = _number(cells[qty_col]), _number(cells[price_col])
        if not label and qty is None and price is None:
            continue  # blank line
        if _norm(label) in ("total", "grand total", "net total"):
            continue
        entry = {"row": header_at + 2 + offset, "input": label[:80], "quantity": qty,
                 "avg_price": price, "error": None}
        if not label:
            entry["error"] = "No stock name"
        elif qty is None or qty <= 0:
            entry["error"] = "Quantity must be a number above 0"
        elif price is None or price <= 0:
            entry["error"] = "Average price must be a number above 0"
        elif label.upper() in seen:
            entry["error"] = "Listed twice — only the first row is used"
        if entry["error"] is None:
            seen.add(label.upper())
        out.append(entry)

    if not out:
        raise HoldingsFileError("No holdings found under the heading row.")
    if sum(1 for e in out if e["error"] is None) > MAX_HOLDINGS:
        raise HoldingsFileError(f"Too many holdings — the portfolio supports up to {MAX_HOLDINGS}.")
    return out


# --- Picking a ticker from Yahoo search results ------------------------------
TRADEABLE_TYPES = {"EQUITY", "ETF", "MUTUALFUND", "INDEX", "FUTURE", "CRYPTOCURRENCY"}


def choose_symbol(user_input: str, candidates: list[dict]) -> tuple[str, Optional[str], list[dict]]:
    """(status, symbol, candidates) for one sheet row.

    status "ok"        -> an unambiguous match; import it as `symbol`.
    status "check"     -> best guess in `symbol`, but the user should confirm
                          (several plausible listings, e.g. NSE vs BSE).
    status "not_found" -> nothing tradeable matched.

    Indian users are the main audience, so among equal candidates NSE (.NS) is
    preferred over BSE (.BO), matching how the rest of the app labels Indian
    stocks."""
    cands = [c for c in candidates if (c.get("type") or "").upper() in TRADEABLE_TYPES][:5]
    if not cands:
        return "not_found", None, []
    raw = user_input.strip().upper()
    by_symbol = {c["symbol"].upper(): c for c in cands}
    for exact in (raw, f"{raw}.NS", f"{raw}.BO"):
        if exact in by_symbol:
            return "ok", by_symbol[exact]["symbol"], cands
    # A name match is trusted only when the listing's own name starts with what
    # was typed (or vice versa) — a typo can still return one wrong company.
    wanted = _norm(user_input)

    def _name_matches(name: object) -> bool:
        n = _norm(name)
        return bool(wanted and n) and (n.startswith(wanted) or wanted.startswith(n))

    named = [c for c in cands if _name_matches(c.get("name"))]
    if len(named) == 1:
        return "ok", named[0]["symbol"], cands
    named_nse = [c for c in named if c["symbol"].upper().endswith(".NS")]
    if len(named_nse) == 1:
        return "ok", named_nse[0]["symbol"], cands
    nse = [c for c in cands if c["symbol"].upper().endswith(".NS")]
    best = (nse or cands)[0]["symbol"]
    return "check", best, cands
