from fastapi import FastAPI, APIRouter, HTTPException, Header, Depends, Request
from fastapi.responses import HTMLResponse
from pymongo.errors import DuplicateKeyError
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
import httpx
from emergentintegrations.llm.chat import LlmChat, UserMessage
import portfolio_optimizer as pfopt
import wallet as wal
import fundamentals as fund
import auth as au
import mailer
import sms
import razorpay_pay as rzp

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
_news_cache: dict = {}


_yf_session: Optional[requests.Session] = None
_yf_crumb: Optional[str] = None


def _yf_authed_session() -> tuple:
    """Yahoo's quoteSummary endpoint now returns 401 unless the request
    carries a consent cookie plus the matching 'crumb' token (the plain GET
    the other endpoints use still works fine). Fetched once and reused; any
    failure re-fetches next call."""
    global _yf_session, _yf_crumb
    if _yf_session is not None and _yf_crumb:
        return _yf_session, _yf_crumb
    s = requests.Session()
    try:
        s.get("https://fc.yahoo.com", headers=YF_HEADERS, timeout=10)
    except Exception:
        pass  # this call is only here to set the cookie; it often 404s
    r = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", headers=YF_HEADERS, timeout=10)
    r.raise_for_status()
    crumb = (r.text or "").strip()
    if not crumb:
        raise RuntimeError("no crumb returned")
    _yf_session, _yf_crumb = s, crumb
    return s, crumb


def fetch_fundamentals_sync(symbol: str) -> Optional[dict]:
    url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
    params = {"modules": fund.FUNDAMENTALS_MODULES}
    for attempt in (1, 2):
        session, crumb = _yf_authed_session()
        r = session.get(url, params={**params, "crumb": crumb}, headers=YF_HEADERS, timeout=15)
        if r.status_code in (401, 403) and attempt == 1:
            global _yf_session, _yf_crumb
            _yf_session, _yf_crumb = None, None  # stale cookie/crumb — get a fresh pair
            continue
        r.raise_for_status()
        data = r.json()
        results = (data.get("quoteSummary") or {}).get("result") or []
        return results[0] if results else None
    return None


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
# Headline sentiment tagging (AI when available, keyword fallback)
# ----------------------------------------------------------------------------
BULLISH_WORDS = (
    "beat", "beats", "surge", "surges", "soar", "soars", "jump", "jumps", "rally", "rallies",
    "rise", "rises", "gain", "gains", "record high", "all-time high", "upgrade", "upgrades",
    "outperform", "raises guidance", "raise guidance", "buy rating", "profit", "strong demand",
    "tops estimates", "top estimates", "growth", "expands", "wins", "approval", "beat estimates",
    "bullish", "beats expectations", "beat expectations", "price target raised", "beat forecast",
    "dividend hike", "buyback", "breakout", "beats street", "beats revenue",
)
BEARISH_WORDS = (
    "miss", "misses", "plunge", "plunges", "slump", "slumps", "fall", "falls", "drop", "drops",
    "sink", "sinks", "tumble", "tumbles", "slide", "slides", "downgrade", "downgrades",
    "underperform", "cuts guidance", "cut guidance", "sell rating", "loss", "losses", "layoff",
    "layoffs", "lawsuit", "probe", "investigation", "recall", "fraud", "warning", "warns",
    "weak demand", "misses estimates", "miss estimates", "bearish", "selloff", "sell-off",
    "crash", "bankruptcy", "delay", "delays", "fine", "price target cut", "short seller",
    "resign", "resigns", "halt", "halts", "slowdown", "sanctions",
)


def classify_headline_keyword(title: str) -> str:
    t = (title or "").lower()
    bull = sum(1 for w in BULLISH_WORDS if w in t)
    bear = sum(1 for w in BEARISH_WORDS if w in t)
    if bull > bear:
        return "BULLISH"
    if bear > bull:
        return "BEARISH"
    return "NEUTRAL"


NEWS_SENTIMENT_SYS = (
    "You are a financial news sentiment classifier. For each numbered headline, decide how a trader "
    "holding the given asset would read it: BULLISH (good for the price), BEARISH (bad for the price) "
    "or NEUTRAL (informational, mixed or no clear price impact). "
    'Reply ONLY with a JSON array of strings, one label per headline, in order. '
    'Example: ["BULLISH","NEUTRAL","BEARISH"]. No other text.'
)
VALID_SENTIMENTS = {"BULLISH", "BEARISH", "NEUTRAL"}


async def tag_news_sentiment(symbol: str, items: list) -> list:
    """Attach a sentiment label to every headline. Tries one batched LLM call;
    falls back to keyword scoring for any headline the model doesn't cover."""
    if not items:
        return items

    for n in items:
        n["sentiment"] = classify_headline_keyword(n.get("title", ""))

    if not EMERGENT_LLM_KEY:
        return items

    listing = "\n".join(f"{i + 1}. {n.get('title', '')}" for i, n in enumerate(items))
    user_text = f"Asset: {symbol}\nHeadlines:\n{listing}\n\nReturn {len(items)} labels as a JSON array."
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=str(uuid.uuid4()),
            system_message=NEWS_SENTIMENT_SYS,
        ).with_model(MODEL_PROVIDER, MODEL_NAME)
        resp = await asyncio.wait_for(chat.send_message(UserMessage(text=user_text)), timeout=45)
        m = re.search(r"\[.*\]", resp or "", re.DOTALL)
        labels = json.loads(m.group(0)) if m else []
        for i, lab in enumerate(labels):
            if i >= len(items):
                break
            lab = str(lab).strip().upper()
            if lab in VALID_SENTIMENTS:
                items[i]["sentiment"] = lab
    except Exception as e:
        logger.warning(f"news sentiment classification failed for {symbol}: {e}")
    return items


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

TIMEFRAME_SYS = (
    "You are the Multi-Horizon Desk. Using the full desk transcript and the Portfolio Manager's final verdict, "
    "produce a SEPARATE call for three distinct time horizons, because a stock can be attractive on one horizon "
    "and unattractive on another. Output ONLY a raw JSON object (no markdown fences, no prose) with EXACTLY "
    "these keys, each holding an object with EXACTLY these sub-keys: "
    '{"short_term": {"decision": "BUY|SELL|HOLD", "confidence": <integer 0-100>, '
    '"target_price": <number or null>, "stop_loss": <number or null>, "thesis": "<=30 word horizon-specific rationale>"}, '
    '"medium_term": {<same shape>}, "long_term": {<same shape>}}. '
    "short_term = next 1-2 weeks, medium_term = next 1-3 months, long_term = next 6-12 months. "
    "Each horizon's call may genuinely differ from the others and from the primary verdict — do not repeat the "
    "same numbers three times unless the case truly holds across all three horizons."
)

TOTAL_STEPS = 13


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


def parse_timeframes(text: str) -> Optional[dict]:
    """Parse the Multi-Horizon Desk's per-horizon calls. Requires all three
    horizons to parse cleanly; otherwise returns None so the caller falls
    back rather than showing a partially-fabricated set."""
    data = extract_json(text)
    if not isinstance(data, dict):
        return None

    def parse_one(d):
        if not isinstance(d, dict):
            return None
        decision = str(d.get("decision", "")).upper().strip()
        if decision not in ("BUY", "SELL", "HOLD"):
            decision = "HOLD"
        try:
            confidence = int(round(float(d.get("confidence", 50))))
        except Exception:
            confidence = 50
        confidence = max(0, min(100, confidence))

        def num(v):
            try:
                return round(float(v), 2)
            except Exception:
                return None

        thesis = str(d.get("thesis") or "").strip()[:280] or None
        return {
            "decision": decision,
            "confidence": confidence,
            "target_price": num(d.get("target_price")),
            "stop_loss": num(d.get("stop_loss")),
            "thesis": thesis,
        }

    horizons = (
        ("short_term", "Short-term (1-2 weeks)"),
        ("medium_term", "Medium-term (1-3 months)"),
        ("long_term", "Long-term (6-12 months)"),
    )
    out = {}
    for key, label in horizons:
        one = parse_one(data.get(key))
        if one is None:
            return None
        one["horizon"] = key
        one["label"] = label
        out[key] = one
    return out


