from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone

import requests
from emergentintegrations.llm.chat import LlmChat, UserMessage

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

EMERGENT_LLM_KEY = os.environ.get('EMERGENT_LLM_KEY')
MODEL_PROVIDER = "openai"
MODEL_NAME = "gpt-5.4"

app = FastAPI()
api_router = APIRouter(prefix="/api")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("tradingagents")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------------
# Market data (Yahoo Finance public endpoints — no key required)
# ----------------------------------------------------------------------------
YF_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

MARKET_CATEGORIES = {
    "trending": ["AAPL", "NVDA", "TSLA", "BTC-USD", "ETH-USD", "GC=F", "CL=F", "RELIANCE.NS"],
    "stocks": ["AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "GOOGL", "META", "JPM"],
    "crypto": ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "BNB-USD", "ADA-USD", "LTC-USD"],
    "commodities": ["GC=F", "SI=F", "CL=F", "BZ=F", "NG=F", "HG=F", "PL=F", "ZW=F"],
}
COMMODITY_NAMES = {
    "GC=F": "Gold", "SI=F": "Silver", "CL=F": "Crude Oil (WTI)", "BZ=F": "Brent Crude",
    "NG=F": "Natural Gas", "HG=F": "Copper", "PL=F": "Platinum", "ZW=F": "Wheat",
}
_market_cache: dict = {}


def _yf_get(url: str, params: dict):
    return requests.get(url, params=params, headers=YF_HEADERS, timeout=15)


def fetch_quote_sync(symbol: str) -> dict:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = _yf_get(url, {"range": "1mo", "interval": "1d"})
    r.raise_for_status()
    data = r.json()
    result = data["chart"]["result"][0]
    meta = result.get("meta", {})
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")

    closes = []
    try:
        closes = result["indicators"]["quote"][0].get("close", []) or []
    except Exception:
        closes = []
    spark = [round(float(c), 4) for c in closes if c is not None]

    change = (price - prev) if (price is not None and prev is not None) else None
    change_pct = (change / prev * 100) if (change is not None and prev) else None

    return {
        "symbol": meta.get("symbol", symbol),
        "name": meta.get("longName") or meta.get("shortName") or symbol,
        "price": round(float(price), 2) if price is not None else None,
        "previousClose": round(float(prev), 2) if prev is not None else None,
        "change": round(float(change), 2) if change is not None else None,
        "changePercent": round(float(change_pct), 2) if change_pct is not None else None,
        "currency": meta.get("currency"),
        "exchange": meta.get("fullExchangeName"),
        "dayHigh": meta.get("regularMarketDayHigh"),
        "dayLow": meta.get("regularMarketDayLow"),
        "fiftyTwoWeekHigh": meta.get("fiftyTwoWeekHigh"),
        "fiftyTwoWeekLow": meta.get("fiftyTwoWeekLow"),
        "sparkline": spark[-30:],
    }


RANGE_MAP = {
    "1D": ("1d", "5m"),
    "1W": ("5d", "30m"),
    "1M": ("1mo", "1d"),
    "1Y": ("1y", "1wk"),
}


def fetch_chart_sync(symbol: str, rng: str) -> dict:
    range_, interval = RANGE_MAP.get(rng, ("1mo", "1d"))
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = _yf_get(url, {"range": range_, "interval": interval})
    r.raise_for_status()
    result = r.json()["chart"]["result"][0]
    try:
        closes = result["indicators"]["quote"][0].get("close", []) or []
    except Exception:
        closes = []
    points = [round(float(c), 4) for c in closes if c is not None]
    meta = result.get("meta", {})
    base = meta.get("chartPreviousClose") or meta.get("previousClose")
    if rng != "1D" and len(points) >= 1:
        base = points[0]
    last = points[-1] if points else meta.get("regularMarketPrice")
    change = (last - base) if (last is not None and base is not None) else None
    change_pct = (change / base * 100) if (change is not None and base) else None
    return {
        "symbol": meta.get("symbol", symbol),
        "range": rng,
        "points": points[-120:],
        "last": round(float(last), 2) if last is not None else None,
        "change": round(float(change), 2) if change is not None else None,
        "changePercent": round(float(change_pct), 2) if change_pct is not None else None,
        "currency": meta.get("currency"),
    }


