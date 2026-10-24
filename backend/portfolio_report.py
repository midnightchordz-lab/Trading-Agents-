"""The portfolio report: one row per stock or fund, the way people read a
statement — what it is, what went in, what it is worth, how it moved, and a
short "what we see" line.

"What we see" states facts (your biggest gainer, how much of your money it
is, the company is losing money, the fund against the Nifty) and the AI
desk's latest outlook for the stock. It never says hold, add, exit or any
other instruction: in India a call on a named security is SEBI Research
Analyst activity (tests hold this, as they do for the rest of the app).

Pure functions over data the route has already fetched, so it is testable
offline.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

# Approximate SEBI/AMFI size bands by market value (AMFI's list ranks the top
# 100 companies as large-cap and the next 150 as mid-cap; these cut-offs track
# that list). Labelled as approximate in the app.
CAP_BANDS = {
    "INR": (1.0e12, 3.0e11),    # ₹1 lakh crore, ₹30,000 crore
    "USD": (1.0e10, 2.0e9),     # $10 billion, $2 billion
}
BIG_SHARE = 0.20                # "x of every 100 of your money" from here up
OUTLOOK_MAX_AGE_DAYS = 45

_OUTLOOK = {"BULLISH": "Bullish", "BUY": "Bullish", "NEUTRAL": "Neutral", "HOLD": "Neutral",
            "BEARISH": "Bearish", "SELL": "Bearish"}


def cap_size(market_cap: Optional[float], currency: Optional[str]) -> Optional[str]:
    bands = CAP_BANDS.get((currency or "").upper())
    if not bands or not market_cap or market_cap <= 0:
        return None
    large, mid = bands
    return "Large-cap" if market_cap >= large else "Mid-cap" if market_cap >= mid else "Small-cap"


def stock_type(market_cap, currency, sector) -> str:
    parts = [p for p in (cap_size(market_cap, currency), sector) if p]
    return " · ".join(parts) or "Stock"


def outlook_from(verdict: Optional[dict], updated_at: Optional[str],
                 now: Optional[datetime] = None) -> Optional[dict]:
    """The AI desk's latest outlook for a stock, if it is recent enough to mean
    anything: {"label": "Bullish", "date": "2026-10-12"}."""
    if not verdict:
        return None
    label = _OUTLOOK.get(str(verdict.get("outlook") or verdict.get("decision") or "").upper())
    if not label or not updated_at:
        return None
    try:
        when = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if (now - when).days > OUTLOOK_MAX_AGE_DAYS:
        return None
    return {"label": label, "date": when.date().isoformat()}


def _pct(x: float, digits: int = 0) -> str:
    return f"{x * 100:+.{digits}f}%"


def build(items: list[dict], cash: float = 0.0) -> dict:
    """items: one per holding, already priced:
      {symbol, kind: "stock"|"fund", name, type, quantity, avg_price,
       price (None if unavailable), currency, price_date?,
       profit_margin?, year_return?, bench_year?, bench_name?, asset_kind?,
       outlook? ({label, date})}
    Returns {rows, totals, summary}."""
    rows = []
    for it in items:
        qty, avg, price = float(it["quantity"]), float(it["avg_price"]), it.get("price")
        invested = qty * avg
        value = qty * price if price is not None else None
        gain = value - invested if value is not None else None
        rows.append({
            "symbol": it["symbol"], "kind": it["kind"], "name": it.get("name") or it["symbol"],
            "type": it.get("type") or ("Mutual fund" if it["kind"] == "fund" else "Stock"),
            "quantity": qty, "avg_price": avg, "price": price, "price_date": it.get("price_date"),
            "currency": (it.get("currency") or "").upper() or None,
            "invested": round(invested, 2),
            "value": round(value, 2) if value is not None else None,
            "gain": round(gain, 2) if gain is not None else None,
            "gain_pct": (gain / invested) if gain is not None and invested > 0 else None,
            "outlook": it.get("outlook"),
            "notes": [],
            "_it": it,
        })

    priced = [r for r in rows if r["value"] is not None]
    currencies = {r["currency"] for r in priced}
    single = next(iter(currencies)) if len(currencies) == 1 else None
    total_value = sum(r["value"] for r in priced) if single else None

    # Biggest gainer / fall, only when there is more than one to compare.
    with_pct = [r for r in priced if r["gain_pct"] is not None]
    if len(with_pct) >= 2:
        best = max(with_pct, key=lambda r: r["gain_pct"])
        worst = min(with_pct, key=lambda r: r["gain_pct"])
        if best["gain_pct"] > 0:
            best["notes"].append("Your biggest gainer")
        if worst["gain_pct"] < 0 and worst is not best:
            worst["notes"].append("Your biggest fall")

    for r in rows:
        it = r.pop("_it")
        if total_value and r["value"] is not None:
            share = r["value"] / total_value
            r["share"] = round(share, 4)
            if share >= BIG_SHARE and len(priced) > 1:
                r["notes"].append(f"{share * 100:.0f} of every 100 of your money")
        else:
            r["share"] = None
        if r["kind"] == "stock":
            pm = it.get("profit_margin")
            if pm is not None and pm < 0:
                r["notes"].append("The company is losing money (latest results)")
        else:
            yr, bench = it.get("year_return"), it.get("bench_year")
            if yr is not None:
                if bench is not None and it.get("asset_kind") == "equity":
                    r["notes"].append(f"Past year {_pct(yr)} · {it.get('bench_name') or 'Nifty 50'} {_pct(bench)}")
                else:
                    r["notes"].append(f"Past year {_pct(yr, 1)}")
        if r["price"] is None:
            r["notes"] = ["Price not available right now"]
        r["notes"] = r["notes"][:3]

    totals = None
    if single and priced:
        invested = sum(r["invested"] for r in priced)
        value = total_value
        totals = {
            "currency": single, "invested": round(invested, 2), "value": round(value, 2),
            "gain": round(value - invested, 2),
            "gain_pct": (value - invested) / invested if invested > 0 else None,
            "cash": round(float(cash or 0), 2),
        }
    up = sum(1 for r in with_pct if r["gain_pct"] > 0)
    down = sum(1 for r in with_pct if r["gain_pct"] < 0)
    rows.sort(key=lambda r: (r["value"] is None, -(r["value"] or 0)))
    return {
        "rows": rows,
        "totals": totals,
        "mixed_currencies": len(currencies) > 1,
        "summary": {"up": up, "down": down, "count": len(rows),
                    "funds": sum(1 for r in rows if r["kind"] == "fund")},
    }
