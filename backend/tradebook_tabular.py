"""Broker exports in Excel / CSV → the same normalised rows as the PDF parsers.

Brokers name their columns differently (Zerodha "trade_type", Upstox "Side",
Kotak "Transaction Type"), so columns are found by matching the header row
loosely against known names, the way the portfolio import does. Two shapes are
recognised:

  - a trade list (date, side, quantity, price, and the contract)  → "fills"
  - a contract P&L list (buy value, sell value, P&L per contract)  → "contracts"

The contract itself arrives either in separate columns (underlying, expiry,
strike, option type — Upstox) or packed into one symbol in one of several
styles, all handled by resolve_contract():
  NIFTY24OCT24500CE (Zerodha monthly)   NIFTY24O1024500CE (Zerodha weekly)
  OPTIDXNIFTY22SEP2026PE (Kotak)        NIFTY 23000 PE 19-10-2026 (spaced)

Commodity quantities are in units at some brokers and in lots at others; when
a sheet has no cash-value column the lot size is applied only if the quantity
is not already a whole multiple of it (see _commodity_units).

Same hostile-file rules as the portfolio import: size caps, a cap on the
unpacked xlsx size, macro workbooks refused, openpyxl read-only with cached
values only.
"""
from __future__ import annotations

import calendar
import csv
import io
import re
import zipfile
from datetime import date, datetime, time
from typing import Optional

from portfolio_import import _norm, _number
from tradebook_parsers import MAX_FILE_BYTES, TradebookError, contract_key, parse, pdf_lines, segment_for

MAX_UNZIPPED_BYTES = 60_000_000
MAX_ROWS = 50_000
MAX_COLUMNS = 60
HEADER_SCAN_ROWS = 40

# Lot sizes (units per lot) for MCX contracts, used only when a sheet gives no
# cash value and its quantity is in lots. Prices are quoted per these units.
COMMODITY_LOT = {
    "CRUDEOIL": 100, "CRUDEOILM": 10, "NATURALGAS": 1250, "NATGASMINI": 250, "GOLD": 100, "GOLDM": 10,
    "GOLDGUINEA": 1, "GOLDPETAL": 1, "GOLDTEN": 1, "SILVER": 30, "SILVERM": 5, "SILVERMIC": 1, "COPPER": 2500,
    "ZINC": 5000, "ZINCMINI": 1000, "ALUMINIUM": 5000, "ALUMINI": 1000, "LEAD": 5000, "LEADMINI": 1000,
    "NICKEL": 1500, "MENTHAOIL": 360, "COTTONCNDY": 48,
}

_COLS = {
    "datetime": ["order execution time", "execution time", "trade date time", "trade datetime", "date time",
                 "exchange time", "order time"],
    "date": ["trade date", "date", "transaction date", "order date", "execution date"],
    "time": ["trade time", "time", "exchange trade time"],
    "symbol": ["symbol", "tradingsymbol", "trading symbol", "contract", "instrument", "scrip name", "security name",
               "security", "scrip", "stock name", "name", "description", "contract name"],
    "underlying": ["underlying", "company", "underlying symbol", "underlying name"],
    "opt": ["option type", "instrument type", "opt type", "call put", "ce pe", "option"],
    "strike": ["strike price", "strike"],
    "expiry": ["expiry date", "expiry", "expiry dt", "maturity"],
    "side": ["side", "trade type", "buy sell", "transaction type", "b s", "action", "type", "order type"],
    "qty": ["quantity", "qty", "traded qty", "filled qty", "trade qty", "units", "shares"],
    "price": ["price", "trade price", "traded price", "rate", "market rate", "avg price", "average price",
              "execution price"],
    "amount": ["amount", "trade value", "value", "total", "net amount", "turnover", "consideration"],
    "charges": ["charges", "total charges"],
    "stt": ["stt", "stt ctt", "ctt"],
    "gst": ["gst"],
    "brokerage": ["brokerage"],
    "misc": ["misc", "other charges", "exchange charges"],
    "exchange": ["exchange", "exch", "segment", "market"],
    "buy": ["buy amt", "buy amount", "buy value", "total buy value", "buy val"],
    "buy_qty": ["buy qty", "buy quantity"],
    "sell_qty": ["sell qty", "sell quantity"],
    "open_qty": ["balance qty", "open quantity", "open qty", "net qty", "net quantity"],
    "sell": ["sell amt", "sell amount", "sell value", "total sell value", "sell val"],
    "pnl": ["realized p l", "realised p l", "realized pnl", "realised pnl", "net p l", "p l", "pnl", "profit loss",
            "gain loss", "total"],
}


