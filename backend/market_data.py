"""Live market data feeds and headline sentiment.

Everything that talks to Yahoo Finance lives here: quotes, charts, OHLC
candles, symbol search, fundamentals and news, plus the sentiment tag applied
to each headline. Pure data in, plain dicts out — no routing, no database, so
the pipeline and the read-only market endpoints can share one implementation.
"""
import asyncio
import json
import re
import time
import uuid
from typing import Optional

import requests
from emergentintegrations.llm.chat import LlmChat, UserMessage
from fastapi import HTTPException

import fundamentals as fund
from core import EMERGENT_LLM_KEY, MODEL_NAME, MODEL_PROVIDER, logger


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


