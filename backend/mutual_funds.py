"""Indian mutual funds: today's NAV, the fund's category, search, 1-year return.

Two free sources:
- AMFI's daily NAV file (https://www.amfiindia.com/spages/NAVAll.txt), the
  official list every fund house reports to. One download gives every open
  scheme's code, name, latest NAV and date, grouped under category headers
  such as "Open Ended Schemes(Equity Scheme - Large Cap Fund)" — which is
  where the fund's type comes from. Cached and refreshed every few hours.
- mfapi.in, a free mirror of AMFI's NAV history, for one fund's past year.
  Optional: if it is down, the report simply leaves out the 1-year line.

A holding is stored as the symbol "MF:<scheme code>", so it never collides
with a stock ticker and every existing duplicate check keeps working.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

from core import logger

NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
HISTORY_URL = "https://api.mfapi.in/mf/{code}"
NAV_TTL = 4 * 3600          # AMFI publishes once a day, late evening
HISTORY_TTL = 12 * 3600
PREFIX = "MF:"

_nav = {"funds": {}, "fetched": 0.0}
_nav_lock = threading.Lock()
_history: dict = {}

_HEADER_RE = re.compile(r"^(.*Schemes?)\s*\((.+)\)\s*$", re.I)

# AMFI category -> the plain label people know.
_PLAIN = [
    ("large & mid cap", "Large & mid-cap fund"),
    ("large cap", "Large-cap fund"),
    ("mid cap", "Mid-cap fund"),
    ("small cap", "Small-cap fund"),
    ("multi cap", "Multi-cap fund"),
    ("flexi cap", "Flexi-cap fund"),
    ("elss", "Tax-saving (ELSS) fund"),
    ("value fund", "Value fund"),
    ("contra", "Contra fund"),
    ("focused", "Focused fund"),
    ("dividend yield", "Dividend-yield fund"),
    ("sectoral", "Sector / theme fund"),
    ("index fund", "Index fund"),
    ("etf", "Exchange-traded fund"),
    ("fund of funds", "Fund of funds"),
    ("fof", "Fund of funds"),
    ("liquid", "Liquid fund"),
    ("overnight", "Overnight fund"),
    ("gilt", "Government bond fund"),
    ("arbitrage", "Arbitrage fund"),
    ("aggressive hybrid", "Aggressive hybrid fund"),
    ("conservative hybrid", "Conservative hybrid fund"),
    ("balanced advantage", "Balanced advantage fund"),
    ("dynamic asset", "Balanced advantage fund"),
    ("multi asset", "Multi-asset fund"),
    ("equity savings", "Equity savings fund"),
    ("retirement", "Retirement fund"),
    ("children", "Children's fund"),
]


def symbol_for(code: str) -> str:
    return f"{PREFIX}{code}"


def code_from(symbol: str) -> Optional[str]:
    """"MF:120586" -> "120586"; None for anything that isn't a fund symbol."""
    if not symbol or not symbol.upper().startswith(PREFIX):
        return None
    code = symbol[len(PREFIX):].strip()
    return code if code.isdigit() and len(code) <= 8 else None


def is_fund(symbol: str) -> bool:
    return code_from(symbol) is not None


def plain_type(category: str) -> str:
    """"Equity Scheme - Large Cap Fund" -> "Large-cap fund"."""
    c = (category or "").lower()
    for key, label in _PLAIN:
        if key in c:
            return label
    if "debt" in c:
        return "Debt fund"
    if "hybrid" in c:
        return "Hybrid fund"
    if "equity" in c:
        return "Equity fund"
    return "Mutual fund"


def asset_kind(category: str) -> str:
    """equity / debt / hybrid / other — decides whether a Nifty comparison makes sense."""
    c = (category or "").lower()
    if "debt" in c or "liquid" in c or "overnight" in c or "gilt" in c:
        return "debt"
    if "hybrid" in c or "arbitrage" in c or "multi asset" in c or "equity savings" in c:
        return "hybrid"
    if "equity" in c or "elss" in c or "index" in c or "etf" in c:
        return "equity"
    return "other"