class _Header:
    def __init__(self, row: list):
        self.names = [_norm(c) for c in row]
        self.used: set[int] = set()

    def find(self, key: str, avoid_used: bool = True) -> Optional[int]:
        for want in _COLS[key]:
            for i, h in enumerate(self.names):
                if h == want and not (avoid_used and i in self.used):
                    self.used.add(i)
                    return i
        return None


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------
def read_rows(data: bytes) -> list[list]:
    if data.startswith(b"PK"):
        return _read_xlsx(data)
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        raise TradebookError("That's an old .xls file. Open it and save it as .xlsx or CSV, then upload that.")
    return _read_csv(data)


def _read_csv(data: bytes) -> list[list]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    if "\x00" in text[:2000]:
        raise TradebookError("Couldn't read that file. Upload a PDF, .xlsx or CSV statement.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for row in csv.reader(io.StringIO(text), dialect):
        rows.append(row[:MAX_COLUMNS])
        if len(rows) > MAX_ROWS:
            raise TradebookError("That file has too many rows — pick a shorter date range.")
    return rows


def _read_xlsx(data: bytes) -> list[list]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED_BYTES:
                raise TradebookError("That spreadsheet is too large.")
            if any(i.filename.lower().endswith("vbaproject.bin") for i in z.infolist()):
                raise TradebookError("Macro-enabled spreadsheets aren't accepted. Save it as a normal .xlsx or CSV.")
    except zipfile.BadZipFile:
        raise TradebookError("That file isn't a valid .xlsx spreadsheet.")
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise TradebookError("Couldn't open that spreadsheet. Save it again as .xlsx or CSV.")
    try:
        # The first sheet that has a recognisable header wins (some exports
        # put a summary sheet first).
        for ws in wb.worksheets[:5]:
            # Many broker exports declare a wrong sheet size (Upstox's says one
            # row); read-only mode trusts it unless the dimensions are reset.
            ws.reset_dimensions()
            rows = []
            for row in ws.iter_rows(max_col=MAX_COLUMNS, values_only=True):
                rows.append(list(row))
                if len(rows) > MAX_ROWS:
                    raise TradebookError("That spreadsheet has too many rows — pick a shorter date range.")
            if _locate(rows)[0] is not None:
                return rows
        return []
    finally:
        wb.close()


# ---------------------------------------------------------------------------
# Header detection
# ---------------------------------------------------------------------------
def _locate(rows: list[list]) -> tuple[Optional[int], Optional[str]]:
    """(header row index, "fills" | "contracts") or (None, None)."""
    for r, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        names = {_norm(c) for c in row}
        has = lambda key: any(w in names for w in _COLS[key])  # noqa: E731
        if has("symbol") or has("underlying"):
            if has("side") and has("qty") and (has("price") or has("amount")) and (has("date") or has("datetime")):
                return r, "fills"
            if has("buy") and has("sell"):
                return r, "contracts"
    return None, None


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------
_MONTHS = {m.upper(): i for i, m in enumerate(calendar.month_abbr) if m}
_WEEKLY_MONTH = {**{str(i): i for i in range(1, 10)}, "O": 10, "N": 11, "D": 12}
_DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d-%b-%y", "%d %b %y",
                 "%d%b%Y", "%d%b%y", "%d-%m-%y", "%d/%m/%y", "%Y/%m/%d", "%b %d, %Y", "%d %B %Y")


