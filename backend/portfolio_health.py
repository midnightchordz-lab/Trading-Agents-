"""Portfolio Health Check — what a portfolio IS, never what to do with it.

Pure functions over price history the route has already fetched. Ideas taken
from the Yale endowment model and Modern Portfolio Theory, reduced to the
parts a retail investor can actually use:

  - diversification across sources of return, not just across tickers
    (effective number of holdings, correlation clusters, sectors, asset classes);
  - how much of the portfolio is simply the market (beta and R² against the
    Nifty 50 or the S&P 500 — the Mladina & Coyle "risk exposure vs skill" split);
  - return per unit of risk (Sharpe ratio), beside the benchmark's;
  - where the portfolio sits against the efficient frontier of its OWN holdings;
  - drift from the investor's own target mix, and liquidity.

Every output is an observation about the past year. Nothing here names a
weight to move to or an asset to buy or sell: the frontier is reported as
points on a curve, never as the mix that produced them, so the screen cannot
be read as an allocation instruction (we are not a SEBI-registered adviser).
"""
from __future__ import annotations

import math
import os
from typing import Optional

import numpy as np
import pandas as pd

TRADING_DAYS = 252
MIN_COMMON_DAYS = 60          # fewer shared trading days than this and the stats are noise
CLUSTER_CORR = 0.70           # pairs at or above this move "as one bet"
DRIFT_BAND = 0.05             # 5 percentage points from target is worth pointing out
LIQUIDITY_DAYS = 20
POSITION_SHARE_OF_DAY = 0.10  # a position over 10% of a normal day's trading is hard to exit quietly
THIN_DAILY_VALUE = {"INR": 1e7, "USD": 1e6}   # ₹1 crore / $1M traded a day


def risk_free_rate(currency: str) -> float:
    """Annual risk-free rate used in the Sharpe ratio: roughly the 91-day
    T-bill yield. Overridable without a deploy because it moves."""
    default = {"INR": "0.065", "USD": "0.04"}.get(currency, "0.04")
    try:
        return float(os.environ.get(f"HEALTH_RF_{currency}", default))
    except ValueError:
        return float(default)


BENCHMARKS = {"INR": ("^NSEI", "Nifty 50"), "USD": ("^GSPC", "S&P 500")}

BUCKETS = {
    "in_equity": "India equity",
    "us_equity": "US equity",
    "other_equity": "Other equity",
    "debt": "Debt & bonds",
    "gold": "Gold",
    "crypto": "Crypto",
    "cash": "Cash",
}
_GOLD_SYMBOLS = {"GOLDBEES", "GOLDCASE", "GOLD1", "SETFGOLD", "HDFCGOLD", "GOLDIETF", "AXISGOLD",
                 "GLD", "IAU", "SGOL", "GLDM", "GC=F"}
_DEBT_SYMBOLS = {"LIQUIDBEES", "LIQUIDCASE", "GILT5YBEES", "EBBETF0430", "BND", "AGG", "TLT", "SHY", "IEF",
                 "BIL", "SGOV", "LQD", "HYG", "TIP"}
_DEBT_WORDS = {"BOND", "BONDS", "GILT", "TREASURY", "DEBT", "LIQUID", "T-BILL"}
_FUNDS = {"ETF", "MUTUALFUND"}


def base_currency(currencies: list[str]) -> str:
    """Rupees when anything is Indian (an Indian investor holding a few US
    names still thinks in rupees), else dollars."""
    return "INR" if "INR" in currencies else "USD"


def asset_bucket(symbol: str, quote_type: Optional[str], currency: Optional[str], name: Optional[str]) -> str:
    """Which asset class a holding belongs to. Name words only count for funds
    and ETFs, so "Goldman Sachs" or "Gold Fields" stay equities."""
    qt = (quote_type or "").upper()
    base = symbol.upper().split(".")[0]
    words = set((name or "").upper().replace("-", " ").replace(",", " ").split())
    if qt == "CRYPTOCURRENCY":
        return "crypto"
    if base in _GOLD_SYMBOLS or base.startswith("SGB") or (qt in _FUNDS and "GOLD" in words):
        return "gold"
    if base in _DEBT_SYMBOLS or (qt in _FUNDS and words & _DEBT_WORDS):
        return "debt"
    if currency == "INR":
        return "in_equity"
    if currency == "USD":
        return "us_equity"
    return "other_equity"