def parse_navall(text: str) -> dict[str, dict]:
    """AMFI's NAVAll.txt -> {scheme_code: {code, name, nav, date, category, amc}}.

    The file is ';'-separated rows of
    Scheme Code;ISIN Payout/Growth;ISIN Reinvestment;Scheme Name;NAV;Date
    under a category header line and a fund-house line. Rows whose NAV is
    "N.A." or not a number are skipped."""
    funds: dict[str, dict] = {}
    category, amc = "", ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _HEADER_RE.match(line) if ";" not in line else None
        if m:
            category = m.group(2).strip()
            continue
        if ";" not in line:
            amc = line  # a fund-house name line
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 6 or not parts[0].isdigit():
            continue  # the column-title row, or something malformed
        try:
            nav = float(parts[4].replace(",", ""))
        except ValueError:
            continue
        if nav <= 0:
            continue
        funds[parts[0]] = {
            "code": parts[0], "name": parts[3], "nav": nav, "date": parts[5],
            "category": category, "amc": amc,
        }
    return funds


def _download() -> dict[str, dict]:
    r = requests.get(NAV_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    funds = parse_navall(r.text)
    if len(funds) < 1000:
        raise RuntimeError(f"AMFI NAV file looked incomplete ({len(funds)} schemes)")
    return funds


def all_funds() -> dict[str, dict]:
    """Every scheme from AMFI's file, cached. A failed refresh keeps serving
    the last good copy rather than going blank."""
    with _nav_lock:
        if _nav["funds"] and time.time() - _nav["fetched"] < NAV_TTL:
            return _nav["funds"]
        try:
            _nav["funds"] = _download()
            _nav["fetched"] = time.time()
        except Exception as e:
            logger.warning(f"mutual funds: AMFI NAV download failed: {e}")
            if not _nav["funds"]:
                raise
        return _nav["funds"]


def get(code: str) -> Optional[dict]:
    return all_funds().get(code)


def search(query: str, limit: int = 12) -> list[dict]:
    """Funds whose name contains every word of the query, Direct plans and
    Growth options first (the ones most people hold through apps)."""
    words = [w for w in re.split(r"\s+", (query or "").lower().strip()) if w]
    if not words:
        return []
    hits = []
    for f in all_funds().values():
        name = f["name"].lower()
        if all(w in name for w in words):
            rank = (0 if "direct" in name else 1, 0 if "growth" in name else 1, len(name))
            hits.append((rank, f))
    hits.sort(key=lambda h: h[0])
    return [{"symbol": symbol_for(f["code"]), "code": f["code"], "name": f["name"],
             "type": plain_type(f["category"]), "nav": f["nav"], "nav_date": f["date"]}
            for _, f in hits[:limit]]


def _parse_date(s: str) -> Optional[datetime]:
    for fmt in ("%d-%m-%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def one_year_change(history: list[dict]) -> Optional[float]:
    """From mfapi's newest-first [{date: "dd-mm-yyyy", nav: "123.4"}], the
    change from the NAV about a year before the latest one to the latest."""
    rows = []
    for h in history or []:
        d = _parse_date(h.get("date", ""))
        try:
            nav = float(h.get("nav"))
        except (TypeError, ValueError):
            continue
        if d and nav > 0:
            rows.append((d, nav))
    if len(rows) < 2:
        return None
    rows.sort()
    latest_d, latest = rows[-1]
    target = latest_d - timedelta(days=365)
    past = [r for r in rows if r[0] <= target]
    if not past or (target - past[-1][0]).days > 10:
        return None  # the fund is younger than a year, or the history has a gap
    return latest / past[-1][1] - 1


def year_return(code: str) -> Optional[float]:
    """The fund's change over the past year, or None if mfapi has no answer."""
    hit = _history.get(code)
    if hit and time.time() - hit[0] < HISTORY_TTL:
        return hit[1]
    value = None
    try:
        r = requests.get(HISTORY_URL.format(code=code), timeout=15)
        r.raise_for_status()
        value = one_year_change((r.json() or {}).get("data") or [])
    except Exception as e:
        logger.info(f"mutual funds: history for {code} unavailable: {e}")
    _history[code] = (time.time(), value)
    return value