def parse_date(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    if not s:
        return None
    s = re.split(r"[T ](?=\d\d:)", s)[0].strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s.title() if "%b" in fmt or "%B" in fmt else s, fmt).date()
        except ValueError:
            continue
    return None


def parse_time(v) -> Optional[time]:
    if isinstance(v, datetime):
        return v.time() if (v.hour or v.minute or v.second) else None
    if isinstance(v, time):
        return v
    m = re.search(r"(\d{1,2}):(\d\d)(?::(\d\d))?\s*(AM|PM)?", str(v or ""), re.I)
    if not m:
        return None
    h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if m.group(4):
        h = h % 12 + (12 if m.group(4).upper() == "PM" else 0)
    return time(h, mi, se) if h < 24 and mi < 60 and se < 60 else None


def parse_side(v) -> Optional[str]:
    s = _norm(v)
    if s in ("buy", "b", "bought", "purchase", "buy open", "buy close"):
        return "Buy"
    if s in ("sell", "s", "sold", "sale", "sell open", "sell close"):
        return "Sell"
    return None


def parse_opt(v) -> Optional[str]:
    s = _norm(v)
    if s in ("ce", "call", "european call", "c", "ec"):
        return "CE"
    if s in ("pe", "put", "european put", "p", "ep"):
        return "PE"
    if s.startswith("fut") or s in ("ff", "xx"):
        return "FUT"
    return None


def resolve_contract(symbol: str, expiry: Optional[date] = None) -> Optional[tuple]:
    """(underlying, expiry, opt, strike) from one packed symbol, or None.
    `expiry` (from an expiry column) disambiguates weekly codes."""
    raw = str(symbol or "").upper().strip()
    if not raw:
        return None
    if re.search(r"\s", raw):
        # Spaced symbols first: with the spaces removed "NIFTY 19 OCT 2026 23000 PE"
        # would also fit the packed DDMMMYY pattern, with the wrong year.
        spaced = _resolve_spaced(raw, expiry)
        if spaced:
            return spaced
    s = re.sub(r"^(OPTIDX|OPTSTK|OPTFUT|OPTCUR|OPTCOM|FUTIDX|FUTSTK|FUTCOM|FUTCUR)", "", raw.replace(" ", ""))

    # Kotak: UND + DDMMMYYYY + CE|PE|FF|XX [+ strike]
    m = re.fullmatch(r"([A-Z&\-]+?)(\d\d)([A-Z]{3})(\d{4})(CE|PE|FF|XX|FUT)([\d.]+)?", s)
    if m and m.group(3) in _MONTHS:
        exp = date(int(m.group(4)), _MONTHS[m.group(3)], int(m.group(2)))
        opt = parse_opt(m.group(5))
        return m.group(1), exp, opt, float(m.group(6)) if m.group(6) and opt != "FUT" else None
    # UND + DDMMMYY + strike + CE|PE   (NIFTY19OCT2623000PE)
    m = re.fullmatch(r"([A-Z&\-]+?)(\d\d)([A-Z]{3})(\d\d)([\d.]+)(CE|PE)", s)
    if m and m.group(3) in _MONTHS and (expiry is None or expiry.day == int(m.group(2))):
        exp = date(2000 + int(m.group(4)), _MONTHS[m.group(3)], int(m.group(2)))
        return m.group(1), exp, m.group(6), float(m.group(5))
    # Zerodha monthly: UND + YY + MMM + (strike + CE|PE | FUT)
    m = re.fullmatch(r"([A-Z&\-]+?)(\d\d)([A-Z]{3})(?:([\d.]+)(CE|PE)|(FUT))", s)
    if m and m.group(3) in _MONTHS:
        y, mo = 2000 + int(m.group(2)), _MONTHS[m.group(3)]
        exp = expiry or _last_weekday_guess(y, mo)
        return m.group(1), exp, m.group(5) or "FUT", float(m.group(4)) if m.group(4) else None
    # Zerodha weekly: UND + YY + M(1-9,O,N,D) + DD + strike + CE|PE
    m = re.fullmatch(r"([A-Z&\-]+?)(\d\d)([1-9OND])(\d\d)([\d.]+)(CE|PE)", s)
    if m:
        try:
            exp = date(2000 + int(m.group(2)), _WEEKLY_MONTH[m.group(3)], int(m.group(4)))
        except ValueError:
            exp = None
        if exp and (expiry is None or expiry == exp):
            return m.group(1), exp, m.group(6), float(m.group(5))
    return None