def search_sync(q: str) -> list:
    url = "https://query1.finance.yahoo.com/v1/finance/search"
    r = _yf_get(url, {"q": q, "quotesCount": 10, "newsCount": 0})
    r.raise_for_status()
    data = r.json()
    out = []
    for it in data.get("quotes", []):
        sym = it.get("symbol")
        if not sym:
            continue
        out.append({
            "symbol": sym,
            "name": it.get("longname") or it.get("shortname") or sym,
            "exchange": it.get("exchDisp"),
            "type": it.get("quoteType"),
        })
    return out


def fetch_news_sync(symbol: str) -> list:
    s = (symbol or "").upper().strip()
    query = _news_query(s)
    url = "https://query1.finance.yahoo.com/v1/finance/search"
    r = _yf_get(url, {"q": query, "quotesCount": 0, "newsCount": 40})
    r.raise_for_status()
    data = r.json()

    def norm(item):
        title = item.get("title")
        link = item.get("link")
        if not title or not link:
            return None
        thumb = None
        try:
            res = (item.get("thumbnail") or {}).get("resolutions") or []
            if res:
                thumb = res[0].get("url")
        except Exception:
            thumb = None
        return {
            "title": title,
            "publisher": item.get("publisher"),
            "link": link,
            "published": item.get("providerPublishTime"),
            "thumbnail": thumb,
        }

    # Keep only stories that Yahoo tags with THIS exact ticker — this is what
    # makes the feed about the researched asset instead of generic filler.
    relevant = []
    for it in data.get("news", []):
        related = {str(t).upper() for t in (it.get("relatedTickers") or [])}
        if s in related:
            n = norm(it)
            if n:
                relevant.append(n)
    return relevant[:12]


CRYPTO_NEWS_NAMES = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "XRP",
    "DOGE": "Dogecoin", "BNB": "BNB", "ADA": "Cardano", "LTC": "Litecoin",
}
INDEX_NEWS_NAMES = {
    "^GSPC": "S&P 500", "^DJI": "Dow Jones", "^IXIC": "Nasdaq", "^NSEI": "Nifty 50",
    "^BSESN": "Sensex", "^FTSE": "FTSE 100", "^N225": "Nikkei 225", "^HSI": "Hang Seng",
}


def _news_query(symbol: str) -> str:
    """Best Yahoo search term for a symbol so the returned news pool actually
    references it (Yahoo indexes news by company/asset name, not raw ticker)."""
    s = symbol.upper().strip()
    if s in COMMODITY_NAMES:
        return COMMODITY_NAMES[s]
    if s in INDEX_NEWS_NAMES:
        return INDEX_NEWS_NAMES[s]
    if s.endswith("-USD") or s.endswith("-USDT"):
        base = s.split("-")[0]
        return CRYPTO_NEWS_NAMES.get(base, base)
    m = re.match(r"^(.+)\.[A-Z]{1,3}$", s)  # strip exchange suffix: RELIANCE.NS -> RELIANCE
    return m.group(1) if m else s


# ----------------------------------------------------------------------------
# Agent personas
# ----------------------------------------------------------------------------
STYLE = (
    " Respond in under 120 words. Be sharp and specific; reference the data provided. "
    "Use 3-5 short bullet points prefixed with '- '. No preamble, no disclaimers. "
    "End with a final line formatted exactly as 'SIGNAL: BULLISH' or 'SIGNAL: BEARISH' or 'SIGNAL: NEUTRAL'."
)

