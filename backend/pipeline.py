"""The multi-agent analysis pipeline.

The agents, their system prompts, the parsers that turn their prose into
structured verdicts, the grounding gate that sanity-checks a verdict against
the live quote, and run_analysis() which drives all of it and streams each
message into the analysis document as it lands.
"""
import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from emergentintegrations.llm.chat import LlmChat, UserMessage

import fundamentals as fund
from core import EMERGENT_LLM_KEY, MODEL_NAME, MODEL_PROVIDER, db, logger, now_iso
from market_data import fetch_fundamentals_sync, fetch_quote_sync


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
SUPPORTED_LANGUAGES = {"en": "English", "hi": "Hindi", "es": "Spanish", "zh": "Mandarin Chinese", "ar": "Arabic"}


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

