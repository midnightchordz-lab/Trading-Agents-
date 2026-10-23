"""Broker statements → normalised trades, for the Trade Review.

Three formats, all PDFs, all taken from real exports:

  - Upstox "trade report"          — every fill, with time (kind "fills")
  - Kotak Neo "transaction statement" — every fill, with time and exact
                                        charges per fill (kind "fills")
  - Kotak "gain/loss statement"     — one row per contract with exact P&L
                                        and charges, no times (kind "contracts")

Parsing is split in two so it can be tested without any real statement:
pdf_lines() turns PDF bytes into text lines, and each parse_* function works on
those lines only. The tests feed synthetic lines in the same layout.

Statements carry the holder's name, PAN and client code. None of it is read
into the output: only trade rows leave this module. The file itself is never
stored — the route reads it into memory, parses it and drops it.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime
from typing import Optional

MAX_FILE_BYTES = 8_000_000
MAX_PAGES = 120

# Index underlyings, as each broker spells them. Upstox writes Sensex as "BSX".
INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX",
                     "SENSEX50", "BSX"}
_ALIASES = {"BSX": "SENSEX"}


class TradebookError(ValueError):
    """A problem with the whole file; the message is safe to show the user."""


# ---------------------------------------------------------------------------
# PDF → lines
# ---------------------------------------------------------------------------
def pdf_lines(data: bytes) -> list[str]:
    if len(data) > MAX_FILE_BYTES:
        raise TradebookError("That file is too large (8 MB max).")
    if not data.startswith(b"%PDF"):
        raise TradebookError("Please upload the PDF statement your broker gives you.")
    import pdfplumber
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                raise TradebookError(f"That statement has more than {MAX_PAGES} pages — pick a shorter date range.")
            out = []
            for page in pdf.pages:
                out.extend(l.strip() for l in (page.extract_text() or "").splitlines() if l.strip())
            return out
    except TradebookError:
        raise
    except PDFPasswordIncorrect:
        raise TradebookError("This PDF is password-protected. Download it again without a password, "
                             "or open it and save a copy without one.")
    except Exception as e:
        if "password" in str(e).lower() or "encrypt" in str(e).lower():
            raise TradebookError("This PDF is password-protected. Download it again without a password.")
        raise TradebookError("Couldn't read that PDF. Download the statement again and retry.")


def detect(lines: list[str]) -> Optional[str]:
    head = "\n".join(lines[:60])
    if "Gain Loss Charges" in head:
        return "kotak_gainloss"
    if re.search(r"Trade Date Security Transaction", head) and re.search(r"\bOrder\b", head):
        return "kotak_transactions"
    if "UPSTOX" in head.upper() and "Trade Num" in head:
        return "upstox_trades"
    return None


def parse(lines: list[str]) -> dict:
    kind = detect(lines)
    if kind == "upstox_trades":
        return {"format": kind, "broker": "Upstox", "kind": "fills", "rows": parse_upstox_trades(lines)}
    if kind == "kotak_transactions":
        return {"format": kind, "broker": "Kotak Neo", "kind": "fills", "rows": parse_kotak_transactions(lines)}
    if kind == "kotak_gainloss":
        return {"format": kind, "broker": "Kotak Neo", "kind": "contracts", "rows": parse_kotak_gainloss(lines)}
    raise TradebookError("This PDF isn't a supported statement yet. PDFs supported: the Upstox trade report "
                         "and the Kotak Neo transaction or gain/loss statement. Any broker's tradebook or P&L "
                         "also works as .xlsx or CSV.")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _num(s: str) -> float:
    s = s.replace(",", "").strip()
    return 0.0 if s in ("", "-") else float(s)


def segment_for(underlying: str, commodity: bool, equity: bool = False) -> str:
    if equity:
        return "equity"
    if commodity:
        return "commodity"
    return "index" if underlying in INDEX_UNDERLYINGS or _ALIASES.get(underlying) in INDEX_UNDERLYINGS else "stock"


def contract_key(underlying: str, expiry: Optional[date], opt: Optional[str], strike: Optional[float]) -> str:
    """Same key whichever broker wrote the row, so fills and contract
    statements for one account can be matched."""
    exp = expiry.isoformat() if expiry else "-"
    k = f"{strike:g}" if strike else "-"
    return f"{underlying}|{exp}|{opt or '-'}|{k}"


def _fill(ts, underlying, segment, opt, strike, expiry, side, qty, price, amount, charges) -> dict:
    underlying = _ALIASES.get(underlying, underlying)
    return {
        "ts": ts, "underlying": underlying, "segment": segment, "opt": opt, "strike": strike, "expiry": expiry,
        "side": side, "qty": qty, "price": price, "amount": amount, "charges": charges,
        "contract": contract_key(underlying, expiry, opt, strike),
    }


# ---------------------------------------------------------------------------
# Upstox trade report
# ---------------------------------------------------------------------------
_UPX_FO = re.compile(
    r"^(\d\d-\d\d-\d{4}) (\S+) ([\d.]+) (\S+) (\S+) (\S+) (European Put|European Call|Future\S*|\S+ \S+) "
    r"([\d.]+) (\d\d-\d\d-\d{4}) (\d+) (\d\d:\d\d(?::\d\d)?) (Buy|Sell) ([\d.]+) ([\d.]+)$")
_UPX_EQ = re.compile(
    r"^(\d\d-\d\d-\d{4}) (.+?) ([\d.]+) (NSE|BSE) (EQ) (\S+) (Equity) ([\d.]+) (\d+) "
    r"(\d\d:\d\d(?::\d\d)?) (Buy|Sell) ([\d.]+) ([\d.]+)$")
_UPX_COMMODITY_EXCH = {"MCX", "NSX", "NCDEX"}


def _ts(d: str, t: str, fmt: str) -> datetime:
    return datetime.strptime(f"{d} {t if t.count(':') == 2 else t + ':00'}", fmt)


def parse_upstox_trades(lines: list[str]) -> list[dict]:
    """Columns: Date Company Amount Exchange Segment Scrip Instrument Strike
    Expiry TradeNum Time Side Quantity Price. Amount is the cash value (it
    already includes the commodity contract multiplier); no charges."""
    rows = []
    for line in lines:
        m = _UPX_FO.match(line)
        if m:
            d, company, amount, exch, _seg, _scrip, itype, strike, expiry, _no, tm, side, qty, price = m.groups()
            opt = "PE" if "Put" in itype else "CE" if "Call" in itype else "FUT"
            rows.append(_fill(_ts(d, tm, "%d-%m-%Y %H:%M:%S"), company,
                              segment_for(company, exch in _UPX_COMMODITY_EXCH), opt,
                              float(strike) or None, datetime.strptime(expiry, "%d-%m-%Y").date(),
                              side, float(qty), float(price), float(amount), None))
            continue
        m = _UPX_EQ.match(line)
        if m:
            d, company, amount, _exch, _seg, _scrip, _it, _strike, _no, tm, side, qty, price = m.groups()
            rows.append(_fill(_ts(d, tm, "%d-%m-%Y %H:%M:%S"), company.replace(" ", ""), "equity", None, None,
                              None, side, float(qty), float(price), float(amount), None))
    if not rows:
        raise TradebookError("No trades found in that Upstox report.")
    return rows


# ---------------------------------------------------------------------------
# Kotak Neo transaction statement
# ---------------------------------------------------------------------------
_KTX_START = re.compile(r"^(OPT|FUT)[A-Z]{3}")
_KTX_HDR = re.compile(r"^(Trade Date|ISIN|Time Name)")
_KTX_CORE = re.compile(r"-\s+(\S+)\s+(Buy|Sell)\s+(\S+)\s+(.+?)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?=\s|$)")
_KTX_DESC = re.compile(r"^(OPTIDX|OPTSTK|OPTFUT|OPTCUR|FUTIDX|FUTSTK|FUTCOM|FUTCUR)(.+?)(\d\d[A-Z]{3}\d{4})(CE|PE|FF|XX)?$")


def parse_kotak_transactions(lines: list[str]) -> list[dict]:
    """Each row wraps over 3-5 text lines (the instrument name is split across
    them); a row starts at a line beginning OPTIDX/OPTSTK/OPTFUT/FUT..., so the
    lines are grouped into blocks and each block is read as one string."""
    blocks, cur = [], None
    for line in lines:
        if _KTX_HDR.match(line):
            continue
        if _KTX_START.match(line):
            if cur:
                blocks.append(cur)
            cur = [line]
        elif cur is not None:
            cur.append(line)
    if cur:
        blocks.append(cur)
    rows = []
    for b in blocks:
        s = " | ".join(b)
        d = re.search(r"\d\d/\d\d/\d{4}", s)
        t = re.search(r"\d\d:\d\d:\d\d", s)
        m = _KTX_CORE.search(s)
        if not (d and t and m):
            continue
        rest = s.replace(d.group(0), " ").replace(t.group(0), " ").replace(m.group(0), " ")
        k = re.search(r"\s(\d+\.\d\d)\s*(\||$)", rest + " ")
        strike = float(k.group(1)) if k else None
        if k:
            rest = rest[:k.start()] + " " + rest[k.end() - 1:]
        desc = re.sub(r"[\s|]", "", rest)
        dm = _KTX_DESC.match(desc)
        if not dm:
            continue
        itype, und, exp, opt = dm.groups()
        exch, side, _product, _source, qty, rate, total, charges, stt = m.groups()
        opt = opt if opt in ("CE", "PE") else "FUT"
        rows.append(_fill(datetime.strptime(f"{d.group(0)} {t.group(0)}", "%d/%m/%Y %H:%M:%S"), und,
                          segment_for(und, exch == "MCX" or itype.endswith(("FUT", "COM"))),
                          opt, strike if opt != "FUT" else None, datetime.strptime(exp, "%d%b%Y").date(),
                          side, float(qty), float(rate), float(total), float(charges) + float(stt)))
    if not rows:
        raise TradebookError("No trades found in that Kotak statement.")
    return rows


# ---------------------------------------------------------------------------
# Kotak gain/loss statement
# ---------------------------------------------------------------------------
_N = r"(-?[\d,]+\.\d\d|-)"
_KGL_ROW = re.compile(r"^(.*?)\b(OPTIDX|OPTSTK|OPTFUT|OPTCUR|FUTIDX|FUTSTK|FUTCOM|FUTCUR|IO|IF|SO|SF)\s+"
                      + r"\s+".join([_N] * 12) + r"$")
_KGL_HDR = re.compile(r"^(Gain Loss Charges|Security|Scrip Name|Type > 1|Intraday|Yr\.|Derivatives|Commodity|Grand Total)")
_KGL_DESC = re.compile(r"^([A-Z&\-]+)\s*(\d\d[A-Z][a-z]{2}\d\d)\s*(CE|PE|FUT)?\s*([\d.]+)?$")


def parse_kotak_gainloss(lines: list[str]) -> list[dict]:
    """One row per contract: Qty, Buy Amt, Sell Amt, Intraday, <1Yr, >1Yr,
    Total, then charges GST, Brokerage, Misc, STT/CTT, Total.

    Measured against the same account's transaction statement: Buy/Sell Amt
    already include GST + brokerage + misc, so "Total" is net of those but
    NOT of STT. The trading P&L before any charge is therefore
    Total + GST + Brokerage + Misc, and after all charges Total − STT."""
    rows, pending, section = [], [], None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line in ("Derivatives", "Commodity"):
            section = line
        m = _KGL_ROW.match(line)
        if m:
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            tail = nxt if nxt and not _KGL_ROW.match(nxt) and not _KGL_HDR.match(nxt) and "Total" not in nxt else ""
            desc = re.sub(r"\s+", " ", " ".join(pending + [m.group(1), tail])).strip()
            n = [_num(x) for x in m.groups()[2:]]
            dm = _KGL_DESC.match(desc)
            if dm:
                und, exp, opt, strike = dm.groups()
                itype = m.group(2)
                opt = opt or ("FUT" if itype.startswith("FUT") or itype in ("IF", "SF") else None)
                expiry = datetime.strptime(exp, "%d%b%y").date()
                strike_f = float(strike) if strike and opt != "FUT" else None
                gst, brok, misc, stt, chg = n[7], n[8], n[9], n[10], n[11]
                rows.append({
                    "underlying": _ALIASES.get(und, und), "expiry": expiry, "opt": opt, "strike": strike_f,
                    "segment": segment_for(und, section == "Commodity" or itype == "OPTFUT"),
                    "qty": n[0], "buy": n[1], "sell": n[2],
                    "gross_pnl": round(n[6] + gst + brok + misc, 2),
                    "charges": chg, "stt": stt, "net_pnl": round(n[6] - stt, 2),
                    "contract": contract_key(_ALIASES.get(und, und), expiry, opt, strike_f),
                })
            pending = []
            i += 2 if tail else 1
            continue
        if not _KGL_HDR.match(line) and "Total" not in line and line not in ("Derivatives", "Commodity"):
            pending.append(line)
        i += 1
    if not rows:
        raise TradebookError("No contracts found in that gain/loss statement.")
    return rows