TECH_SYS = "You are a veteran Technical Analyst at a hedge fund. You read price action, trend, momentum (MACD/RSI), support/resistance and volume." + STYLE
FUND_SYS = "You are a Fundamentals Analyst. You judge valuation, growth, margins, balance sheet strength and competitive moat." + STYLE
SENT_SYS = "You are a Sentiment Analyst. You gauge crowd mood from social chatter, retail flow and options positioning for short-term bias." + STYLE
NEWS_SYS = "You are a Macro & News Analyst. You weigh recent headlines, catalysts, sector rotation and macro conditions." + STYLE
BULL_SYS = "You are the Bull Researcher. You build the strongest possible case to BUY, using the analyst reports. Be persuasive but grounded; rebut the bear directly when given." + STYLE
BEAR_SYS = "You are the Bear Researcher. You build the strongest possible case to SELL/AVOID, using the analyst reports. Be persuasive but grounded; rebut the bull directly when given." + STYLE
RM_SYS = "You are the Research Manager judging the bull vs bear debate. Declare which side won and the recommended stance. Be decisive." + STYLE
TRADER_SYS = "You are the Trader. Turn the research into a concrete plan: action (buy/sell/hold), entry zone, target, stop-loss and position sizing rationale." + STYLE
RISK_SYS = "You are the Risk Manager. Stress-test the trade for volatility, liquidity, downside and sizing. Approve, adjust or reject with reasoning." + STYLE
PM_SYS = (
    "You are the Portfolio Manager making the FINAL call after reviewing the entire desk. "
    "Output ONLY a raw JSON object (no markdown fences, no prose) with EXACTLY these keys: "
    '{"decision": "BUY|SELL|HOLD", "confidence": <integer 0-100>, '
    '"target_price": <number or null>, "stop_loss": <number or null>, '
    '"time_horizon": "<short string e.g. 3-6 months>", '
    '"summary": "<2-3 sentence rationale>", "key_risks": ["<risk>", "<risk>", "<risk>"]}'
)

DEBATE_SYS = (
    "You run a concise 3-analyst round table on a single asset. Using the desk's analysis, write SHORT, punchy arguments. "
    "Output ONLY a raw JSON object (no markdown, no prose) with EXACTLY these keys: "
    '{"bull": "<=45 word bullish argument>", "bear": "<=45 word bearish argument>", '
    '"fundamentals": "<=45 word valuation/financials argument>", '
    '"agreements": ["<short point>", "<short point>"], '
    '"disagreements": ["<short point>", "<short point>"], '
    '"recommendation": "<=40 word final call consistent with the verdict>"}'
)

TOTAL_STEPS = 12


async def safe_agent(system_message: str, user_text: str, fallback: str = "Analysis unavailable.") -> str:
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=str(uuid.uuid4()),
            system_message=system_message,
        ).with_model(MODEL_PROVIDER, MODEL_NAME)
        resp = await asyncio.wait_for(chat.send_message(UserMessage(text=user_text)), timeout=120)
        text = (resp or "").strip()
        return text or fallback
    except Exception as e:
        logger.warning(f"agent call failed: {e}")
        return fallback


def parse_signal(text: str):
    m = re.search(r'SIGNAL:\s*(BULLISH|BEARISH|NEUTRAL)', text, re.IGNORECASE)
    return m.group(1).lower() if m else None


def split_signal(text: str):
    sig = parse_signal(text)
    cleaned = re.sub(r'\n?\s*SIGNAL:\s*(BULLISH|BEARISH|NEUTRAL)\s*$', '', text, flags=re.IGNORECASE).strip()
    return cleaned, sig


def extract_json(text: str):
    if not text:
        return None
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        return None
    blob = m.group(0)
    for candidate in (blob, re.sub(r',\s*([}\]])', r'\1', blob)):
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def parse_verdict(text: str) -> dict:
    data = extract_json(text) or {}
    decision = str(data.get("decision", "")).upper().strip()
    if decision not in ("BUY", "SELL", "HOLD"):
        t = (text or "").upper()
        if "SELL" in t:
            decision = "SELL"
        elif "BUY" in t:
            decision = "BUY"
        else:
            decision = "HOLD"
    try:
        confidence = int(round(float(data.get("confidence", 55))))
    except Exception:
        confidence = 55
    confidence = max(0, min(100, confidence))

    def num(v):
        try:
            return round(float(v), 2)
        except Exception:
            return None

    risks = data.get("key_risks") or []
    if not isinstance(risks, list):
        risks = [str(risks)]
    return {
        "decision": decision,
        "confidence": confidence,
        "target_price": num(data.get("target_price")),
        "stop_loss": num(data.get("stop_loss")),
        "time_horizon": str(data.get("time_horizon")) if data.get("time_horizon") else "N/A",
        "summary": str(data.get("summary")) if data.get("summary") else "The committee reached a decision from the multi-agent analysis.",
        "key_risks": [str(x) for x in risks][:4],
    }