# ---------------------------------------------------------------------------
# Lining the data up
# ---------------------------------------------------------------------------
def to_base(closes: dict[str, float], fx: Optional[dict[str, float]]) -> dict[str, float]:
    """Convert a dated close series into the base currency with a dated FX
    series, carrying the last known rate over FX holidays. fx=None means the
    series is already in the base currency."""
    if fx is None:
        return dict(closes)
    rates = pd.Series(fx).sort_index()
    out = {}
    for day, c in closes.items():
        known = rates[rates.index <= day]
        if len(known):
            out[day] = c * float(known.iloc[-1])
    return out


def daily_returns(series: dict[str, dict[str, float]]) -> pd.DataFrame:
    """Simple daily returns on the dates EVERY series traded. Inner join on
    purpose: forward-filling an NSE holiday would invent a 0% day for that
    stock and understate its correlation with the rest."""
    frame = pd.DataFrame({s: pd.Series(c) for s, c in series.items() if c}).sort_index()
    frame = frame.dropna(how="any")
    return frame.pct_change().dropna(how="any")


# ---------------------------------------------------------------------------
# The measurements
# ---------------------------------------------------------------------------
def concentration(weights: dict[str, float]) -> dict:
    total = sum(weights.values()) or 1.0
    w = {s: v / total for s, v in weights.items()}
    hhi = sum(v * v for v in w.values())
    top = sorted(w.items(), key=lambda kv: -kv[1])[:3]
    return {
        "holdings": len(w),
        "effective_holdings": round(1 / hhi, 1) if hhi else 0.0,
        "top": [{"symbol": s, "weight": round(v, 4)} for s, v in top],
    }


def weighted_avg_correlation(corr: pd.DataFrame, weights: dict[str, float]) -> Optional[float]:
    syms = [s for s in corr.columns if s in weights]
    num = den = 0.0
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            pair = weights[a] * weights[b]
            num += pair * float(corr.loc[a, b])
            den += pair
    return round(num / den, 3) if den else None


def correlation_clusters(corr: pd.DataFrame, weights: dict[str, float], threshold: float = CLUSTER_CORR) -> list[dict]:
    """Groups of holdings linked by pairwise correlation >= threshold (single
    linkage). Each group is, in practice, one bet."""
    syms = list(corr.columns)
    parent = {s: s for s in syms}

    def find(s):
        while parent[s] != s:
            parent[s] = parent[parent[s]]
            s = parent[s]
        return s

    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            if float(corr.loc[a, b]) >= threshold:
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for s in syms:
        groups.setdefault(find(s), []).append(s)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        pairs = [float(corr.loc[a, b]) for i, a in enumerate(members) for b in members[i + 1:]]
        out.append({
            "symbols": sorted(members, key=lambda s: -weights.get(s, 0)),
            "weight": round(sum(weights.get(s, 0) for s in members), 4),
            "avg_correlation": round(sum(pairs) / len(pairs), 2),
        })
    return sorted(out, key=lambda g: -g["weight"])