def _resolve_spaced(raw: str, expiry: Optional[date]) -> Optional[tuple]:
    """"NIFTY 23000 PE 19-10-2026", "NIFTY 19 OCT 2026 23000 PE", "NIFTY 26OCT 23000 CE"."""
    tokens = raw.replace(",", " ").split()
    if len(tokens) >= 2 and re.fullmatch(r"[A-Z&\-]+", tokens[0]):
        opt = next((parse_opt(t) for t in tokens if parse_opt(t)), None)
        rest = " ".join(t for t in tokens[1:] if not parse_opt(t))
        exp = expiry
        if exp is None:
            dm = re.search(r"(\d{1,2}[-/ ]?[A-Z]{3}[-/ ]?\d{2,4}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}-\d\d-\d\d)", rest)
            if dm:
                exp = parse_date(re.sub(r"(\d)([A-Z])", r"\1 \2", re.sub(r"([A-Z])(\d)", r"\1 \2", dm.group(1))).replace("-", " ")
                                 if re.search(r"[A-Z]", dm.group(1)) else dm.group(1))
                rest = rest.replace(dm.group(1), " ")
        nums = [float(x) for x in re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])", rest)]
        strike = max(nums) if nums and opt in ("CE", "PE") else None
        if opt and exp:
            return tokens[0], exp, opt, strike
    return None


def _last_weekday_guess(y: int, mo: int) -> date:
    """Monthly contracts without an expiry column: the last Thursday of the
    month — only used as a grouping key, never shown as a real date."""
    d = date(y, mo, calendar.monthrange(y, mo)[1])
    while d.weekday() != 3:
        d = d.replace(day=d.day - 1)
    return d


def _commodity_units(underlying: str, qty: float) -> tuple[float, bool]:
    """(units, assumed_lots). Lots are assumed only when the quantity is not
    already a whole number of lots in units (1 lot of crude = 100 barrels:
    qty 1 → lots, qty 300 → units)."""
    lot = COMMODITY_LOT.get(underlying)
    if not lot or lot == 1:
        return qty, False
    if qty % lot == 0:
        return qty, False
    return qty * lot, True


def _is_commodity(exchange: str, underlying: str) -> bool:
    e = _norm(exchange)
    return any(k in e.split() or k in e for k in ("mcx", "ncdex", "commodity", "com")) or underlying in COMMODITY_LOT


# ---------------------------------------------------------------------------
# The two shapes
# ---------------------------------------------------------------------------
def parse_table(rows: list[list]) -> dict:
    at, kind = _locate(rows)
    if at is None:
        raise TradebookError("Couldn't find the columns in that sheet. It needs a heading row with the date, "
                             "symbol, buy/sell, quantity and price — like a broker's tradebook or P&L export.")
    h = _Header(rows[at])
    body = rows[at + 1:]
    out = _fills(h, body) if kind == "fills" else _contracts(h, body)
    out.update(_preamble(rows[:at]))
    return out


_BROKERS = ("Upstox", "Zerodha", "Kotak", "Groww", "Angel One", "Dhan", "ICICI Direct", "HDFC Securities",
            "Motilal Oswal", "5paisa", "Fyers", "Sharekhan")