# --- Verdict grounding gate (additive; verdict itself is never modified) ---
def ground_verdict(verdict: dict, quote: Optional[dict]) -> dict:
    """Check the agents' price levels against observed market evidence.

    Never changes the verdict. Returns a grounding report the UI can show
    and a future order-placement step can gate on:
      status: grounded | warning | failed | unverified
    """
    checks: list = []

    def add(cid: str, ok: bool, msg: str, severity: str = "fail"):
        checks.append({"id": cid, "ok": ok, "severity": "info" if ok else severity, "message": msg})

    if not quote or quote.get("price") in (None, 0):
        return {
            "status": "unverified",
            "checks": [{"id": "evidence", "ok": False, "severity": "warn", "message": "No live quote was available, so price levels could not be verified."}],
            "evidence": None,
        }

    price = float(quote["price"])
    lo52 = quote.get("fiftyTwoWeekLow")
    hi52 = quote.get("fiftyTwoWeekHigh")
    evidence = {
        "price": price,
        "dayLow": quote.get("dayLow"),
        "dayHigh": quote.get("dayHigh"),
        "fiftyTwoWeekLow": lo52,
        "fiftyTwoWeekHigh": hi52,
        "currency": quote.get("currency"),
        "asOf": now_iso(),
    }

    decision = verdict.get("decision", "HOLD")
    target = verdict.get("target_price")
    stop = verdict.get("stop_loss")

    def num(v):
        try:
            f = float(v)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    t, sl = num(target), num(stop)

    # 1. Levels present and positive (a HOLD may legitimately carry no levels -> warn only)
    missing_sev = "warn" if decision == "HOLD" else "fail"
    add("target_present", t is not None, "Target price is missing or not a positive number." if t is None else "Target price present.", severity=missing_sev)
    add("stop_present", sl is not None, "Stop loss is missing or not a positive number." if sl is None else "Stop loss present.", severity=missing_sev)

    # 2. Direction consistency with the decision
    if decision == "BUY":
        if t is not None:
            add("target_direction", t > price, f"BUY target {t:g} is not above the live price {price:g}." if t <= price else "Target sits above the live price.")
        if sl is not None:
            add("stop_direction", sl < price, f"BUY stop {sl:g} is not below the live price {price:g}." if sl >= price else "Stop sits below the live price.")
    elif decision == "SELL":
        if t is not None:
            add("target_direction", t < price, f"SELL target {t:g} is not below the live price {price:g}." if t >= price else "Target sits below the live price.")
        if sl is not None:
            add("stop_direction", sl > price, f"SELL stop {sl:g} is not above the live price {price:g}." if sl <= price else "Stop sits above the live price.")

    # 3. Magnitude plausibility vs live price (warn, not fail)
    for cid, lvl, name in (("target_magnitude", t, "Target"), ("stop_magnitude", sl, "Stop")):
        if lvl is None:
            continue
        ratio = lvl / price
        ok = 0.5 <= ratio <= 2.0
        add(cid, ok, f"{name} {lvl:g} is {ratio:.2f}x the live price — outside a plausible range." if not ok else f"{name} is within 0.5x–2x of the live price.", severity="warn")

    # 4. Against 52-week range (warn): beyond 25% outside the observed year
    if lo52 and hi52:
        try:
            lo, hi = float(lo52), float(hi52)
            band_lo, band_hi = lo * 0.75, hi * 1.25
            for cid, lvl, name in (("target_52w", t, "Target"), ("stop_52w", sl, "Stop")):
                if lvl is None:
                    continue
                ok = band_lo <= lvl <= band_hi
                add(cid, ok, f"{name} {lvl:g} is far outside the 52-week range {lo:g}–{hi:g}." if not ok else f"{name} is within reach of the 52-week range.", severity="warn")
        except (TypeError, ValueError):
            pass

    # 5. Risk/reward (warn)
    if t is not None and sl is not None and decision in ("BUY", "SELL"):
        reward = abs(t - price)
        risk = abs(price - sl)
        if risk > 0:
            rr = reward / risk
            add("risk_reward", rr >= 1.0, f"Risk/reward is {rr:.2f} — less reward than risk." if rr < 1.0 else f"Risk/reward is {rr:.2f}.", severity="warn")

    if any((not c["ok"]) and c["severity"] == "fail" for c in checks):
        status = "failed"
    elif any(not c["ok"] for c in checks):
        status = "warning"
    else:
        status = "grounded"
    return {"status": status, "checks": checks, "evidence": evidence}