def portfolio_series(returns: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Daily returns of the portfolio held at today's weights for the whole
    year — "how would what you own now have behaved", not your actual history
    (we don't know when you bought what)."""
    cols = [c for c in returns.columns if weights.get(c)]
    w = np.array([weights[c] for c in cols], dtype=float)
    w = w / w.sum()
    return returns[cols].dot(w)


def risk_return(r: pd.Series, rf: float) -> dict:
    n = len(r)
    growth = float((1 + r).prod())
    annual_return = growth ** (TRADING_DAYS / n) - 1 if n and growth > 0 else -1.0
    vol = float(r.std(ddof=1)) * math.sqrt(TRADING_DAYS) if n > 1 else 0.0
    return {
        "annual_return": round(annual_return, 4),
        "volatility": round(vol, 4),
        # Float noise leaves a flat series with ~1e-18 volatility, not 0.
        "sharpe": round((annual_return - rf) / vol, 2) if vol > 1e-9 else None,
        "max_drawdown": round(float(((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min()), 4) if n else 0.0,
    }


def market_exposure(port: pd.Series, bench: pd.Series) -> Optional[dict]:
    joined = pd.concat([port, bench], axis=1, join="inner").dropna()
    if len(joined) < MIN_COMMON_DAYS:
        return None
    p, b = joined.iloc[:, 0], joined.iloc[:, 1]
    var_b = float(b.var(ddof=1))
    if var_b <= 0:
        return None
    beta = float(p.cov(b)) / var_b
    corr = float(p.corr(b))
    return {"beta": round(beta, 2), "r_squared": round(corr * corr, 2), "correlation": round(corr, 2),
            "days": len(joined)}


def frontier(returns: pd.DataFrame, weights: dict[str, float], rf: float, points: int = 12) -> Optional[dict]:
    """The efficient frontier of the holdings the user already owns, from the
    past year's returns, and where the user's own mix sits against it.

    Uses annualised arithmetic means and the sample covariance for BOTH the
    curve and the user's point so the two are comparable. Long-only, no
    leverage. Only coordinates are returned — never the weights behind a point.
    """
    from pypfopt import EfficientFrontier

    cols = [c for c in returns.columns if weights.get(c)]
    if len(cols) < 2:
        return None
    mu = returns[cols].mean() * TRADING_DAYS
    cov = returns[cols].cov() * TRADING_DAYS
    w = np.array([weights[c] for c in cols], dtype=float)
    w = w / w.sum()
    you = {"volatility": round(float(math.sqrt(w @ cov.values @ w)), 4), "return": round(float(w @ mu.values), 4)}

    def solve(method: str, *args, **kw) -> Optional[dict]:
        try:
            ef = EfficientFrontier(mu, cov, weight_bounds=(0, 1))
            getattr(ef, method)(*args, **kw)
            ret, vol, _ = ef.portfolio_performance(risk_free_rate=rf)
            return {"volatility": round(float(vol), 4), "return": round(float(ret), 4)}
        except Exception:
            return None

    lowest = solve("min_volatility")
    if lowest is None:
        return None
    top = float(mu.max())
    curve = [lowest]
    if top > lowest["return"]:
        for target in np.linspace(lowest["return"], top, points)[1:]:
            p = solve("efficient_return", float(min(target, top - 1e-6)))
            if p:
                curve.append(p)
    same_risk = solve("efficient_risk", you["volatility"]) if you["volatility"] > lowest["volatility"] + 1e-6 else None
    same_return = solve("efficient_return", you["return"]) if lowest["return"] <= you["return"] < top else None
    best_ratio = solve("max_sharpe", risk_free_rate=rf) if top > rf else None
    return {
        "you": you,
        "curve": curve,
        "lowest_risk": lowest,
        "best_return_per_risk": best_ratio,
        "same_risk_best_return": same_risk,
        "same_return_lowest_risk": same_return,
        "return_gap_at_same_risk": round(same_risk["return"] - you["return"], 4) if same_risk else None,
        "risk_gap_at_same_return": round(you["volatility"] - same_return["volatility"], 4) if same_return else None,
    }


def bucket_mix(bucket_values: dict[str, float]) -> dict[str, float]:
    total = sum(bucket_values.values())
    return {k: round(v / total, 4) for k, v in bucket_values.items() if v > 0} if total else {}


def drift(actual: dict[str, float], targets: Optional[dict[str, float]]) -> list[dict]:
    """Per bucket: actual share vs the user's own target (both fractions)."""
    if not targets:
        return []
    rows = []
    for key in BUCKETS:
        a, t = actual.get(key, 0.0), targets.get(key, 0.0)
        if a == 0 and t == 0:
            continue
        rows.append({"bucket": key, "label": BUCKETS[key], "actual": round(a, 4), "target": round(t, 4),
                     "drift": round(a - t, 4), "flag": abs(a - t) >= DRIFT_BAND})
    return sorted(rows, key=lambda r: -abs(r["drift"]))


def liquidity(symbol: str, position_value: float, closes: dict, volumes: dict, fx_rate: float,
              currency: str, bucket: str) -> Optional[dict]:
    """Average daily traded value over the last 20 sessions, in base currency,
    and the position as a share of it. Crypto is skipped: Yahoo reports its
    volume in quote currency, and retail sizes are a rounding error there."""
    if bucket == "crypto":
        return None
    days = sorted(d for d in closes if volumes.get(d))[-LIQUIDITY_DAYS:]
    if len(days) < 5:
        return None
    adv = sum(closes[d] * volumes[d] for d in days) / len(days) * fx_rate
    if adv <= 0:
        return None
    share = position_value / adv
    thin = adv < THIN_DAILY_VALUE.get(currency, THIN_DAILY_VALUE["USD"])
    return {"symbol": symbol, "avg_daily_value": round(adv, 2), "position_share_of_day": round(share, 4),
            "thinly_traded": thin, "flag": thin or share >= POSITION_SHARE_OF_DAY}


def diversification_score(eff_holdings: float, avg_corr: Optional[float], top_sector_share: Optional[float],
                          classes: int) -> dict:
    """0-100, from four parts the screen also shows separately, so the number
    is never a black box."""
    breadth = min(1.0, max(0.0, (eff_holdings - 1) / 11))                     # ~12 equal holdings = full
    spread = 0.5 if avg_corr is None else min(1.0, max(0.0, (0.8 - avg_corr) / 0.6))   # 0.2 → full, 0.8 → none
    sector = 0.5 if top_sector_share is None else min(1.0, max(0.0, (0.7 - top_sector_share) / 0.45))
    mix = {0: 0.0, 1: 0.2, 2: 0.6}.get(classes, 1.0)
    score = round(100 * (0.30 * breadth + 0.35 * spread + 0.20 * sector + 0.15 * mix))
    label = "Well spread" if score >= 75 else "Moderately spread" if score >= 50 else "Concentrated"
    return {"score": score, "label": label, "parts": {
        "breadth": round(breadth, 2), "independence": round(spread, 2),
        "sectors": round(sector, 2), "asset_classes": round(mix, 2)}}


# ---------------------------------------------------------------------------
# Plain-language observations
# ---------------------------------------------------------------------------
def _pct(x: float, digits: int = 0) -> str:
    return f"{x * 100:.{digits}f}%"


def observations(report: dict) -> list[str]:
    """What the numbers say, in sentences. Descriptive only — no "should",
    no buy/sell/add/trim (tests hold this)."""
    out = []
    c = report["concentration"]
    if c["holdings"] >= 2 and c["top"]:
        out.append(f"You hold {c['holdings']} positions, but by size they behave like about "
                   f"{c['effective_holdings']:.0f} equal ones. {c['top'][0]['symbol']} alone is "
                   f"{_pct(c['top'][0]['weight'])} of the invested amount.")
    for g in report.get("clusters", [])[:2]:
        out.append(f"{', '.join(g['symbols'][:4])}{' and others' if len(g['symbols']) > 4 else ''} moved together "
                   f"over the past year (correlation {g['avg_correlation']:.2f}) and make up {_pct(g['weight'])} — "
                   "in practice, close to one bet.")
    s = report.get("sectors") or []
    if s and s[0]["weight"] >= 0.4 and s[0]["sector"] != "Unknown":
        out.append(f"{_pct(s[0]['weight'])} of the invested amount is in one sector: {s[0]['sector']}.")
    m, bench = report.get("market"), report.get("benchmark", {}).get("name")
    if m and bench:
        out.append(f"About {_pct(m['r_squared'])} of your portfolio's daily ups and downs matched the {bench}. "
                   f"Beta {m['beta']:.2f}: on a day the {bench} moved 1%, your portfolio moved about "
                   f"{abs(m['beta']):.1f}% on average.")
        if m["r_squared"] >= 0.8:
            out.append(f"Most of your result has come from the market itself rather than from which stocks you picked — "
                       f"an index fund on the {bench} would have behaved much the same.")
    you, b = report.get("risk_return"), report.get("benchmark_risk_return")
    if you and you.get("sharpe") is not None:
        line = f"Return per unit of risk (Sharpe ratio) over the past year: {you['sharpe']:.2f}"
        if b and b.get("sharpe") is not None and bench:
            line += f", against {b['sharpe']:.2f} for the {bench}"
        out.append(line + ".")
    f = report.get("frontier")
    if f and f.get("return_gap_at_same_risk") is not None and f["return_gap_at_same_risk"] >= 0.02:
        out.append(f"Looking back, other mixes of these same holdings reached the same risk with about "
                   f"{_pct(f['return_gap_at_same_risk'])} more yearly return. That is hindsight, not a forecast.")
    elif f and f.get("return_gap_at_same_risk") is not None:
        out.append("Your mix sat close to the efficient frontier of your own holdings over the past year.")
    for d in [r for r in report.get("drift", []) if r["flag"]][:3]:
        word = "above" if d["drift"] > 0 else "below"
        out.append(f"{d['label']} is {_pct(d['actual'])} of the portfolio — {abs(d['drift']) * 100:.0f} points "
                   f"{word} your {_pct(d['target'])} target.")
    for q in [x for x in report.get("liquidity", []) if x["flag"]][:3]:
        if q["position_share_of_day"] >= POSITION_SHARE_OF_DAY:
            out.append(f"Your {q['symbol']} position is about {_pct(q['position_share_of_day'])} of a normal day's "
                       "trading in it — exiting all at once could move the price.")
        else:
            out.append(f"{q['symbol']} is thinly traded, so prices can jump and orders can fill slowly.")
    return out


# ---------------------------------------------------------------------------
# The whole report
# ---------------------------------------------------------------------------
def parse_targets(raw: Optional[dict]) -> Optional[dict[str, float]]:
    """The user's own target mix, in percent per bucket, as fractions. Raises
    ValueError with a user-facing message when it can't be used."""
    if not raw:
        return None
    unknown = [k for k in raw if k not in BUCKETS]
    if unknown:
        raise ValueError(f"Unknown target group: {unknown[0]}")
    vals = {}
    for k, v in raw.items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ValueError("Targets must be numbers")
        if not math.isfinite(f) or f < 0 or f > 100:
            raise ValueError("Each target must be between 0 and 100")
        vals[k] = f
    total = sum(vals.values())
    if total == 0:
        return None
    if abs(total - 100) > 1:
        raise ValueError(f"Targets add up to {total:g}% — they need to add up to 100%")
    return {k: v / total for k, v in vals.items()}


def _unit(currency: Optional[str]) -> tuple[Optional[str], float]:
    """Yahoo prices London listings in pence ("GBp"); everything else is in
    whole units."""
    if currency == "GBp":
        return "GBP", 0.01
    return (currency.upper() if currency else None), 1.0


def build_report(holdings: list[dict], cash: float, targets: Optional[dict[str, float]],
                 histories: dict[str, Optional[dict]], sectors: dict[str, Optional[str]],
                 fx: dict[str, Optional[dict]], benchmark: Optional[dict], base: str) -> dict:
    """holdings: [{symbol, quantity}]. histories: symbol -> fetch_dated_history
    output (None if it failed). fx: currency -> dated rates into `base`.
    benchmark: dated history of the base currency's index."""
    skipped, values, series, meta = [], {}, {}, {}
    for h in holdings:
        sym, hist = h["symbol"], histories.get(h["symbol"])
        if not hist or not hist.get("price") or not hist.get("closes"):
            skipped.append({"symbol": sym, "reason": "No price history"})
            continue
        cur, unit = _unit(hist.get("currency"))
        rates = None if cur == base else fx.get(cur)
        if cur != base and not rates:
            skipped.append({"symbol": sym, "reason": f"No {cur}→{base} exchange rate"})
            continue
        fx_now = 1.0 if rates is None else float(pd.Series(rates).sort_index().iloc[-1])
        values[sym] = float(h["quantity"]) * float(hist["price"]) * unit * fx_now
        series[sym] = to_base({d: c * unit for d, c in hist["closes"].items()}, rates)
        bucket = asset_bucket(sym, hist.get("quote_type"), cur, hist.get("name"))
        meta[sym] = {"name": hist.get("name"), "currency": cur, "bucket": bucket, "fx": fx_now * unit,
                     "sector": sectors.get(sym) if bucket in ("in_equity", "us_equity", "other_equity") else BUCKETS[bucket]}

    invested = sum(v for v in values.values() if v > 0)
    weights = {s: v / invested for s, v in values.items() if v > 0} if invested else {}

    bucket_values: dict[str, float] = {}
    for s, v in values.items():
        bucket_values[meta[s]["bucket"]] = bucket_values.get(meta[s]["bucket"], 0.0) + v
    if cash > 0:
        bucket_values["cash"] = cash
    mix = bucket_mix(bucket_values)

    sector_w: dict[str, float] = {}
    for s, w in weights.items():
        key = meta[s]["sector"] or "Unknown"
        sector_w[key] = sector_w.get(key, 0.0) + w
    sector_rows = [{"sector": k, "weight": round(v, 4)} for k, v in sorted(sector_w.items(), key=lambda kv: -kv[1])]

    returns = daily_returns({s: series[s] for s in weights})
    enough = len(returns) >= MIN_COMMON_DAYS and returns.shape[1] >= 1
    corr = returns.corr() if enough and returns.shape[1] >= 2 else None
    rf = risk_free_rate(base)
    port = portfolio_series(returns, weights) if enough else None

    bench_name = BENCHMARKS.get(base, BENCHMARKS["USD"])[1]
    bench_ret = None
    if benchmark and benchmark.get("closes"):
        bench_ret = pd.Series(benchmark["closes"]).sort_index().pct_change().dropna()
    market = market_exposure(port, bench_ret) if port is not None and bench_ret is not None else None
    bench_rr = None
    if port is not None and bench_ret is not None:
        aligned = bench_ret[bench_ret.index.isin(port.index)]
        if len(aligned) >= MIN_COMMON_DAYS:
            bench_rr = risk_return(aligned, rf)

    conc = concentration(weights)
    avg_corr = weighted_avg_correlation(corr, weights) if corr is not None else None
    liq = []
    for s in weights:
        hist = histories[s]
        unit = _unit(hist.get("currency"))[1]
        row = liquidity(s, values[s], {d: c * unit for d, c in hist["closes"].items()}, hist.get("volumes") or {},
                        meta[s]["fx"] / unit if unit else 1.0, base, meta[s]["bucket"])
        if row:
            liq.append(row)
    classes = sum(1 for k, v in bucket_mix({k: v for k, v in bucket_values.items() if k != "cash"}).items() if v >= 0.05)

    report = {
        "base_currency": base,
        "invested_value": round(invested, 2),
        "cash": round(max(cash, 0.0), 2),
        "holdings": [{"symbol": s, "name": meta[s]["name"], "weight": round(w, 4), "value": round(values[s], 2),
                      "bucket": meta[s]["bucket"], "sector": meta[s]["sector"] or "Unknown"}
                     for s, w in sorted(weights.items(), key=lambda kv: -kv[1])],
        "skipped": skipped,
        "days_analysed": int(len(returns)) if enough else 0,
        "concentration": conc,
        "avg_correlation": avg_corr,
        "clusters": correlation_clusters(corr, weights) if corr is not None else [],
        "correlation": ({"symbols": list(corr.columns), "matrix": [[round(float(x), 2) for x in row] for row in corr.values]}
                        if corr is not None and corr.shape[0] <= 15 else None),
        "sectors": sector_rows,
        "asset_mix": [{"bucket": k, "label": BUCKETS[k], "weight": v} for k, v in sorted(mix.items(), key=lambda kv: -kv[1])],
        "drift": drift(mix, targets),
        "targets_set": bool(targets),
        "benchmark": {"symbol": BENCHMARKS.get(base, BENCHMARKS["USD"])[0], "name": bench_name},
        "market": market,
        "risk_free_rate": rf,
        "risk_return": risk_return(port, rf) if port is not None else None,
        "benchmark_risk_return": bench_rr,
        "frontier": frontier(returns, weights, rf) if enough and returns.shape[1] >= 2 else None,
        "liquidity": sorted(liq, key=lambda r: (-int(r["flag"]), -r["position_share_of_day"])),
    }
    report["diversification"] = diversification_score(
        conc["effective_holdings"], avg_corr, sector_rows[0]["weight"] if sector_rows else None, classes)
    report["observations"] = observations(report)
    if not enough and weights:
        report["observations"].insert(0, "There isn't enough shared price history yet (under "
                                         f"{MIN_COMMON_DAYS} common trading days) for risk and market figures.")
    return report
