"""Trade Review — how someone actually trades, from their own broker statements.

Input: normalised rows from tradebook_parsers (fills with times, and/or one
row per contract with exact P&L and charges). Output: one report dict.

Everything is arithmetic on the user's own past trades — no AI, no market
data, no forecast. Observations describe what happened ("days with 3+
positions lost ₹7.2 lakh"); they never tell the user what to trade or how
(tests hold the wording), and every report carries SEBI's F&O risk line.

A "position" is one contract from the first fill until the quantity is back
to zero. A contract that is not flat by the end of the file is reported as
open (or settled at expiry outside the file) and left out of the P&L, never
guessed.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

SEBI_FO_LINE = ("SEBI's study of individual traders found 9 out of 10 made net losses in equity F&O. "
                "This review describes your own past trades; it is not advice.")
REENTRY_MINUTES = 30
MIN_POSITIONS_FOR_HABITS = 8

SEGMENTS = {"index": "Index options", "stock": "Stock options", "commodity": "Commodity options",
            "equity": "Equity (shares/ETFs)"}


def _rupees(x: float) -> str:
    """₹1,23,456 / ₹12.3 lakh / ₹1.2 crore, with the sign in front."""
    sign = "−" if x < 0 else "+" if x > 0 else ""
    a = abs(x)
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f} crore"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.2f} lakh"
    s = f"{a:,.0f}"
    # Indian grouping for the thousands part.
    if a >= 1e3:
        whole = f"{int(round(a))}"
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join(([head] if head else []) + groups) + "," + tail
    return f"{sign}₹{s}"


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


# ---------------------------------------------------------------------------
# Positions from fills
# ---------------------------------------------------------------------------
def build_positions(fills: list[dict]) -> tuple[list[dict], list[dict]]:
    """(closed positions, still-open contracts)."""
    state: dict[str, dict] = {}
    closed = []
    for f in sorted(fills, key=lambda r: r["ts"]):
        st = state.get(f["contract"])
        if st is None:
            st = state[f["contract"]] = {
                "contract": f["contract"], "underlying": f["underlying"], "segment": f["segment"],
                "opt": f["opt"], "expiry": f["expiry"], "start": f["ts"], "net": 0.0, "cash": 0.0,
                "charges": 0.0, "has_charges": f["charges"] is not None, "first_side": f["side"],
                "open_amount": 0.0, "entry_times": set(), "fills": 0,
            }
        signed = f["qty"] if f["side"] == "Buy" else -f["qty"]
        st["net"] += signed
        st["cash"] += f["amount"] if f["side"] == "Sell" else -f["amount"]
        st["charges"] += f["charges"] or 0.0
        st["fills"] += 1
        if f["side"] == st["first_side"]:
            st["open_amount"] += f["amount"]
            st["entry_times"].add(f["ts"].replace(second=0))
        if abs(st["net"]) < 1e-6:
            closed.append({
                "contract": st["contract"], "underlying": st["underlying"], "segment": st["segment"],
                "opt": st["opt"], "expiry": st["expiry"], "start": st["start"], "end": f["ts"],
                "pnl": round(st["cash"], 2), "charges": round(st["charges"], 2) if st["has_charges"] else None,
                "direction": "long" if st["first_side"] == "Buy" else "short",
                "size": round(st["open_amount"], 2), "entries": len(st["entry_times"]), "fills": st["fills"],
            })
            del state[f["contract"]]
    last = max((f["ts"] for f in fills), default=None)
    still_open = [{
        "contract": st["contract"], "underlying": st["underlying"], "segment": st["segment"],
        "net_qty": st["net"],
        "status": "settled at expiry (outside this file)" if st["expiry"] and last and st["expiry"] < last.date()
        else "holding" if st["segment"] == "equity" else "still open",
    } for st in state.values()]
    return closed, still_open


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
def _group(df: pd.DataFrame, by, order: Optional[list] = None) -> list[dict]:
    g = df.groupby(by, observed=True).agg(n=("pnl", "size"), pnl=("pnl", "sum"),
                                          win=("pnl", lambda s: (s > 0).mean()))
    if order is not None:
        g = g.reindex([o for o in order if o in g.index])
    return [{"label": str(k), "n": int(r.n), "pnl": round(float(r.pnl), 2), "win": round(float(r.win), 3)}
            for k, r in g.iterrows()]


def _table(tid: str, title: str, rows: list[dict], note: Optional[str] = None, label: str = "") -> dict:
    return {"id": tid, "title": title, "label": label, "note": note, "rows": rows}


def _summary(pnls: pd.Series) -> dict:
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    return {
        "count": int(len(pnls)),
        "win_rate": round(float((pnls > 0).mean()), 3) if len(pnls) else None,
        "avg_win": round(float(wins.mean()), 2) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else None,
        "gross_profit": round(float(wins.sum()), 2),
        "gross_loss": round(float(losses.sum()), 2),
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else None,
    }


def position_tables(P: pd.DataFrame, timed: bool = True) -> tuple[list[dict], dict]:
    """The behaviour tables, plus the raw facts the observations are built from.
    Without trade times (some spreadsheets carry only dates) the order of
    trades within a day is unknown, so the timing tables are left out."""
    facts: dict = {}
    tables = []
    P = P.sort_values("start").reset_index(drop=True)
    P["mins"] = (P.end - P.start).dt.total_seconds() / 60
    P["day"] = P.start.dt.date

    # Size
    if len(P) >= MIN_POSITIONS_FOR_HABITS:
        q = pd.qcut(P["size"].rank(method="first"), 4, labels=["Smallest 25%", "2nd", "3rd", "Largest 25%"])
        rows = _group(P.assign(b=q), "b")
        avg = P.assign(b=q).groupby("b", observed=True)["size"].mean()
        for r in rows:
            r["extra"] = f"avg {_rupees(float(avg[r['label']])).lstrip('+')}"
        tables.append(_table("size", "BY POSITION SIZE", rows, "Positions split into four equal groups by money put in.", "Size"))
        facts["size"] = rows

    # Trades per day and Nth trade of the day
    P["nth"] = P.groupby("day").cumcount() + 1
    D = P.groupby("day").agg(n=("pnl", "size"), pnl=("pnl", "sum"))
    D["b"] = pd.cut(D.n, [0, 1, 2, 4, 10_000], labels=["1 position", "2 positions", "3–4 positions", "5+ positions"])
    per_day = [{"label": str(k), "n": int(r.n), "pnl": round(float(r.pnl), 2), "win": round(float(r.win), 3)}
               for k, r in D.groupby("b", observed=True).agg(n=("pnl", "size"), pnl=("pnl", "sum"),
                                                             win=("pnl", lambda s: (s > 0).mean())).iterrows()]
    tables.append(_table("per_day", "BY POSITIONS THAT DAY", per_day, "Each row counts days; win = share of green days.", "Day type"))
    facts.update(per_day=per_day, days=int(len(D)), green_days=int((D.pnl > 0).sum()))
    if not timed:
        return _untimed_tables(P, tables, facts)
    nth = _group(P.assign(b=P.nth.clip(upper=5).map(lambda n: f"{n}{'st' if n == 1 else 'nd' if n == 2 else 'rd' if n == 3 else 'th'}{'+' if n == 5 else ''}")),
                 "b", ["1st", "2nd", "3rd", "4th", "5th+"])
    tables.append(_table("nth", "BY TRADE NUMBER IN THE DAY", nth, None, "Trade of the day"))
    facts["nth"] = nth

    # After a loss
    prev_end = P.end.shift()
    gap = (P.start - prev_end).dt.total_seconds() / 60
    quick = (P.pnl.shift() < 0) & gap.between(0, REENTRY_MINUTES) & (P.start.dt.date == prev_end.dt.date)
    rows = _group(P.assign(b=quick.map({True: f"Within {REENTRY_MINUTES} min of a losing exit", False: "All other positions"})),
                  "b", [f"Within {REENTRY_MINUTES} min of a losing exit", "All other positions"])
    tables.append(_table("after_loss", "AFTER A LOSS", rows, None, "Entry"))
    facts["after_loss"] = rows

    # Holding time
    hb = pd.cut(P.mins, [-1, 10, 30, 60, 120, 375, 10**7],
                labels=["Under 10 min", "10–30 min", "30–60 min", "1–2 hours", "2+ hours, same day", "Overnight or longer"])
    hold = _group(P.assign(b=hb), "b")
    tables.append(_table("hold", "BY HOLDING TIME", hold, None, "Held"))
    facts["hold"] = hold

    # Entry time
    mins = P.start.dt.hour * 60 + P.start.dt.minute
    sb = pd.cut(mins, [-1, 9 * 60 + 30, 11 * 60, 13 * 60, 15 * 60 + 30, 24 * 60],
                labels=["9:15–9:30", "9:30–11:00", "11:00–13:00", "13:00–15:30", "Evening (commodities)"])
    slot = _group(P.assign(b=sb), "b")
    tables.append(_table("time", "BY ENTRY TIME", slot, None, "Entered"))
    facts["time"] = slot

    return _untimed_tables(P, tables, facts)


def _untimed_tables(P: pd.DataFrame, tables: list, facts: dict) -> tuple[list[dict], dict]:
    D = P.groupby("day").agg(n=("pnl", "size"), pnl=("pnl", "sum"))
    # Days to expiry, index options
    idx = P[(P.segment == "index") & P.expiry.notna()]
    if len(idx) >= 5:
        dte = (pd.to_datetime(idx.expiry) - idx.start.dt.normalize()).dt.days
        db = pd.cut(dte, [-1, 0, 1, 3, 7, 10_000], labels=["on expiry day", "1 day before expiry", "2–3 days before expiry",
                                             "4–7 days before expiry", "8+ days before expiry"])
        rows = _group(idx.assign(b=db), "b")
        tables.append(_table("dte", "INDEX OPTIONS · DAYS TO EXPIRY AT ENTRY", rows, None, "Opened"))
        facts["dte"] = rows

    # Weekday
    wd = _group(P.assign(b=P.start.dt.day_name()), "b", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
    tables.append(_table("weekday", "BY WEEKDAY", wd, None, "Day"))

    # Direction
    if (P.direction == "short").any():
        rows = _group(P.assign(b=P.direction.map({"long": "Bought first (option buying)", "short": "Sold first (option writing)"})), "b")
        tables.append(_table("direction", "BUYING VS WRITING", rows, None, "Style"))
    facts["short_share"] = round(float((P.direction == "short").mean()), 3)

    # Worst and best days
    worst = D.nsmallest(5, "pnl")
    best = D.nlargest(3, "pnl")
    tables.append(_table("days", "WORST AND BEST DAYS", [
        {"label": d.strftime("%d %b %Y"), "n": int(r.n), "pnl": round(float(r.pnl), 2), "win": None}
        for d, r in pd.concat([worst, best[best.pnl > 0]]).iterrows()], None, "Day"))
    facts["worst_day"] = (worst.index[0], float(worst.pnl.iloc[0]), int(worst.n.iloc[0])) if len(worst) else None
    return tables, facts


def contract_tables(C: pd.DataFrame, pnl_col: str) -> list[dict]:
    tables = []
    seg = _group(C.assign(pnl=C[pnl_col], b=C.segment.map(SEGMENTS)), "b")
    tables.append(_table("segment", "BY MARKET", seg, None, "Market"))
    und = C.assign(pnl=C[pnl_col]).groupby("underlying").agg(n=("pnl", "size"), pnl=("pnl", "sum"),
                                                              win=("pnl", lambda s: (s > 0).mean())).sort_values("pnl")
    pick = pd.concat([und.head(6), und.tail(4)[und.tail(4).pnl > 0]])
    tables.append(_table("underlying", "WORST AND BEST UNDERLYINGS", [
        {"label": k, "n": int(r.n), "pnl": round(float(r.pnl), 2), "win": round(float(r.win), 3)} for k, r in pick.iterrows()],
        f"{int((und.pnl > 0).sum())} of {len(und)} underlyings made money.", "Underlying"))
    if C.opt.isin(["CE", "PE"]).any():
        tables.append(_table("opt", "CALLS VS PUTS", _group(C[C.opt.isin(["CE", "PE"])].assign(
            pnl=C[pnl_col], b=C.opt.map({"CE": "Calls", "PE": "Puts"})), "b"), None, "Type"))
    if "month" in C:
        tables.append(_table("month", "BY MONTH", _group(C.assign(pnl=C[pnl_col]), "month", sorted(C.month.unique())), None, "Month"))
    return tables


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------
def observations(money: dict, facts: dict, top_losers: Optional[float], n_top: int) -> list[str]:
    """Plain sentences built only from the numbers. Descriptive: no "should",
    no "stop", no strategy names to switch to (tests hold this)."""
    out = []
    if money.get("profit_factor") is not None and money.get("avg_win") and money.get("avg_loss"):
        unit = money.get("counted_in", "positions")
        out.append(f"You won {_pct(money['win_rate'])} of {unit}. The average win was {_rupees(money['avg_win'])} "
                   f"and the average loss {_rupees(money['avg_loss'])}, so every ₹1 won was matched by "
                   f"₹{1 / money['profit_factor']:.2f} lost." if money["profit_factor"] < 1 else
                   f"You won {_pct(money['win_rate'])} of {unit}, with ₹{money['profit_factor']:.2f} won for every ₹1 lost.")
    if top_losers is not None and money.get("net", 0) < 0 and top_losers < 0:
        rest = money["net"] - top_losers
        unit = money.get("counted_in", "positions")
        out.append(f"Your {n_top} biggest losing {unit} lost {_rupees(top_losers).lstrip('−')}; all the other {unit} together "
                   f"made {_rupees(rest)}." if top_losers <= money["net"] else
                   f"Your {n_top} biggest losing {unit} account for {_pct(top_losers / money['net'])} of the total loss.")
    size = facts.get("size")
    if size and money.get("net", 0) < 0:
        big = size[-1]
        rest = sum(r["pnl"] for r in size[:-1])
        total = sum(r["pnl"] for r in size)
        if big["pnl"] < 0 and total < 0 and big["pnl"] / total >= 0.6:
            out.append(f"Your largest 25% of positions ({big['extra']}) made {_rupees(big['pnl'])}; "
                       f"the other 75% together made {_rupees(rest)}.")
    per_day = {r["label"]: r for r in facts.get("per_day", [])}
    light = [per_day[k] for k in ("1 position", "2 positions") if k in per_day]
    heavy = [per_day[k] for k in ("3–4 positions", "5+ positions") if k in per_day]
    if light and heavy:
        lp, hp = sum(r["pnl"] for r in light), sum(r["pnl"] for r in heavy)
        if hp < lp:
            out.append(f"Days with one or two positions made {_rupees(lp)} in total; "
                       f"days with three or more made {_rupees(hp)}.")
    nth = facts.get("nth") or []
    if len(nth) >= 4 and nth[0]["n"] >= 5 and nth[-1]["n"] >= 5 and nth[0]["win"] - nth[-1]["win"] >= 0.1:
        out.append(f"Win rate by trade of the day: {_pct(nth[0]['win'])} for the first, "
                   f"{_pct(nth[-1]['win'])} for the {nth[-1]['label']}.")
    al = facts.get("after_loss") or []
    if len(al) == 2 and al[0]["n"] >= 5 and al[0]["pnl"] < 0 and al[0]["win"] < al[1]["win"]:
        out.append(f"{al[0]['n']} positions were opened within {REENTRY_MINUTES} minutes of a losing exit. They made "
                   f"{_rupees(al[0]['pnl'])} with a {_pct(al[0]['win'])} win rate, against {_pct(al[1]['win'])} for the rest.")
    hold = facts.get("hold") or []
    if hold and money.get("net", 0) < 0:
        worst = min(hold, key=lambda r: r["pnl"])
        if worst["pnl"] < 0 and worst["pnl"] / money["net"] >= 0.4:
            out.append(f"Positions held {worst['label'].lower()} made {_rupees(worst['pnl'])} "
                       f"({_pct(worst['win'])} winners) — the costliest holding time.")
    t = facts.get("time") or []
    if t and money.get("net", 0) < 0:
        worst = min(t, key=lambda r: r["pnl"])
        if worst["pnl"] < 0 and worst["n"] >= 5:
            out.append(f"Entries at {worst['label']} made {_rupees(worst['pnl'])} — the weakest time of day.")
    dte = facts.get("dte") or []
    if dte and money.get("net", 0) < 0:
        worst = min(dte, key=lambda r: r["pnl"])
        if worst["pnl"] < 0 and worst["n"] >= 3:
            out.append(f"Index options opened {worst['label']} made {_rupees(worst['pnl'])} over {worst['n']} positions.")
    if facts.get("worst_day") and money.get("net", 0) < 0:
        d, p, n = facts["worst_day"]
        if p / money["net"] >= 0.15:
            out.append(f"Your worst day, {d.strftime('%d %b')}, made {_rupees(p)} across {n} position{'s' if n > 1 else ''}.")
    if money.get("charges") and money.get("gross") is not None and money["gross"] < 0:
        out.append(f"Charges (brokerage, STT, exchange fees, GST) came to {_rupees(money['charges']).lstrip('+')}, "
                   f"on top of the trading result.")
    return out


# ---------------------------------------------------------------------------
# The whole report
# ---------------------------------------------------------------------------
def build_report(parsed: list[dict]) -> dict:
    fills = [r for p in parsed if p["kind"] == "fills" for r in p["rows"]]
    contracts = [r for p in parsed if p["kind"] == "contracts" for r in p["rows"]]
    if not fills and not contracts:
        raise ValueError("Nothing to review")
    fo_fills = [f for f in fills if f["segment"] != "equity"]
    positions, still_open = build_positions(fo_fills) if fo_fills else ([], [])
    equity_rows = len(fills) - len(fo_fills)
    timed = all(f.get("timed", True) for f in fo_fills)

    P = pd.DataFrame(positions)
    if len(P):
        P["charges"] = pd.to_numeric(P.charges)  # all-None (Upstox) would otherwise be dtype object
    C = pd.DataFrame(contracts)
    tables, facts = [], {}

    # Money: exact from a contract statement when there is one, else from fills.
    if len(C):
        C["charges"] = pd.to_numeric(C.charges)
        exact = bool(C.charges.notna().all())
        C["month"] = pd.to_datetime(C.expiry).dt.strftime("%Y-%m")
        gross = float(C.gross_pnl.sum())
        charges = float(C.charges.fillna(0).sum())
        net = float(C.net_pnl.sum())
        per = _summary(C.net_pnl)
        unit = "contracts"
        top = C.net_pnl.nsmallest(10)
    else:
        exact = len(P) > 0 and P.charges.notna().all()
        gross = float(P.pnl.sum()) if len(P) else 0.0
        charges = float(P.charges.fillna(0).sum()) if len(P) else 0.0
        net = gross - charges
        per = _summary(P.pnl - P.charges.fillna(0)) if len(P) else _summary(pd.Series(dtype=float))
        unit = "positions"
        top = (P.pnl - P.charges.fillna(0)).nsmallest(10) if len(P) else pd.Series(dtype=float)
    money = {"gross": round(gross, 2), "charges": round(charges, 2), "net": round(net, 2),
             "charges_known": bool(exact), **per, "counted_in": unit}
    top = top[top < 0]
    top_losers = float(top.sum()) if len(top) else None

    if len(P):
        P["start"] = pd.to_datetime(P.start)
        P["end"] = pd.to_datetime(P.end)
        if len(P) >= MIN_POSITIONS_FOR_HABITS:
            t, facts = position_tables(P, timed)
            tables += t
        if not len(C):
            P["month"] = P.start.dt.strftime("%Y-%m")
            tables = contract_tables(P.assign(gross_pnl=P.pnl), "gross_pnl") + tables
    if len(C):
        tables = contract_tables(C, "net_pnl") + tables

    # Tax view: F&O is non-speculative business income; turnover by the usual
    # ICAI method is the sum of absolute per-contract profits and losses.
    if len(C):
        per_contract = C.set_index("contract").gross_pnl
    elif len(P):
        per_contract = P.groupby("contract").pnl.sum()
    else:
        per_contract = pd.Series(dtype=float)
    tax = {
        "turnover": round(float(per_contract.abs().sum()), 2),
        "net_pnl_after_charges": round(net, 2),
        "charges": round(charges, 2),
        "charges_exact": money["charges_known"],
        "note": "Turnover here is the sum of each contract's profit or loss counted as positive (the usual ICAI "
                "method for F&O). F&O results are generally non-speculative business income. Take these "
                "figures to your CA — this is not tax advice.",
    }

    stated = [p["period"] for p in parsed if p.get("period")]
    ts = [f["ts"] for f in fills]
    exp = [c["expiry"] for c in contracts]
    period = {"from": (min(ts).date() if ts else min(exp)).isoformat() if ts or exp else None,
              "to": (max(ts).date() if ts else max(exp)).isoformat() if ts or exp else None}

    if stated:
        period = {"from": min(x["from"] for x in stated), "to": max(x["to"] for x in stated)}
    obs = observations(money, facts, top_losers, len(top))
    if not len(P) and len(C):
        obs.append("This statement has no trade times, so timing habits (time of day, holding time, trades "
                   "after a loss) need the transaction statement or trade report as well.")
    notes = []
    if not money["charges_known"]:
        notes.append("This report has no charges, so results are before brokerage, STT and fees. Add the "
                     "gain/loss statement for the same period for exact figures.")
    if len(C) and len(P):
        matched = len(set(C.contract) & set(P.contract))
        notes.append(f"Money figures come from the gain/loss statement ({len(C)} contracts); habits come from the "
                     f"{len(fills)} trades in the transaction file ({matched} contracts appear in both).")
    if fo_fills and not timed:
        notes.append("This file has trade dates but no times, so time of day, holding time and trades after a "
                     "loss can't be shown. A tradebook with execution times adds them.")
    not_closed = sum(p.get("not_closed", 0) for p in parsed)
    if not_closed:
        notes.append(f"{not_closed} contract{'s' if not_closed > 1 else ''} in the summary had only one side "
                     f"(opened before the statement's start date, or still open) and {'were' if not_closed > 1 else 'was'} "
                     "left out of the results.")
    lots = sum(p.get("lots_assumed", 0) for p in parsed)
    if lots:
        notes.append(f"{lots} commodity trades had no cash value, so quantities were read as lots and multiplied "
                     "by the exchange lot size. Check the commodity figures against your broker's P&L.")
    if equity_rows:
        notes.append(f"{equity_rows} share/ETF trades were left out — this review covers F&O and commodities.")

    return {
        "period": period,
        "sources": [{"broker": p["broker"], "format": p["format"], "kind": p["kind"], "rows": len(p["rows"])} for p in parsed],
        "money": money,
        "positions": int(len(P)),
        "contracts": int(len(C)) if len(C) else int(P.contract.nunique()) if len(P) else 0,
        "underlyings": int((C if len(C) else P).underlying.nunique()) if (len(C) or len(P)) else 0,
        "active_days": facts.get("days"),
        "green_days": facts.get("green_days"),
        "observations": obs,
        "tables": tables,
        "tax": tax,
        "open_positions": still_open[:20],
        "notes": notes,
        "risk_line": SEBI_FO_LINE,
    }