def parse_debate(text: str):
    d = extract_json(text) or {}

    def s(k: str) -> str:
        v = d.get(k)
        return str(v).strip() if v else ""

    def lst(k: str) -> list:
        v = d.get(k) or []
        if not isinstance(v, list):
            v = [str(v)]
        return [str(x).strip() for x in v if str(x).strip()][:4]

    bull, bear, fund = s("bull"), s("bear"), s("fundamentals")
    if not (bull and bear and fund):
        return None
    return {
        "bull": bull,
        "bear": bear,
        "fundamentals": fund,
        "agreements": lst("agreements"),
        "disagreements": lst("disagreements"),
        "recommendation": s("recommendation") or "See the desk verdict above.",
    }


def build_context(symbol: str, quote: Optional[dict]) -> str:
    if symbol.endswith("=F"):
        asset_class = "Commodity / futures contract"
    elif symbol.endswith("-USD") or symbol.endswith("=X"):
        asset_class = "Cryptocurrency / FX"
    else:
        asset_class = "Equity / stock"
    lines = [f"TICKER: {symbol}", f"ASSET CLASS: {asset_class}", f"ANALYSIS DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"]
    if quote and quote.get("price") is not None:
        lines.append(f"Name: {quote.get('name')}")
        lines.append(f"Current Price: {quote.get('price')} {quote.get('currency') or ''}")
        if quote.get("changePercent") is not None:
            lines.append(f"1-Day Change: {quote.get('changePercent')}%")
        if quote.get("fiftyTwoWeekLow") and quote.get("fiftyTwoWeekHigh"):
            lines.append(f"52-Week Range: {quote.get('fiftyTwoWeekLow')} - {quote.get('fiftyTwoWeekHigh')}")
        spark = quote.get("sparkline") or []
        if len(spark) >= 2:
            trend = "UP" if spark[-1] >= spark[0] else "DOWN"
            lines.append(f"~1-Month Trend: {trend} (from {spark[0]} to {spark[-1]})")
        if quote.get("exchange"):
            lines.append(f"Exchange: {quote.get('exchange')}")
    else:
        lines.append("Live price data is unavailable — reason qualitatively from known fundamentals and general market knowledge.")
    return "\n".join(lines)


def make_message(agent: str, tag: str, phase: str, content: str, sentiment=None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "agent": agent,
        "tag": tag,
        "phase": phase,
        "content": content,
        "sentiment": sentiment,
        "ts": now_iso(),
    }


async def append_message(analysis_id: str, message: dict, step: int):
    await db.analyses.update_one(
        {"id": analysis_id},
        {
            "$push": {"messages": message},
            "$set": {"current_step": step, "updated_at": now_iso()},
        },
    )