def _preamble(rows: list[list]) -> dict:
    """Broker name and the statement's own date range from the rows above the
    header. Only these two things are read there — the same rows hold the
    holder's name, PAN and client code, which are never looked at."""
    out: dict = {}
    for row in rows:
        text = " ".join(str(c) for c in row if c is not None)
        low = text.lower()
        if "broker" not in out:
            hit = next((b for b in _BROKERS if b.lower() in low), None)
            if hit:
                out["broker"] = "Kotak Neo" if hit == "Kotak" else hit
        if "period" not in out and ("period" in low or re.search(r"\bfrom\b.*\bto\b", low)):
            dates = [parse_date(d) for d in re.findall(r"\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4}-\d\d-\d\d|\d{1,2}[- ][A-Za-z]{3}[- ]\d{4}", text)]
            dates = [d for d in dates if d]
            if len(dates) >= 2:
                out["period"] = {"from": min(dates).isoformat(), "to": max(dates).isoformat()}
    return out


def _cell(row: list, i: Optional[int]):
    return row[i] if i is not None and i < len(row) else None


def _fills(h: _Header, body: list[list]) -> dict:
    c = {k: h.find(k) for k in ("datetime", "date", "time", "underlying", "opt", "strike", "expiry", "side",
                                "qty", "price", "amount", "charges", "stt", "exchange")}
    c["symbol"] = h.find("symbol")
    out, lots_assumed, skipped = [], 0, 0
    for row in body:
        side = parse_side(_cell(row, c["side"]))
        qty = _number(_cell(row, c["qty"]))
        if side is None or not qty:
            continue
        stamp = _cell(row, c["datetime"])
        d = parse_date(stamp) if c["datetime"] is not None else None
        d = d or parse_date(_cell(row, c["date"]))
        t = parse_time(stamp) if c["datetime"] is not None else None
        t = t or parse_time(_cell(row, c["time"]))
        if d is None:
            skipped += 1
            continue
        expiry_col = parse_date(_cell(row, c["expiry"]))
        und = str(_cell(row, c["underlying"]) or "").upper().replace(" ", "")
        opt = parse_opt(_cell(row, c["opt"]))
        strike = _number(_cell(row, c["strike"]))
        if und and (opt or expiry_col):
            resolved = (und, expiry_col, opt or "FUT", strike if (opt or "FUT") != "FUT" else None)
        else:
            resolved = resolve_contract(_cell(row, c["symbol"]) or und, expiry_col)
        exchange = str(_cell(row, c["exchange"]) or "")
        price = _number(_cell(row, c["price"])) or 0.0
        amount = _number(_cell(row, c["amount"]))
        if resolved is None:
            # No option/future recognised: shares (left out of the review) or unreadable.
            name = str(_cell(row, c["symbol"]) or und).upper().replace(" ", "")
            if not name:
                skipped += 1
                continue
            out.append(_row(datetime.combine(d, t or time()), bool(t), name, "equity", None, None, None,
                            side, qty, price, abs(amount) if amount else qty * price, None))
            continue
        u, exp, o, k = resolved
        commodity = _is_commodity(exchange, u)
        units = qty
        if commodity and not amount:
            units, assumed = _commodity_units(u, qty)
            lots_assumed += assumed
        charges = None
        if c["charges"] is not None or c["stt"] is not None:
            charges = (_number(_cell(row, c["charges"])) or 0.0) + (_number(_cell(row, c["stt"])) or 0.0)
        out.append(_row(datetime.combine(d, t or time()), bool(t), u, segment_for(u, commodity), o,
                        k, exp, side, units, price, abs(amount) if amount else units * price, charges))
    if not out:
        raise TradebookError("No trades found under the heading row.")
    return {"format": "spreadsheet_trades", "broker": "Spreadsheet", "kind": "fills", "rows": out,
            "lots_assumed": lots_assumed, "skipped": skipped}


def _row(ts, timed, und, segment, opt, strike, expiry, side, qty, price, amount, charges) -> dict:
    return {"ts": ts, "timed": timed, "underlying": und, "segment": segment, "opt": opt, "strike": strike,
            "expiry": expiry, "side": side, "qty": qty, "price": price, "amount": round(amount, 4),
            "charges": charges, "contract": contract_key(und, expiry, opt, strike)}