def fallback_timeframes(verdict: dict) -> dict:
    """Used only when the Multi-Horizon Desk call fails or doesn't parse.
    Never invents new price levels — reuses the primary verdict's decision
    and confidence, leaves target/stop null, and says so explicitly."""
    horizons = (
        ("short_term", "Short-term (1-2 weeks)"),
        ("medium_term", "Medium-term (1-3 months)"),
        ("long_term", "Long-term (6-12 months)"),
    )
    return {
        key: {
            "horizon": key,
            "label": label,
            "decision": verdict.get("decision", "HOLD"),
            "confidence": verdict.get("confidence", 50),
            "target_price": None,
            "stop_loss": None,
            "thesis": "Timeframe-specific view unavailable — shown as the primary desk verdict.",
        }
        for key, label in horizons
    }


def build_context(symbol: str, quote: Optional[dict], fundamentals_summary: Optional[str] = None) -> str:
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
    if fundamentals_summary:
        lines.append("")
        lines.append("FUNDAMENTALS (source: latest available data, may lag real-time filings):")
        lines.append(fundamentals_summary)
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
async def run_analysis(analysis_id: str, symbol: str, language: str = "en"):
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

        fundamentals_summary = None
        try:
            raw_fundamentals = await asyncio.to_thread(fetch_fundamentals_sync, symbol)
            fundamentals_summary = fund.summarize(fund.parse_fundamentals(raw_fundamentals))
        except Exception as e:
            logger.warning(f"fundamentals unavailable for {symbol}: {e}")

        ctx = build_context(symbol, quote, fundamentals_summary)
        step = 0
        lang_directive = language_directive(language if language in SUPPORTED_LANGUAGES else "en")

        # PHASE 1 — Analyst team (concurrent)
        analyst_specs = [
            ("Technical Analyst", "TECHNICAL_ANALYST", TECH_SYS),
            ("Fundamentals Analyst", "FUNDAMENTALS_ANALYST", FUND_SYS),
            ("Sentiment Analyst", "SENTIMENT_ANALYST", SENT_SYS),
            ("News Analyst", "NEWS_ANALYST", NEWS_SYS),
        ]
        analyst_tasks = [
            safe_agent(sysmsg + lang_directive, f"{ctx}\n\nProvide your {name} briefing for {symbol}.")
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
            bull_raw = await safe_agent(BULL_SYS + lang_directive, bull_user)
            bull_content, _ = split_signal(bull_raw)
            step += 1
            await append_message(analysis_id, make_message("Bull Researcher", "BULL_RESEARCHER", "debate", bull_content, "bullish"), step)
            bull_prev = bull_content
            debate_log.append(f"BULL (r{rnd}): {bull_content}")

            bear_user = (
                f"{ctx}\n\nANALYST REPORTS:\n{analyst_summary}\n\n"
                f"The Bull just argued:\n{bull_prev}\n\nRebut the bull and make the BEARISH case for {symbol} (round {rnd})."
            )
            bear_raw = await safe_agent(BEAR_SYS + lang_directive, bear_user)
            bear_content, _ = split_signal(bear_raw)
            step += 1
            await append_message(analysis_id, make_message("Bear Researcher", "BEAR_RESEARCHER", "debate", bear_content, "bearish"), step)
            bear_prev = bear_content
            debate_log.append(f"BEAR (r{rnd}): {bear_content}")
        debate_text = "\n\n".join(debate_log)

        # PHASE 3 — Research Manager
        rm_raw = await safe_agent(
            RM_SYS + lang_directive,
            f"{ctx}\n\nANALYST REPORTS:\n{analyst_summary}\n\nDEBATE:\n{debate_text}\n\nJudge the debate and give the recommended stance for {symbol}.",
        )
        rm_content, rm_sig = split_signal(rm_raw)
        step += 1
        await append_message(analysis_id, make_message("Research Manager", "RESEARCH_MANAGER", "debate", rm_content, rm_sig), step)

        # PHASE 4 — Trader
        tr_raw = await safe_agent(
            TRADER_SYS + lang_directive,
            f"{ctx}\n\nANALYST REPORTS:\n{analyst_summary}\n\nDEBATE VERDICT:\n{rm_content}\n\nPropose a concrete trade plan for {symbol}.",
        )
        tr_content, tr_sig = split_signal(tr_raw)
        step += 1
        await append_message(analysis_id, make_message("Trader", "TRADER", "trade", tr_content, tr_sig), step)

        # PHASE 5 — Risk Manager
        rk_raw = await safe_agent(
            RISK_SYS + lang_directive,
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
            PM_SYS + lang_directive,
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
            DEBATE_SYS + lang_directive,
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

        # PHASE 8 — Multi-Horizon Desk (short / medium / long-term calls)
        tf_raw = await safe_agent(
            TIMEFRAME_SYS + lang_directive,
            f"{ctx}\n\nDESK TRANSCRIPT:\n{full_transcript}\n\n"
            f"PRIMARY VERDICT: {verdict['decision']} ({verdict['confidence']}%), "
            f"target {verdict.get('target_price')}, stop {verdict.get('stop_loss')}. {verdict['summary']}\n\n"
            f"Produce the three-horizon JSON for {symbol}.",
            fallback="{}",
        )
        timeframes = parse_timeframes(tf_raw) or fallback_timeframes(verdict)
        step += 1
        tf_msg = (
            f"SHORT-TERM: {timeframes['short_term']['decision']} ({timeframes['short_term']['confidence']}%)\n"
            f"MEDIUM-TERM: {timeframes['medium_term']['decision']} ({timeframes['medium_term']['confidence']}%)\n"
            f"LONG-TERM: {timeframes['long_term']['decision']} ({timeframes['long_term']['confidence']}%)"
        )
        await append_message(analysis_id, make_message("Multi-Horizon Desk", "MULTI_HORIZON_DESK", "decision", tf_msg, None), step)

        grounding = ground_verdict(verdict, quote)
        await db.analyses.update_one(
            {"id": analysis_id},
            {"$set": {"status": "completed", "verdict": verdict, "debate": debate, "timeframes": timeframes, "grounding": grounding, "current_step": TOTAL_STEPS, "updated_at": now_iso()}},
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
SUPPORTED_LANGUAGES = {"en": "English", "hi": "Hindi", "es": "Spanish", "zh": "Mandarin Chinese"}


def language_directive(lang: str) -> str:
    """Appended to an agent's system prompt when lang != "en". Returns ""
    (a no-op) for English, so English-language behavior is byte-identical
    to before this feature existed. Explicitly protects the fixed JSON
    keys and enum values every parser depends on."""
    name = SUPPORTED_LANGUAGES.get(lang)
    if not name or lang == "en":
        return ""
    return (
        f"\n\nRespond in {name}. Write every free-text field's VALUE in {name} "
        "(summaries, arguments, theses, risk descriptions, rationale). "
        "Keep every JSON KEY in English exactly as specified, and keep every "
        'enum value (e.g. "decision": "BUY", "SELL", or "HOLD") in English '
        "exactly as specified — never translate a key or an enum value."
    )


class AnalyzeRequest(BaseModel):
    symbol: str
    name: Optional[str] = None
    language: str = "en"
    device_id: Optional[str] = None  # required only when WALLET_ENFORCEMENT_ENABLED


# --- Auth config + session dependencies. Defined here (ahead of the wallet
# and analyze endpoints) because those endpoints depend on them. The auth
# endpoints themselves live further down. ---
AUTH_REQUIRED_ENABLED = os.environ.get("AUTH_REQUIRED_ENABLED", "false").lower() == "true"
AUTH_DEBUG_RETURN_OTP = os.environ.get("AUTH_DEBUG_RETURN_OTP", "false").lower() == "true"  # DEV ONLY
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-change-me")
# Comma-separated phone/email allowlist — NOT hardcoded in source. Ships
# with one default so the requested super-user works immediately; change
# or extend via the real env var in your deployment, not by editing this line.
ADMIN_IDENTIFIERS = au.parse_admin_identifiers(os.environ.get("ADMIN_IDENTIFIERS", "+918446307145"))
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")  # unused: Google runs through Emergent managed auth
APPLE_SERVICES_ID = os.environ.get("APPLE_SERVICES_ID", "")


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """FastAPI dependency for endpoints that always require a valid session."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]
    payload = au.decode_session_token(token, JWT_SECRET)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    user = await db.users.find_one({"id": payload["sub"]})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


async def require_admin(authorization: Optional[str] = Header(None)) -> dict:
    """FastAPI dependency for admin-only endpoints. Requires a valid
    session AND that the account's phone/email is in ADMIN_IDENTIFIERS.
    Intentionally unused in this pass — it exists for a future,
    narrowly-scoped admin endpoint that exposes no individual user data."""
    user = await get_current_user(authorization)
    if not au.is_admin(user, ADMIN_IDENTIFIERS):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_user(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    """Session dependency for the priced/user-specific endpoints (analyze,
    wallet). A token is always honoured when present; it is *mandatory* only
    when AUTH_REQUIRED_ENABLED is on, so the flag stays a single switch."""
    if not authorization:
        if AUTH_REQUIRED_ENABLED:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return None
    return await get_current_user(authorization)


def wallet_key_for(user: Optional[dict], device_id: Optional[str]) -> Optional[str]:
    """Signed-in users get an account-keyed wallet so the balance follows them
    across devices; anonymous callers fall back to their device id."""
    if user:
        return f"user:{user['id']}"
    return device_id


ADMIN_PHONES = [p for p in os.environ.get("ADMIN_PHONES", "").split(",") if p.strip()]


def is_admin(user: Optional[dict]) -> bool:
    """Admin allowlist now lives in ADMIN_IDENTIFIERS (phone OR email,
    normalized); ADMIN_PHONES is still honoured so an existing deployment's
    env keeps working."""
    return au.is_admin(user, ADMIN_IDENTIFIERS) or (
        bool(user) and wal.is_admin_phone(user.get("phone"), ADMIN_PHONES)
    )


# --- Wallet / usage-based pricing (additive; OFF by default — see WALLET_ENFORCEMENT_ENABLED) ---
WALLET_ENFORCEMENT_ENABLED = os.environ.get("WALLET_ENFORCEMENT_ENABLED", "false").lower() == "true"
# Launch promotion: everyone bypasses billing until this date, automatically
# — no manual flag to remember to flip weeks later. Empty by default (no
# free period unless explicitly configured). ISO date, e.g. "2026-10-16".
LAUNCH_FREE_UNTIL = os.environ.get("LAUNCH_FREE_UNTIL", "")
# Used to build the Razorpay checkout/callback URLs, which must be absolute
# and publicly reachable over HTTPS.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def public_base(request: Request) -> str:
    """Absolute origin used for the Razorpay checkout + callback URLs. Derived
    from the incoming request (honouring the ingress' forwarded headers) so
    preview and production each point back at themselves — a stale
    PUBLIC_BASE_URL baked into a deployed image would otherwise send paying
    customers to the wrong host, where their order id doesn't exist. The env
    var stays as a fallback for local/CLI use."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}".rstrip("/") if host else PUBLIC_BASE_URL


class WalletTopup(BaseModel):
    device_id: Optional[str] = None  # ignored for signed-in users (account-keyed wallet)
    amount: float  # must match one of wallet.TOPUP_PACKS
    # Razorpay's payment links require BOTH an email and a phone number, but an
    # account only has whichever one was used to sign in. The app asks for the
    # missing one once and it's stored on the account from then on.
    email: Optional[str] = None
    phone: Optional[str] = None


async def get_free_credits_remaining(user: Optional[dict]) -> int:
    """Accounts created before free credits existed are backfilled on first
    read, so nobody is worse off than a brand-new signup."""
    if not user:
        return 0
    if "free_credits_remaining" not in user:
        await db.users.update_one(
            {"id": user["id"], "free_credits_remaining": {"$exists": False}},
            {"$set": {"free_credits_remaining": wal.FREE_CREDITS_ON_SIGNUP}},
        )
        return wal.FREE_CREDITS_ON_SIGNUP
    try:
        return max(0, int(user.get("free_credits_remaining") or 0))
    except (TypeError, ValueError):
        return 0


async def consume_free_credit(user: dict) -> bool:
    """Atomically spends one free credit. The `$gt: 0` guard is what stops
    two concurrent analyses from spending the same last credit twice."""
    res = await db.users.update_one(
        {"id": user["id"], "free_credits_remaining": {"$gt": 0}},
        {"$inc": {"free_credits_remaining": -1}},
    )
    return res.modified_count == 1


async def get_wallet_balance(device_id: str) -> float:
    doc = await db.wallets.find_one({"device_id": device_id})
    return doc["balance"] if doc else 0.0


def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def launch_free_daily_remaining(key: str) -> int:
    """Read-only view of today's remaining launch-free allowance."""
    wallet_doc = await db.wallets.find_one({"device_id": key}) or {}
    _, count_today = wal.launch_free_daily_state(
        wallet_doc.get("launch_free_daily_date", ""),
        wallet_doc.get("launch_free_daily_count", 0),
        _utc_day(),
    )
    return max(0, wal.LAUNCH_FREE_DAILY_CAP - count_today)


async def latest_completed_analysis_for(symbol: str, language: str = "en") -> Optional[dict]:
    """Most recent completed analysis document for a symbol IN THE REQUESTED
    LANGUAGE (full doc, not just the verdict) — used only for the wallet's
    free-recheck decision. A cached run in another language is not a valid
    answer, so it isn't reused. Never triggers a new run."""
    return await db.analyses.find_one(
        {"symbol": symbol, "language": language, "status": "completed", "verdict": {"$ne": None}},
        {"_id": 0},
        sort=[("updated_at", -1)],
    )


@api_router.get("/wallet/balance")
async def wallet_balance(device_id: Optional[str] = None, user: Optional[dict] = Depends(require_user)):
    key = wallet_key_for(user, device_id)
    if not key:
        raise HTTPException(status_code=400, detail="device_id is required")
    balance = await get_wallet_balance(key)
    admin = is_admin(user)
    free_credits = await get_free_credits_remaining(user)
    launch_free = wal.is_launch_free_period(LAUNCH_FREE_UNTIL, datetime.now(timezone.utc))
    daily_left = await launch_free_daily_remaining(key) if launch_free else None
    return {
        "device_id": key,
        "balance": round(balance, 2),
        "currency": wal.CURRENCY,
        "symbol": wal.CURRENCY_SYMBOL,
        "prices": wal.PRICES,
        "packs": wal.TOPUP_PACKS,
        "free_credits_remaining": free_credits,
        # Admins are never billed, and neither is anyone with free credits
        # left — so the app shows them no balance gate. Nor is anyone with
        # launch-free allowance left today, otherwise the app would grey out
        # the analyze button for a drained wallet the backend would run free.
        "enforcement_enabled": (
            WALLET_ENFORCEMENT_ENABLED
            and not admin
            and not (launch_free and (daily_left or 0) > 0)
            and free_credits <= 0
        ),
        "is_admin": admin,
        "payments_live": rzp.payments_configured(),
        "launch_free_active": launch_free,
        # Powers the on-screen countdown; `remaining` is null outside the window.
        "launch_free_daily_remaining": daily_left,
        "launch_free_daily_cap": wal.LAUNCH_FREE_DAILY_CAP,
    }


async def credit_wallet_once(payment_id: str, order: dict) -> bool:
    """The only place a balance is ever credited from a payment. The unique
    index on payment_id is what makes a double credit impossible, whichever
    of the callback / webhook arrives first (or twice)."""
    try:
        await db.wallet_ledger.insert_one({
            "payment_id": payment_id,
            "order_id": order["razorpay_order_id"],
            "wallet_key": order["wallet_key"],
            "amount": order["amount"],
            "currency": wal.CURRENCY,
            "created_at": now_iso(),
        })
    except DuplicateKeyError:
        return False
    await db.wallets.update_one(
        {"device_id": order["wallet_key"]},
        {"$inc": {"balance": order["amount"]},
         "$set": {"device_id": order["wallet_key"], "updated_at": now_iso()}},
        upsert=True,
    )
    await db.payments.update_one(
        {"razorpay_order_id": order["razorpay_order_id"]},
        {"$set": {"status": "captured", "payment_id": payment_id, "updated_at": now_iso()}},
    )
    logger.info(f"wallet credited {wal.CURRENCY} {order['amount']} for {order['wallet_key']} ({payment_id})")
    return True


async def settle_payment(order: dict, payment_id: str) -> str:
    """Confirms with Razorpay that the payment really is captured, then
    credits. Returns the payment status."""
    payment = await rzp.fetch_payment(payment_id)
    if payment.get("order_id") != order["razorpay_order_id"]:
        raise HTTPException(status_code=400, detail="Order mismatch")
    if payment.get("amount") != int(round(order["amount"] * 100)):
        raise HTTPException(status_code=400, detail="Amount mismatch")
    status = payment.get("status", "unknown")
    if status == "captured":
        await credit_wallet_once(payment_id, order)
    else:
        await db.payments.update_one(
            {"razorpay_order_id": order["razorpay_order_id"]},
            {"$set": {"status": status, "updated_at": now_iso()}},
        )
    return status


async def bind_order_id(record: dict, payment_id: str) -> dict:
    """A Payment Link's underlying Razorpay order only exists once the customer
    actually starts paying, so the stored record is created without one and the
    id is bound here — before the normal order-based verification runs."""
    payment = await rzp.fetch_payment(payment_id)
    order_id = payment.get("order_id")
    if order_id and record.get("razorpay_order_id") != order_id:
        await db.payments.update_one(
            {"reference_id": record["reference_id"]},
            {"$set": {"razorpay_order_id": order_id, "updated_at": now_iso()}},
        )
        record = {**record, "razorpay_order_id": order_id}
    return record


async def resolve_payment_customer(user: Optional[dict], body: "WalletTopup") -> tuple[dict, list]:
    """Razorpay rejects a payment link unless it carries a customer email AND
    contact number. Users sign in with only one of the two, so this fills in
    what's known, accepts whatever the app just asked for, remembers it on the
    account, and reports what's still missing."""
    email = (user or {}).get("email")
    phone = (user or {}).get("phone")
    for raw in (body.email, body.phone):
        if not raw:
            continue
        kind, normalized = au.normalize_identifier(raw)
        if kind == "email":
            email = normalized
        elif kind == "phone":
            phone = normalized
    missing = [name for name, value in (("email", email), ("phone", phone)) if not value]
    if not missing and user and (email != user.get("email") or phone != user.get("phone")):
        await db.users.update_one({"id": user["id"]}, {"$set": {"email": email, "phone": phone}})
    customer = {"name": (user or {}).get("name") or "TradingAgents user", "email": email, "contact": phone}
    return customer, missing


@api_router.post("/pay/order")
async def create_topup_order(body: WalletTopup, request: Request, user: Optional[dict] = Depends(require_user)):
    """Creates a Razorpay Payment Link for one of the fixed top-up packs and
    returns its hosted checkout URL. The amount is validated here — never taken
    on trust."""
    if not rzp.payments_configured():
        raise HTTPException(status_code=503, detail="Payments aren't set up yet")
    if not wal.is_valid_topup(body.amount):
        raise HTTPException(
            status_code=400,
            detail=f"Choose one of the top-up packs: {', '.join(str(int(p)) for p in wal.TOPUP_PACKS)}",
        )
    key = wallet_key_for(user, body.device_id)
    if not key:
        raise HTTPException(status_code=400, detail="device_id is required")

    customer, missing = await resolve_payment_customer(user, body)
    if missing:
        # Machine-readable so the app can ask for exactly what's missing.
        raise HTTPException(status_code=400, detail=f"contact_required:{','.join(missing)}")

    reference_id = f"wallet_{uuid.uuid4().hex[:20]}"
    try:
        link = await rzp.create_payment_link(
            amount=body.amount,
            reference_id=reference_id,
            notes={"wallet_key": key, "purpose": "wallet_topup"},
            callback_url=f"{public_base(request)}/api/pay/callback",
            description=f"{wal.CURRENCY_SYMBOL}{body.amount:.0f} wallet top-up",
            customer=customer,
        )
    except rzp.RazorpayError as e:
        logger.error(f"razorpay payment link creation failed [{e.code}]: {e.description}")
        # Razorpay's own wording is the only useful thing here — a generic
        # "try again" sent the user round the same loop three times.
        raise HTTPException(status_code=502, detail=f"Razorpay: {e.description}")
    except Exception as e:
        logger.error(f"razorpay payment link creation failed: {e}")
        raise HTTPException(status_code=502, detail="Couldn't reach Razorpay — try again")

    await db.payments.insert_one({
        "razorpay_payment_link_id": link["id"],
        # A link's underlying order only exists once the customer starts paying.
        # The field is omitted (not null) so the unique sparse index ignores it.
        **({"razorpay_order_id": link["order_id"]} if link.get("order_id") else {}),
        "reference_id": reference_id,
        "wallet_key": key,
        "amount": body.amount,
        "currency": wal.CURRENCY,
        "status": "created",
        "receipt": reference_id,
        "short_url": link["short_url"],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    })
    return {
        # The app polls /pay/status with whatever id it gets back.
        "order_id": link["id"],
        "amount": body.amount,
        "currency": wal.CURRENCY,
        "checkout_url": link["short_url"],
    }


@api_router.api_route("/pay/callback", methods=["POST", "GET"], response_class=HTMLResponse)
async def pay_callback(request: Request):
    """Razorpay redirects the customer back here after the hosted payment page.
    Payment Links arrive as a GET with the razorpay_payment_link_* params; the
    older self-hosted checkout arrived as a form POST. Cancels, failures and
    some bank / UPI redirect chains arrive with no fields at all, so every
    field is read defensively (a strict Form(...) signature 422s the user
    mid-payment). Whatever arrives is a hint only: the signature is checked and
    the payment re-fetched from Razorpay before anything is credited."""
    fields: dict = {}
    if request.method == "POST":
        ctype = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in ctype or "multipart/form-data" in ctype:
            fields = dict(await request.form())
        elif "application/json" in ctype:
            try:
                fields = await request.json()
            except Exception:
                fields = {}
    q = request.query_params
    link_id = fields.get("razorpay_payment_link_id") or q.get("razorpay_payment_link_id")
    if link_id:
        return await link_callback(fields, q, link_id)

    razorpay_payment_id = fields.get("razorpay_payment_id") or q.get("razorpay_payment_id")
    razorpay_order_id = (
        fields.get("razorpay_order_id") or q.get("razorpay_order_id") or q.get("order_id")
    )
    razorpay_signature = fields.get("razorpay_signature") or q.get("razorpay_signature")

    order = await db.payments.find_one({"razorpay_order_id": razorpay_order_id}) if razorpay_order_id else None

    # Cancelled / failed / bodyless redirect — nothing to verify, nothing charged.
    if not razorpay_payment_id or not razorpay_signature:
        logger.info(f"payment callback without success fields for order {razorpay_order_id}")
        if order:
            await db.payments.update_one(
                {"razorpay_order_id": order["razorpay_order_id"], "status": {"$ne": "captured"}},
                {"$set": {
                    "status": "failed",
                    "failure": fields.get("error[description]") or q.get("error[description]"),
                    "updated_at": now_iso(),
                }},
            )
        return HTMLResponse(rzp.result_html("Payment wasn't completed — nothing was charged.", ok=False))

    if not order or not rzp.verify_checkout_signature(
        order["razorpay_order_id"], razorpay_payment_id, razorpay_signature
    ):
        logger.warning(f"invalid payment signature for order {razorpay_order_id}")
        return HTMLResponse(rzp.result_html("We couldn't verify that payment.", ok=False), status_code=400)
    try:
        status = await settle_payment(order, razorpay_payment_id)
    except Exception as e:
        logger.error(f"payment settle failed: {e}")
        return HTMLResponse(rzp.result_html("Payment received — we're still confirming it.", ok=True))
    if status == "captured":
        return HTMLResponse(rzp.result_html(
            f"Added {wal.CURRENCY_SYMBOL}{order['amount']:.0f} to your wallet.", ok=True))
    return HTMLResponse(rzp.result_html("That payment didn't go through — nothing was charged.", ok=False))


async def link_callback(fields: dict, q, link_id: str) -> HTMLResponse:
    """Payment Link return leg. Signature message differs from checkout's:
    link_id|reference_id|status|payment_id."""
    def val(name: str) -> str:
        return fields.get(name) or q.get(name) or ""

    payment_id = val("razorpay_payment_id")
    reference_id = val("razorpay_payment_link_reference_id")
    link_status = val("razorpay_payment_link_status")
    signature = val("razorpay_signature")

    record = await db.payments.find_one({"razorpay_payment_link_id": link_id})
    if not payment_id or not signature:
        logger.info(f"payment link callback without success fields for {link_id}")
        if record:
            await db.payments.update_one(
                {"reference_id": record["reference_id"], "status": {"$ne": "captured"}},
                {"$set": {"status": "failed", "updated_at": now_iso()}},
            )
        return HTMLResponse(rzp.result_html("Payment wasn't completed — nothing was charged.", ok=False))

    if not record or record["reference_id"] != reference_id or not rzp.verify_link_signature(
        link_id=link_id, reference_id=reference_id, status=link_status,
        payment_id=payment_id, supplied=signature,
    ):
        logger.warning(f"invalid payment link signature for {link_id}")
        return HTMLResponse(rzp.result_html("We couldn't verify that payment.", ok=False), status_code=400)

    try:
        record = await bind_order_id(record, payment_id)
        status = await settle_payment(record, payment_id)
    except Exception as e:
        logger.error(f"payment link settle failed: {e}")
        return HTMLResponse(rzp.result_html("Payment received — we're still confirming it.", ok=True))
    if status == "captured":
        return HTMLResponse(rzp.result_html(
            f"Added {wal.CURRENCY_SYMBOL}{record['amount']:.0f} to your wallet.", ok=True))
    return HTMLResponse(rzp.result_html("That payment didn't go through — nothing was charged.", ok=False))


@api_router.post("/pay/webhook")
async def pay_webhook(request: Request):
    """Razorpay's server-to-server confirmation. Verified against the raw body
    and de-duplicated by event id."""
    raw = await request.body()
    if not rzp.verify_webhook_signature(raw, request.headers.get("X-Razorpay-Signature", "")):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_id = request.headers.get("X-Razorpay-Event-Id", str(uuid.uuid4()))
    try:
        await db.webhook_events.insert_one({"event_id": event_id, "received_at": now_iso()})
    except DuplicateKeyError:
        return {"ok": True, "duplicate": True}

    event = json.loads(raw or b"{}")
    name = event.get("event")
    payload = event.get("payload", {}) or {}
    entity = (payload.get("payment", {}) or {}).get("entity", {})
    payment_id, order_id = entity.get("id"), entity.get("order_id")

    # Payment Links carry their own entity and are the authoritative event for
    # the top-up flow — the underlying order id may not be on our record yet.
    link_entity = (payload.get("payment_link", {}) or {}).get("entity", {})
    if link_entity.get("id"):
        record = await db.payments.find_one({"razorpay_payment_link_id": link_entity["id"]})
        if not record:
            return {"ok": True}
        if name == "payment_link.paid" and payment_id:
            expected = int(round(record["amount"] * 100))
            if link_entity.get("amount_paid") == expected and link_entity.get("currency") == record["currency"]:
                record = await bind_order_id(record, payment_id)
                await settle_payment(record, payment_id)
        elif name in ("payment_link.expired", "payment_link.cancelled"):
            await db.payments.update_one(
                {"reference_id": record["reference_id"], "status": {"$ne": "captured"}},
                {"$set": {"status": "failed", "updated_at": now_iso()}},
            )
        return {"ok": True}

    if not order_id:
        return {"ok": True}

    order = await db.payments.find_one({"razorpay_order_id": order_id})
    if not order:
        return {"ok": True}

    if name == "payment.captured" and payment_id:
        if entity.get("amount") == int(round(order["amount"] * 100)):
            await credit_wallet_once(payment_id, order)
    elif name == "payment.failed":
        await db.payments.update_one(
            {"razorpay_order_id": order_id},
            {"$set": {"status": "failed", "failure": entity.get("error_description"), "updated_at": now_iso()}},
        )
    return {"ok": True}


@api_router.get("/pay/status/{order_id}")
async def pay_status(order_id: str, user: Optional[dict] = Depends(require_user)):
    """Polled by the app after checkout closes. If the browser redirect never
    made it back (WebView dismissed, network dropped), this asks Razorpay
    directly and credits then — so a paid top-up is never lost. Accepts either
    a payment link id (plink_…) or a legacy order id."""
    order = await db.payments.find_one(
        {"$or": [{"razorpay_payment_link_id": order_id}, {"razorpay_order_id": order_id}]}
    )
    if not order:
        raise HTTPException(status_code=404, detail="Unknown order")
    key = wallet_key_for(user, None)
    if key and order["wallet_key"] != key:
        raise HTTPException(status_code=403, detail="Not your order")

    if order["status"] != "captured" and rzp.payments_configured():
        try:
            if order.get("razorpay_payment_link_id"):
                link = await rzp.fetch_payment_link(order["razorpay_payment_link_id"])
                expected = int(round(order["amount"] * 100))
                paid = (
                    link.get("status") == "paid"
                    and link.get("amount_paid") == expected
                    and link.get("currency") == order["currency"]
                )
                payment_id = next(
                    (p.get("payment_id") for p in reversed(link.get("payments") or []) if p.get("payment_id")),
                    None,
                )
                if paid and payment_id:
                    order = await bind_order_id(order, payment_id)
                    await settle_payment(order, payment_id)
                elif link.get("status") in ("expired", "cancelled"):
                    await db.payments.update_one(
                        {"reference_id": order["reference_id"], "status": {"$ne": "captured"}},
                        {"$set": {"status": "failed", "updated_at": now_iso()}},
                    )
            elif order.get("razorpay_order_id"):
                found = await rzp.razorpay_request("GET", f"/orders/{order['razorpay_order_id']}/payments")
                for p in found.get("items", []):
                    if p.get("status") == "captured":
                        await settle_payment(order, p["id"])
                        break
            order = await db.payments.find_one(
                {"$or": [{"razorpay_payment_link_id": order_id}, {"razorpay_order_id": order_id}]}
            )
        except Exception as e:
            logger.warning(f"payment status refresh failed: {e}")

    balance = await get_wallet_balance(order["wallet_key"])
    return {"order_id": order_id, "status": order["status"], "balance": round(balance, 2)}


# --- Auth endpoints. Phone/email OTP + Google sign-in. Email codes go out via
# Emergent managed email, SMS via Twilio, and Google sign-in via Emergent
# managed auth. Apple sign-in is still a placeholder (needs an Apple Services
# ID from the app owner). ---
EMERGENT_SESSION_DATA_URL = "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"


class OtpRequest(BaseModel):
    identifier: str  # phone or email, auto-detected
    device_id: Optional[str] = None  # to link an existing anonymous wallet on first login


class OtpVerify(BaseModel):
    identifier: str
    otp: str
    device_id: Optional[str] = None


class SocialSignIn(BaseModel):
    token: str  # Google id_token or Apple identity_token
    device_id: Optional[str] = None


async def send_welcome_if_new(user: dict) -> None:
    """One welcome email per account, ever. Only for accounts that have an
    email address (phone-only sign-ups have nowhere to send it)."""
    email = user.get("email")
    if not email or user.get("welcome_sent_at"):
        return
    # Claim it first so a double sign-in can't send twice.
    claimed = await db.users.update_one(
        {"id": user["id"], "welcome_sent_at": {"$in": [None, ""]}},
        {"$set": {"welcome_sent_at": now_iso()}},
    )
    if claimed.modified_count == 0:
        return
    if not await mailer.send_welcome_email(email):
        await db.users.update_one({"id": user["id"]}, {"$set": {"welcome_sent_at": None}})


async def find_or_create_user(identifier_type: str, identifier: str) -> dict:
    key = "phone" if identifier_type == "phone" else "email"
    existing = await db.users.find_one({key: identifier})
    if existing:
        return existing
    user = {"id": str(uuid.uuid4()), "phone": None, "email": None, "google_sub": None,
            "apple_sub": None, "free_credits_remaining": wal.FREE_CREDITS_ON_SIGNUP,
            "created_at": now_iso()}
    user[key] = identifier
    await db.users.insert_one({**user})
    return user


async def link_device_wallet_to_user(device_id: Optional[str], user_id: str) -> None:
    """On first login, merge an existing anonymous device wallet balance
    into the user's own wallet rather than losing it. Additive — never
    removes the device-keyed record, just credits the user-keyed one."""
    if not device_id:
        return
    device_wallet = await db.wallets.find_one({"device_id": device_id})
    if not device_wallet or device_wallet.get("balance", 0) <= 0:
        return
    user_wallet = await db.wallets.find_one({"device_id": f"user:{user_id}"})
    current = user_wallet["balance"] if user_wallet else 0.0
    merged = round(current + device_wallet["balance"], 4)
    await db.wallets.update_one(
        {"device_id": f"user:{user_id}"},
        {"$set": {"device_id": f"user:{user_id}", "balance": merged, "updated_at": now_iso()}},
        upsert=True,
    )
    await db.wallets.update_one({"device_id": device_id}, {"$set": {"balance": 0.0, "updated_at": now_iso()}})


@api_router.post("/auth/otp/request")
async def auth_otp_request(body: OtpRequest):
    id_type, identifier = au.normalize_identifier(body.identifier)
    if not id_type:
        raise HTTPException(status_code=400, detail="Enter a valid phone number or email address")

    recent = await db.otp_requests.find({"identifier": identifier}, None).sort("created_at", -1).to_list(20)
    recent_ts = [r["created_at"] for r in recent]
    if au.is_rate_limited(recent_ts):
        raise HTTPException(status_code=429, detail="Too many attempts — try again later")

    if id_type == "phone" and not sms.sms_configured():
        raise HTTPException(
            status_code=503,
            detail="Text messages aren't available yet — sign in with your email address instead",
        )

    otp = au.generate_otp()
    doc = {
        "id": str(uuid.uuid4()),
        "identifier": identifier,
        "identifier_type": id_type,
        "otp_hash": au.hash_otp(otp),
        "attempts": 0,
        "verified": False,
        "created_at": now_iso(),
    }
    await db.otp_requests.insert_one({**doc})

    ttl_minutes = au.OTP_TTL_SECONDS // 60
    if id_type == "email":
        delivered = await mailer.send_otp_email(identifier, otp, ttl_minutes)
        if not delivered and not AUTH_DEBUG_RETURN_OTP:
            raise HTTPException(status_code=502, detail="Couldn't send the code — try again in a moment")
    else:
        delivered, err = await sms.send_otp_sms(identifier, otp, ttl_minutes, mailer.EMAIL_FROM_NAME)
        if not delivered and not AUTH_DEBUG_RETURN_OTP:
            raise HTTPException(status_code=502, detail=err or "Couldn't send the text — try again")

    response = {"identifier": identifier, "identifier_type": id_type, "sent": True}
    if AUTH_DEBUG_RETURN_OTP:
        # DEV/TEST ONLY, off by default. Never enable this in production —
        # it puts the code in the API response for anyone to read.
        response["debug_otp"] = otp
    return response


@api_router.post("/auth/otp/verify")
async def auth_otp_verify(body: OtpVerify):
    id_type, identifier = au.normalize_identifier(body.identifier)
    if not id_type:
        raise HTTPException(status_code=400, detail="Enter a valid phone number or email address")

    record = await db.otp_requests.find_one({"identifier": identifier, "verified": False}, sort=[("created_at", -1)])
    if not record:
        raise HTTPException(status_code=400, detail="No pending code for this identifier — request a new one")
    if au.is_otp_expired(record["created_at"]):
        raise HTTPException(status_code=400, detail="Code expired — request a new one")
    if record.get("attempts", 0) >= au.OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many incorrect attempts — request a new code")
    if not au.verify_otp_code(body.otp, record["otp_hash"]):
        await db.otp_requests.update_one({"id": record["id"]}, {"$set": {"attempts": record.get("attempts", 0) + 1}})
        raise HTTPException(status_code=400, detail="Incorrect code")

    await db.otp_requests.update_one({"id": record["id"]}, {"$set": {"verified": True}})
    user = await find_or_create_user(id_type, identifier)
    await link_device_wallet_to_user(body.device_id, user["id"])
    asyncio.create_task(send_welcome_if_new(user))
    token = au.create_session_token(user["id"], JWT_SECRET)
    return {"token": token, "user": {"id": user["id"], "phone": user.get("phone"), "email": user.get("email")}}


class GoogleSession(BaseModel):
    session_id: str  # one-time id Emergent appends to the redirect URL
    device_id: Optional[str] = None


@api_router.post("/auth/session")
async def auth_session(body: GoogleSession):
    """Exchanges the one-time Emergent session_id for one of our own session
    tokens, creating/reusing a user keyed on the Google email."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(EMERGENT_SESSION_DATA_URL, headers={"X-Session-ID": body.session_id})
    except Exception as e:
        logger.warning(f"google session exchange failed: {e}")
        raise HTTPException(status_code=502, detail="Couldn't reach the sign-in service — try again")
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="That sign-in link has expired — try again")

    data = resp.json()
    email = (data.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="Google didn't return an email address")

    user = await db.users.find_one({"email": email})
    if not user:
        user = {"id": str(uuid.uuid4()), "phone": None, "email": email, "google_sub": data.get("id"),
                "apple_sub": None, "name": data.get("name"), "picture": data.get("picture"),
                "free_credits_remaining": wal.FREE_CREDITS_ON_SIGNUP, "created_at": now_iso()}
        await db.users.insert_one({**user})
    elif not user.get("google_sub"):
        await db.users.update_one({"id": user["id"]}, {"$set": {"google_sub": data.get("id")}})

    await link_device_wallet_to_user(body.device_id, user["id"])
    asyncio.create_task(send_welcome_if_new(user))
    token = au.create_session_token(user["id"], JWT_SECRET)
    return {"token": token, "user": {"id": user["id"], "phone": user.get("phone"), "email": user.get("email")}}


_apple_jwks_cache: dict = {"keys": None, "fetched_at": 0.0}
APPLE_JWKS_CACHE_TTL = 3600  # seconds — Apple's signing keys rotate infrequently


def fetch_apple_jwks() -> list:
    now = time.time()
    if _apple_jwks_cache["keys"] is not None and (now - _apple_jwks_cache["fetched_at"]) < APPLE_JWKS_CACHE_TTL:
        return _apple_jwks_cache["keys"]
    r = requests.get("https://appleid.apple.com/auth/keys", timeout=10)
    r.raise_for_status()
    keys = r.json().get("keys", [])
    _apple_jwks_cache["keys"] = keys
    _apple_jwks_cache["fetched_at"] = now
    return keys


@api_router.post("/auth/apple")
async def auth_apple(body: SocialSignIn):
    if not APPLE_SERVICES_ID:
        raise HTTPException(status_code=501, detail="Apple sign-in is not configured yet (APPLE_SERVICES_ID unset)")
    try:
        jwks = await asyncio.to_thread(fetch_apple_jwks)
    except Exception as e:
        logger.warning(f"Apple JWKS fetch failed: {e}")
        raise HTTPException(status_code=502, detail="Could not verify Apple sign-in right now — try again")
    claims = au.verify_apple_id_token(body.token, APPLE_SERVICES_ID, jwks)
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid Apple sign-in token")
    user = await db.users.find_one({"apple_sub": claims.get("sub")})
    if not user:
        user = {"id": str(uuid.uuid4()), "phone": None, "email": claims.get("email"),
                "google_sub": None, "apple_sub": claims.get("sub"),
                "free_credits_remaining": wal.FREE_CREDITS_ON_SIGNUP, "created_at": now_iso()}
        await db.users.insert_one({**user})
    await link_device_wallet_to_user(body.device_id, user["id"])
    token = au.create_session_token(user["id"], JWT_SECRET)
    return {"token": token, "user": {"id": user["id"], "email": user.get("email")}}


@api_router.get("/auth/me")
async def auth_me(user: dict = Depends(get_current_user)):
    return {"id": user["id"], "phone": user.get("phone"), "email": user.get("email")}


@api_router.delete("/account")
async def delete_account(user: dict = Depends(get_current_user)):
    """Apple requires in-app account deletion, not just 'contact support'.
    Deletes the account record and its wallet — the two things that
    directly identify and grant access to this person. Deliberately does
    NOT delete payment/transaction records (payments, wallet_ledger):
    financial recordkeeping obligations generally require retaining those
    regardless of account deletion — this is a data-retention judgment
    call, not an oversight, and should be confirmed against your actual
    compliance requirements before relying on it. Analyses aren't deleted
    either, since they were never linked to this account's identity in the
    first place (see the Privacy Policy)."""
    user_id = user["id"]
    await db.users.delete_one({"id": user_id})
    await db.wallets.delete_one({"device_id": f"user:{user_id}"})
    return {"deleted": True}


# --- Portfolio optimization (additive; never mutates analyses or the pipeline) ---
def fetch_price_history_sync(symbol: str, days: int = 300) -> list:
    """Daily closes for covariance/return estimation. Independent of the
    existing /chart and /ohlc endpoints so their behavior is untouched."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = _yf_get(url, {"range": "1y", "interval": "1d"})
    r.raise_for_status()
    result = r.json()["chart"]["result"][0]
    closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", []) or []
    return [round(float(c), 4) for c in closes if c is not None][-days:]


async def latest_verdict_for(symbol: str) -> Optional[dict]:
    """Most recent completed analysis' verdict for a symbol, or None.

    Never triggers a new analysis run; only reads what already exists.
    """
    doc = await db.analyses.find_one(
        {"symbol": symbol, "status": "completed", "verdict": {"$ne": None}},
        sort=[("updated_at", -1)],
    )
    return doc["verdict"] if doc else None


class PortfolioHolding(BaseModel):
    symbol: str
    quantity: float
    avg_price: float


class PortfolioOptimizeRequest(BaseModel):
    holdings: list[PortfolioHolding]
    objective: str = "hrp"  # "hrp" | "max_sharpe" | "min_volatility"
    use_agent_views: bool = False
    cash: float = 0.0  # additional uninvested cash to include in total value


@api_router.post("/portfolio/optimize")
async def portfolio_optimize(body: PortfolioOptimizeRequest):
    if not body.holdings:
        raise HTTPException(status_code=400, detail="No holdings supplied")
    symbols = [h.symbol.strip().upper() for h in body.holdings]
    if len(set(symbols)) != len(symbols):
        raise HTTPException(status_code=400, detail="Duplicate symbols in holdings")

    quotes = await asyncio.gather(
        *[asyncio.to_thread(fetch_quote_sync, s) for s in symbols], return_exceptions=True
    )
    current_prices: dict = {}
    quote_failed: list = []
    for sym, q in zip(symbols, quotes):
        if isinstance(q, Exception) or not q.get("price"):
            quote_failed.append(sym)
        else:
            current_prices[sym] = float(q["price"])

    histories = await asyncio.gather(
        *[asyncio.to_thread(fetch_price_history_sync, s) for s in symbols], return_exceptions=True
    )
    price_history = {
        sym: (h if not isinstance(h, Exception) else [])
        for sym, h in zip(symbols, histories)
    }

    prices_df, dropped = pfopt.build_price_frame(price_history)
    if prices_df.empty or prices_df.shape[1] < 2:
        raise HTTPException(status_code=422, detail="Not enough price history to optimize (need at least 2 symbols with sufficient history)")

    usable_symbols = list(prices_df.columns)
    holdings_by_symbol = {h.symbol.strip().upper(): h for h in body.holdings}
    values = {s: holdings_by_symbol[s].quantity * current_prices.get(s, holdings_by_symbol[s].avg_price) for s in usable_symbols if s in holdings_by_symbol}
    total_value = sum(values.values()) + max(0.0, body.cash)
    current_weights = {s: (v / total_value if total_value else 0.0) for s, v in values.items()}

    verdicts, missing_view_for = {}, []
    if body.use_agent_views:
        for sym in usable_symbols:
            v = await latest_verdict_for(sym)
            if v:
                verdicts[sym] = v
            else:
                missing_view_for.append(sym)

    objective = body.objective if body.objective in ("hrp", "max_sharpe", "min_volatility") else "hrp"
    try:
        result = pfopt.optimize(
            prices_df,
            objective,
            verdicts=verdicts if body.use_agent_views else None,
            current_prices={s: current_prices.get(s) for s in usable_symbols if current_prices.get(s)},
        )
    except Exception as e:
        logger.exception("portfolio optimization failed")
        raise HTTPException(status_code=422, detail=f"Optimization failed: {e}")

    actions = pfopt.classify_actions(current_weights, result["weights"])
    alloc, leftover = ({}, total_value)
    try:
        alloc, leftover = pfopt.discrete_allocation(result["weights"], current_prices, total_value)
    except Exception as e:
        logger.warning(f"discrete allocation failed: {e}")

    return {
        "objective": objective,
        "used_agent_views_for": result.get("views_used", []),
        "missing_agent_view_for": missing_view_for,
        "dropped_symbols": dropped,
        "total_value": round(total_value, 2),
        "current_weights": {k: round(v, 4) for k, v in current_weights.items()},
        "suggested_weights": {k: round(v, 4) for k, v in result["weights"].items()},
        "actions": actions,
        "suggested_shares": alloc,
        "leftover_cash": round(leftover, 2),
        "expected_return": result["expected_return"],
        "volatility": result["volatility"],
        "sharpe": result["sharpe"],
        "current_prices": current_prices,
    }


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
    key = (symbol or "").upper().strip()
    entry = _news_cache.get(key)
    if entry and time.time() - entry["ts"] < 600:
        return {"results": entry["data"]}
    try:
        items = await asyncio.to_thread(fetch_news_sync, symbol)
        items = await tag_news_sentiment(key, items)
        _news_cache[key] = {"ts": time.time(), "data": items}
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
async def analyze(body: AnalyzeRequest, user: Optional[dict] = Depends(require_user)):
    symbol = (body.symbol or "").strip().upper()
    if not symbol or not re.match(r'^[A-Z0-9.\-\^=]{1,20}$', symbol):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")
    language = body.language if body.language in SUPPORTED_LANGUAGES else "en"

    # During a configured launch-free window, everyone gets a capped daily
    # allowance instead of unlimited free runs — unlimited-free has real,
    # unbounded cost exposure (nothing stops scripted abuse while analyses
    # cost nothing); the cap closes that gap while staying generous. Tracked
    # on the wallet doc, not on analyses — a count, not which tickers were
    # run, so this doesn't touch the "analyses aren't linked to identity"
    # privacy commitment at all. Once the day's allowance is spent the request
    # falls through to normal billing (free credits, then wallet), it is not a
    # hard block.
    launch_free_now = wal.is_launch_free_period(LAUNCH_FREE_UNTIL, datetime.now(timezone.utc))
    launch_free_daily_ok = False
    if launch_free_now:
        launch_key = wallet_key_for(user, body.device_id)
        if not launch_key:
            raise HTTPException(status_code=400, detail="device_id is required")
        today_str = datetime.now(timezone.utc).date().isoformat()
        wallet_doc = await db.wallets.find_one({"device_id": launch_key}) or {}
        reset_date, reset_count = wal.launch_free_daily_state(
            wallet_doc.get("launch_free_daily_date", ""),
            wallet_doc.get("launch_free_daily_count", 0),
            today_str,
        )
        launch_free_daily_ok = wal.has_launch_free_daily_quota(reset_count)
        new_count = reset_count + 1 if launch_free_daily_ok else reset_count
        await db.wallets.update_one(
            {"device_id": launch_key},
            {"$set": {
                "launch_free_daily_date": reset_date,
                "launch_free_daily_count": new_count,
                "updated_at": now_iso(),
            }},
            upsert=True,
        )
    admin_bypass = is_admin(user) or launch_free_daily_ok

    used_free_credit = False
    billed = WALLET_ENFORCEMENT_ENABLED and not admin_bypass
    if billed:
        wkey = wallet_key_for(user, body.device_id)
        if not wkey:
            raise HTTPException(status_code=400, detail="device_id is required")

        cached = await latest_completed_analysis_for(symbol, language)
        cached_verdict = cached["verdict"] if cached else None
        reference_price = (cached.get("quote") or {}).get("price") if cached else None
        live_quote = None
        try:
            live_quote = await asyncio.to_thread(fetch_quote_sync, symbol)
        except Exception as e:
            logger.warning(f"pricing quote fetch failed for {symbol}: {e}")
        live_price = live_quote.get("price") if live_quote else None

        if not wal.should_charge_for_recheck(cached_verdict, live_price, reference_price):
            # Nothing has meaningfully changed since the cached verdict —
            # serve it for free. No new analysis document, no LLM calls.
            # Billing fields must describe THIS request, not the original
            # run's (which may have been another account or an admin).
            return {**cached, "served_from_cache": True, "billed": False,
                    "price_charged": None, "admin_bypass": admin_bypass,
                    "used_free_credit": False,
                    "free_credits_remaining": await get_free_credits_remaining(user)}

        # Free signup credits are spent before any money is. Only reached
        # when a fresh run is actually needed, so an unchanged re-check
        # never burns one.
        if wal.should_use_free_credit(await get_free_credits_remaining(user)) and await consume_free_credit(user):
            used_free_credit = True
            billed = False
        else:
            balance = await get_wallet_balance(wkey)
            if not wal.has_sufficient_balance(balance, "full_analysis"):
                raise HTTPException(
                    status_code=402,
                    detail=(f"Insufficient balance: need {wal.CURRENCY_SYMBOL}{wal.get_price('full_analysis'):.2f}, "
                            f"have {wal.CURRENCY_SYMBOL}{balance:.2f}"),
                )
            new_balance = wal.new_balance_after_charge(balance, "full_analysis")
            await db.wallets.update_one(
                {"device_id": wkey},
                {"$set": {"balance": new_balance, "updated_at": now_iso()}},
            )

    analysis = {
        "id": str(uuid.uuid4()),
        "symbol": symbol,
        "name": (body.name or symbol),
        "language": language,
        "status": "running",
        "messages": [],
        "quote": None,
        "verdict": None,
        "debate": None,
        "timeframes": None,
        "current_step": 0,
        "total_steps": TOTAL_STEPS,
        "error": None,
        "billed": billed,
        "price_charged": wal.get_price("full_analysis") if billed else None,
        "admin_bypass": admin_bypass if WALLET_ENFORCEMENT_ENABLED else False,
        "launch_free_active": launch_free_daily_ok if WALLET_ENFORCEMENT_ENABLED else False,
        "used_free_credit": used_free_credit,
        "free_credits_remaining": await get_free_credits_remaining(
            await db.users.find_one({"id": user["id"]}, {"_id": 0}) if user else None
        ),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.analyses.insert_one({**analysis})
    asyncio.create_task(run_analysis(analysis["id"], symbol, language))
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


@app.get("/health")
async def health():
    """Platform readiness probe. Deliberately app-level (not under /api) and
    DB-free so a slow Mongo can't make the container look dead."""
    return {"status": "ok"}



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


@app.on_event("startup")
async def migrate_legacy_wallet_field():
    """One-time: wallet rows written before the field rename carry
    `balance_usd`, which reads as $0 today. Takes the higher of the two so
    no row with a real balance can read as empty, then drops the old field."""
    try:
        migrated = 0
        async for doc in db.wallets.find({"balance_usd": {"$exists": True}}):
            merged = max(float(doc.get("balance") or 0), float(doc.get("balance_usd") or 0))
            await db.wallets.update_one(
                {"_id": doc["_id"]},
                {"$set": {"balance": merged}, "$unset": {"balance_usd": ""}},
            )
            migrated += 1
        if migrated:
            logger.info(f"migrated {migrated} legacy wallet rows (balance_usd -> balance)")
    except Exception as e:
        logger.warning(f"legacy wallet migration failed: {e}")


@app.on_event("startup")
async def ensure_payment_indexes():
    """Unique indexes are what keep a top-up from being credited twice."""
    try:
        # Payment-link records carry no order id until the customer starts
        # paying, so these must be sparse — the old non-sparse unique index
        # would reject every record after the first one missing the field.
        existing = await db.payments.index_information()
        if "razorpay_order_id_1" in existing and not existing["razorpay_order_id_1"].get("sparse"):
            await db.payments.drop_index("razorpay_order_id_1")
        await db.payments.create_index("razorpay_order_id", unique=True, sparse=True)
        await db.payments.create_index("razorpay_payment_link_id", unique=True, sparse=True)
        await db.payments.create_index("reference_id", unique=True, sparse=True)
        await db.wallet_ledger.create_index("payment_id", unique=True)
        await db.webhook_events.create_index("event_id", unique=True)
    except Exception as e:
        logger.warning(f"payment index setup failed: {e}")
    if rzp.payments_configured():
        logger.info(f"razorpay ready ({'LIVE' if rzp.is_live_mode() else 'test'} mode)")
        # Prove the keys actually authenticate. A deployed image carrying stale
        # keys otherwise looks fine until a customer taps top-up and gets a 502.
        try:
            await rzp.razorpay_request("GET", "/payments?count=1")
            logger.info("razorpay credentials authenticated")
        except rzp.RazorpayError as e:
            logger.error(f"RAZORPAY CREDENTIALS REJECTED [{e.code}]: {e.description} — top-ups will fail")
        except Exception as e:
            logger.warning(f"razorpay credential check skipped: {e}")