# ----------------------------------------------------------------------------
# The multi-agent pipeline
# ----------------------------------------------------------------------------
async def run_analysis(analysis_id: str, symbol: str):
    try:
        quote = None
        try:
            quote = await asyncio.to_thread(fetch_quote_sync, symbol)
            await db.analyses.update_one(
                {"id": analysis_id},
                {"$set": {"quote": quote, "name": quote.get("name") or symbol, "updated_at": now_iso()}},
            )
        except Exception as e:
            logger.warning(f"quote fetch failed for {symbol}: {e}")

        ctx = build_context(symbol, quote)
        step = 0

        # PHASE 1 — Analyst team (concurrent)
        analyst_specs = [
            ("Technical Analyst", "TECHNICAL_ANALYST", TECH_SYS),
            ("Fundamentals Analyst", "FUNDAMENTALS_ANALYST", FUND_SYS),
            ("Sentiment Analyst", "SENTIMENT_ANALYST", SENT_SYS),
            ("News Analyst", "NEWS_ANALYST", NEWS_SYS),
        ]
        analyst_tasks = [
            safe_agent(sysmsg, f"{ctx}\n\nProvide your {name} briefing for {symbol}.")
            for (name, _tag, sysmsg) in analyst_specs
        ]
        analyst_results = await asyncio.gather(*analyst_tasks)

        transcript_parts = []
        fund_content = ""
        for (name, tag, _s), raw in zip(analyst_specs, analyst_results):
            content, sig = split_signal(raw)
            if tag == "FUNDAMENTALS_ANALYST":
                fund_content = content
            step += 1
            await append_message(analysis_id, make_message(name, tag, "analysis", content, sig), step)
            transcript_parts.append(f"### {name}\n{content}")
        analyst_summary = "\n\n".join(transcript_parts)

        # PHASE 2 — Bull vs Bear debate (2 rounds)
        debate_log = []
        bull_prev = ""
        bear_prev = ""
        for rnd in (1, 2):
            bull_user = f"{ctx}\n\nANALYST REPORTS:\n{analyst_summary}\n\n"
            if bear_prev:
                bull_user += f"The Bear just argued:\n{bear_prev}\n\nRebut the bear and "
            bull_user += f"make the BULLISH case for {symbol} (round {rnd})."
            bull_raw = await safe_agent(BULL_SYS, bull_user)
            bull_content, _ = split_signal(bull_raw)
            step += 1
            await append_message(analysis_id, make_message("Bull Researcher", "BULL_RESEARCHER", "debate", bull_content, "bullish"), step)
            bull_prev = bull_content
            debate_log.append(f"BULL (r{rnd}): {bull_content}")

            bear_user = (
                f"{ctx}\n\nANALYST REPORTS:\n{analyst_summary}\n\n"
                f"The Bull just argued:\n{bull_prev}\n\nRebut the bull and make the BEARISH case for {symbol} (round {rnd})."
            )
            bear_raw = await safe_agent(BEAR_SYS, bear_user)
            bear_content, _ = split_signal(bear_raw)
            step += 1
            await append_message(analysis_id, make_message("Bear Researcher", "BEAR_RESEARCHER", "debate", bear_content, "bearish"), step)
            bear_prev = bear_content
            debate_log.append(f"BEAR (r{rnd}): {bear_content}")
        debate_text = "\n\n".join(debate_log)

        # PHASE 3 — Research Manager
        rm_raw = await safe_agent(
            RM_SYS,
            f"{ctx}\n\nANALYST REPORTS:\n{analyst_summary}\n\nDEBATE:\n{debate_text}\n\nJudge the debate and give the recommended stance for {symbol}.",
        )
        rm_content, rm_sig = split_signal(rm_raw)
        step += 1
        await append_message(analysis_id, make_message("Research Manager", "RESEARCH_MANAGER", "debate", rm_content, rm_sig), step)

        # PHASE 4 — Trader
        tr_raw = await safe_agent(
            TRADER_SYS,
            f"{ctx}\n\nANALYST REPORTS:\n{analyst_summary}\n\nDEBATE VERDICT:\n{rm_content}\n\nPropose a concrete trade plan for {symbol}.",
        )
        tr_content, tr_sig = split_signal(tr_raw)
        step += 1
        await append_message(analysis_id, make_message("Trader", "TRADER", "trade", tr_content, tr_sig), step)

        # PHASE 5 — Risk Manager
        rk_raw = await safe_agent(
            RISK_SYS,
            f"{ctx}\n\nTRADE PLAN:\n{tr_content}\n\nDEBATE VERDICT:\n{rm_content}\n\nStress-test the trade and give your risk ruling for {symbol}.",
        )
        rk_content, rk_sig = split_signal(rk_raw)
        step += 1
        await append_message(analysis_id, make_message("Risk Manager", "RISK_MANAGER", "risk", rk_content, rk_sig), step)

        # PHASE 6 — Portfolio Manager (final structured verdict)
        full_transcript = (
            f"{analyst_summary}\n\nDEBATE:\n{debate_text}\n\nRESEARCH MANAGER:\n{rm_content}"
            f"\n\nTRADER:\n{tr_content}\n\nRISK MANAGER:\n{rk_content}"
        )
        pm_raw = await safe_agent(
            PM_SYS,
            f"{ctx}\n\nFULL DESK TRANSCRIPT:\n{full_transcript}\n\nMake the FINAL decision for {symbol}. Output ONLY the JSON object.",
            fallback="{}",
        )
        verdict = parse_verdict(pm_raw)
        step += 1
        sentiment = {"BUY": "bullish", "SELL": "bearish", "HOLD": "neutral"}.get(verdict["decision"], "neutral")
        decision_msg = f"FINAL VERDICT: {verdict['decision']} — CONFIDENCE {verdict['confidence']}%\n\n{verdict['summary']}"
        await append_message(analysis_id, make_message("Portfolio Manager", "PORTFOLIO_MANAGER", "decision", decision_msg, sentiment), step)

        # PHASE 7 — Round-table debate (Bull vs Bear vs Fundamentals) + synthesis
        debate_raw = await safe_agent(
            DEBATE_SYS,
            f"{ctx}\n\nDESK TRANSCRIPT:\n{full_transcript}\n\n"
            f"FINAL VERDICT: {verdict['decision']} ({verdict['confidence']}%). {verdict['summary']}\n\n"
            f"Produce the round-table JSON for {symbol}.",
            fallback="{}",
        )
        debate = parse_debate(debate_raw)
        if not debate:
            debate = {
                "bull": bull_prev or "Upside catalysts and momentum support taking a long position here.",
                "bear": bear_prev or "Weak momentum and downside risks argue for caution or an exit.",
                "fundamentals": fund_content or "Valuation is the swing factor and looks fairly balanced at current levels.",
                "agreements": ["Volatility is elevated", "The current setup is not clean"],
                "disagreements": ["The direction of the next major move", "Whether the current valuation is justified"],
                "recommendation": verdict["summary"],
            }

        grounding = ground_verdict(verdict, quote)
        await db.analyses.update_one(
            {"id": analysis_id},
            {"$set": {"status": "completed", "verdict": verdict, "debate": debate, "grounding": grounding, "current_step": TOTAL_STEPS, "updated_at": now_iso()}},
        )
        logger.info(f"analysis {analysis_id} for {symbol} completed: {verdict['decision']}")
    except Exception as e:
        logger.exception("analysis pipeline failed")
        await db.analyses.update_one(
            {"id": analysis_id},
            {"$set": {"status": "error", "error": str(e), "updated_at": now_iso()}},
        )