def _contracts(h: _Header, body: list[list]) -> dict:
    c = {k: h.find(k) for k in ("underlying", "opt", "strike", "expiry", "qty", "buy", "sell", "gst", "brokerage",
                                "misc", "stt", "exchange", "buy_qty", "sell_qty", "open_qty")}
    c["symbol"] = h.find("symbol")
    c["pnl"] = h.find("pnl")
    c["charges"] = h.find("charges")
    if c["charges"] is None and c["pnl"] is not None and "total" in h.names[c["pnl"] + 1:]:
        # Kotak repeats "Total": the first is P&L, the second total charges.
        c["charges"] = h.names.index("total", c["pnl"] + 1)
    kotak_layout = all(c[k] is not None for k in ("gst", "brokerage", "misc", "stt"))
    out, still_open = [], []
    for row in body:
        buy, sell = _number(_cell(row, c["buy"])), _number(_cell(row, c["sell"]))
        sym = str(_cell(row, c["symbol"]) or _cell(row, c["underlying"]) or "")
        if buy is None or sell is None or not sym.strip() or "total" in _norm(sym):
            continue
        open_qty = _number(_cell(row, c["open_qty"]))
        bq, sq = _number(_cell(row, c["buy_qty"])), _number(_cell(row, c["sell_qty"]))
        if (open_qty is not None and abs(open_qty) > 1e-9) or (bq is not None and sq is not None and abs(bq - sq) > 1e-9):
            # Opened before (or still open after) the statement's dates: its buy
            # or sell side is missing, so sell − buy is not a result.
            still_open.append(sym.strip())
            continue
        expiry_col = parse_date(_cell(row, c["expiry"]))
        resolved = resolve_contract(sym, expiry_col)
        if resolved is None:
            und = sym.upper().replace(" ", "")
            resolved = (und, expiry_col, parse_opt(_cell(row, c["opt"])), _number(_cell(row, c["strike"])))
            if not resolved[1] and not resolved[2]:
                continue  # a share row; the review covers F&O
        u, exp, o, k = resolved
        pnl = _number(_cell(row, c["pnl"]))
        pnl = sell - buy if pnl is None else pnl
        stt = _number(_cell(row, c["stt"])) or 0.0
        charges = _number(_cell(row, c["charges"])) if c["charges"] is not None else None
        if kotak_layout:
            # As in the PDF: P&L is already net of GST + brokerage + misc, not STT.
            others = sum(_number(_cell(row, c[k])) or 0.0 for k in ("gst", "brokerage", "misc"))
            gross, net = pnl + others, pnl - stt
            charges = charges if charges is not None else others + stt
        else:
            gross = pnl
            net = pnl - charges if charges is not None else pnl
        out.append({"underlying": u, "expiry": exp, "opt": o, "strike": k,
                    "segment": segment_for(u, _is_commodity(str(_cell(row, c["exchange"]) or ""), u)),
                    "qty": _number(_cell(row, c["qty"])) or 0.0, "buy": buy, "sell": sell,
                    "gross_pnl": round(gross, 2), "charges": round(charges, 2) if charges is not None else None,
                    "stt": stt, "net_pnl": round(net, 2), "contract": contract_key(u, exp, o, k)})
    if not out and not still_open:
        raise TradebookError("No F&O contracts found under the heading row.")
    return {"format": "spreadsheet_pnl", "broker": "Spreadsheet", "kind": "contracts", "rows": out,
            "not_closed": len(still_open)}


def parse_any(data: bytes) -> dict:
    """One uploaded file of any supported type → parsed rows."""
    if not data:
        raise TradebookError("That file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise TradebookError("That file is too large (8 MB max).")
    if data.startswith(b"%PDF"):
        return parse(pdf_lines(data))
    return parse_table(read_rows(data))