# ----------------------------------------------------------------------------
# Schemas
# ----------------------------------------------------------------------------
class AnalyzeRequest(BaseModel):
    symbol: str
    name: Optional[str] = None


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------
@api_router.get("/")
async def root():
    return {"service": "TradingAgents", "status": "ok"}


@api_router.get("/search")
async def search(q: str):
    q = (q or "").strip()
    if not q:
        return {"results": []}
    try:
        results = await asyncio.to_thread(search_sync, q)
        return {"results": results}
    except Exception as e:
        logger.warning(f"search failed: {e}")
        return {"results": []}


@api_router.get("/quote/{symbol}")
async def quote(symbol: str):
    try:
        data = await asyncio.to_thread(fetch_quote_sync, symbol)
        return data
    except Exception as e:
        logger.warning(f"quote failed for {symbol}: {e}")
        raise HTTPException(status_code=404, detail="Quote unavailable for this ticker")


@api_router.get("/chart/{symbol}")
async def chart(symbol: str, range: str = "1M"):
    rng = range.upper()
    if rng not in RANGE_MAP:
        rng = "1M"
    try:
        data = await asyncio.to_thread(fetch_chart_sync, symbol, rng)
        return data
    except Exception as e:
        logger.warning(f"chart failed for {symbol}: {e}")
        raise HTTPException(status_code=404, detail="Chart unavailable for this ticker")


# --- OHLC candles (additive; used by the in-app fallback chart for NSE/BSE) ---
def fetch_ohlc_sync(symbol: str, rng: str) -> dict:
    range_, interval = RANGE_MAP.get(rng, ("1mo", "1d"))
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = _yf_get(url, {"range": range_, "interval": interval})
    r.raise_for_status()
    result = r.json()["chart"]["result"][0]
    ts = result.get("timestamp") or []
    q = (result.get("indicators", {}).get("quote") or [{}])[0]
    o, h, l, c, v = (q.get(k) or [] for k in ("open", "high", "low", "close", "volume"))
    bars = []
    for i, t in enumerate(ts):
        try:
            if None in (o[i], h[i], l[i], c[i]):
                continue
            bars.append({
                "time": int(t),
                "open": round(float(o[i]), 4),
                "high": round(float(h[i]), 4),
                "low": round(float(l[i]), 4),
                "close": round(float(c[i]), 4),
                "volume": int(v[i] or 0) if i < len(v) else 0,
            })
        except (IndexError, TypeError, ValueError):
            continue
    meta = result.get("meta", {})
    return {
        "symbol": meta.get("symbol", symbol),
        "range": rng,
        "interval": interval,
        "currency": meta.get("currency"),
        "bars": bars[-500:],
    }


@api_router.get("/ohlc/{symbol}")
async def ohlc(symbol: str, range: str = "1M"):
    rng = range.upper()
    if rng not in RANGE_MAP:
        rng = "1M"
    try:
        return await asyncio.to_thread(fetch_ohlc_sync, symbol, rng)
    except Exception as e:
        logger.warning(f"ohlc failed for {symbol}: {e}")
        raise HTTPException(status_code=404, detail="OHLC unavailable for this ticker")


@api_router.get("/news/{symbol}")
async def news(symbol: str):
    try:
        items = await asyncio.to_thread(fetch_news_sync, symbol)
        return {"results": items}
    except Exception as e:
        logger.warning(f"news failed for {symbol}: {e}")
        return {"results": []}


async def get_market(category: str) -> list:
    now = time.time()
    entry = _market_cache.get(category)
    if entry and entry.get("data") is not None and now - entry["ts"] < 90:
        return entry["data"]
    symbols = MARKET_CATEGORIES.get(category)
    if not symbols:
        raise HTTPException(status_code=404, detail="Unknown market category")
    results = await asyncio.gather(*[asyncio.to_thread(fetch_quote_sync, s) for s in symbols], return_exceptions=True)
    clean = []
    for sym, r in zip(symbols, results):
        if isinstance(r, dict):
            if category == "commodities":
                r["name"] = COMMODITY_NAMES.get(sym, r.get("name"))
            clean.append(r)
    _market_cache[category] = {"ts": now, "data": clean}
    return clean


@api_router.get("/trending")
async def trending():
    return {"results": await get_market("trending")}


@api_router.get("/markets/{category}")
async def markets(category: str):
    return {"results": await get_market(category)}


@api_router.post("/analyze")
async def analyze(body: AnalyzeRequest):
    symbol = (body.symbol or "").strip().upper()
    if not symbol or not re.match(r'^[A-Z0-9.\-\^=]{1,20}$', symbol):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    analysis = {
        "id": str(uuid.uuid4()),
        "symbol": symbol,
        "name": (body.name or symbol),
        "status": "running",
        "messages": [],
        "quote": None,
        "verdict": None,
        "debate": None,
        "current_step": 0,
        "total_steps": TOTAL_STEPS,
        "error": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.analyses.insert_one({**analysis})
    asyncio.create_task(run_analysis(analysis["id"], symbol))
    return analysis


@api_router.get("/analysis/{analysis_id}")
async def get_analysis(analysis_id: str):
    doc = await db.analyses.find_one({"id": analysis_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return doc


@api_router.get("/history")
async def history():
    docs = await db.analyses.find({}, {"_id": 0, "messages": 0}).sort("created_at", -1).to_list(100)
    return {"results": docs}


@api_router.delete("/analysis/{analysis_id}")
async def delete_analysis(analysis_id: str):
    res = await db.analyses.delete_one({"id": analysis_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return {"ok": True}


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
