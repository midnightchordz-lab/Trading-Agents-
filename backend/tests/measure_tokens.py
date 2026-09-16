"""Measure real per-analysis token usage by replaying a completed analysis's
transcript through the exact prompt shapes the pipeline builds."""
import asyncio
import os
import sys

import tiktoken
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
import market_data as MD  # noqa: E402
import pipeline as S  # noqa: E402

ENC = tiktoken.get_encoding("o200k_base")


def n(t: str) -> int:
    return len(ENC.encode(t or ""))


async def main(symbol="NVDA"):
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "test_database")]
    doc = await db.analyses.find_one({"symbol": symbol, "status": "completed"}, sort=[("created_at", -1)])
    if not doc:
        print("no completed analysis found")
        return
    msgs = {m["tag"]: m["content"] for m in doc.get("messages", [])}
    ctx = S.build_context(doc["symbol"], doc.get("quote"))
    lang = S.language_directive(doc.get("language") or "en")

    analysts = [(t, msgs.get(t, "")) for t in
                ("TECHNICAL_ANALYST", "FUNDAMENTALS_ANALYST", "SENTIMENT_ANALYST", "NEWS_ANALYST")]
    analyst_summary = "\n\n".join(f"### {t}\n{c}" for t, c in analysts)
    bulls = [m["content"] for m in doc["messages"] if m["tag"] == "BULL_RESEARCHER"]
    bears = [m["content"] for m in doc["messages"] if m["tag"] == "BEAR_RESEARCHER"]
    debate_log = []
    for i in range(len(bulls)):
        debate_log.append(f"BULL (r{i+1}): {bulls[i]}")
        if i < len(bears):
            debate_log.append(f"BEAR (r{i+1}): {bears[i]}")
    debate_text = "\n\n".join(debate_log)
    rm = msgs.get("RESEARCH_MANAGER", "")
    tr = msgs.get("TRADER", "")
    rk = msgs.get("RISK_MANAGER", "")
    full = (f"{analyst_summary}\n\nDEBATE:\n{debate_text}\n\nRESEARCH MANAGER:\n{rm}"
            f"\n\nTRADER:\n{tr}\n\nRISK MANAGER:\n{rk}")
    v = doc.get("verdict") or {}

    calls = []
    for name, sysmsg in (("Technical Analyst", S.TECH_SYS), ("Fundamentals Analyst", S.FUND_SYS),
                         ("Sentiment Analyst", S.SENT_SYS), ("News Analyst", S.NEWS_SYS)):
        calls.append((name, sysmsg + lang, f"{ctx}\n\nProvide your {name} briefing for {doc['symbol']}."))
    for rnd in (1, 2):
        bu = f"{ctx}\n\nANALYST REPORTS:\n{analyst_summary}\n\n"
        if rnd == 2:
            bu += f"The Bear just argued:\n{bears[0]}\n\nRebut the bear and "
        bu += f"make the BULLISH case for {doc['symbol']} (round {rnd})."
        calls.append((f"Bull r{rnd}", S.BULL_SYS + lang, bu))
        calls.append((f"Bear r{rnd}", S.BEAR_SYS + lang,
                      f"{ctx}\n\nANALYST REPORTS:\n{analyst_summary}\n\nThe Bull just argued:\n"
                      f"{bulls[rnd-1] if len(bulls) >= rnd else ''}\n\nRebut the bull and make the "
                      f"BEARISH case for {doc['symbol']} (round {rnd})."))
    calls.append(("Research Manager", S.RM_SYS + lang,
                  f"{ctx}\n\nANALYST REPORTS:\n{analyst_summary}\n\nDEBATE:\n{debate_text}\n\n"
                  f"Judge the debate and give the recommended stance for {doc['symbol']}."))
    calls.append(("Trader", S.TRADER_SYS + lang,
                  f"{ctx}\n\nANALYST REPORTS:\n{analyst_summary}\n\nDEBATE VERDICT:\n{rm}\n\n"
                  f"Propose a concrete trade plan for {doc['symbol']}."))
    calls.append(("Risk Manager", S.RISK_SYS + lang,
                  f"{ctx}\n\nTRADE PLAN:\n{tr}\n\nDEBATE VERDICT:\n{rm}\n\n"
                  f"Stress-test the trade and give your risk ruling for {doc['symbol']}."))
    calls.append(("Portfolio Manager", S.PM_SYS + lang,
                  f"{ctx}\n\nFULL DESK TRANSCRIPT:\n{full}\n\nMake the FINAL decision for "
                  f"{doc['symbol']}. Output ONLY the JSON object."))
    calls.append(("Round-table", S.DEBATE_SYS + lang,
                  f"{ctx}\n\nDESK TRANSCRIPT:\n{full}\n\nFINAL VERDICT: {v.get('decision')} "
                  f"({v.get('confidence')}%). {v.get('summary')}\n\nProduce the round-table JSON for {doc['symbol']}."))
    calls.append(("Multi-Horizon Desk", S.TIMEFRAME_SYS + lang,
                  f"{ctx}\n\nDESK TRANSCRIPT:\n{full}\n\nPRIMARY VERDICT: {v.get('decision')} "
                  f"({v.get('confidence')}%), target {v.get('target_price')}, stop {v.get('stop_loss')}. "
                  f"{v.get('summary')}\n\nProduce the three-horizon JSON for {doc['symbol']}."))

    print(f"symbol={doc['symbol']}  lang={doc.get('language')}  ctx_tokens={n(ctx)}")
    print(f"{'call':22} {'system':>8} {'user':>8} {'input':>8}")
    tot_in = 0
    for name, sysmsg, user in calls:
        si, ui = n(sysmsg), n(user)
        tot_in += si + ui
        print(f"{name:22} {si:>8} {ui:>8} {si+ui:>8}")

    outs = [n(m["content"]) for m in doc.get("messages", [])]
    # JSON-producing calls (PM / round-table / horizons) aren't stored verbatim; estimate
    json_out = 200 + 320 + 260
    tot_out = sum(outs) + json_out
    print(f"\nTOTAL CALLS: {len(calls)}")
    print(f"TOTAL INPUT TOKENS : {tot_in}")
    print(f"TOTAL OUTPUT TOKENS: ~{tot_out} (stored messages {sum(outs)} + ~{json_out} for 3 JSON calls)")
    print(f"AVG INPUT / CALL   : {tot_in // len(calls)}")

    # news sentiment call
    items = await asyncio.to_thread(MD.fetch_news_sync, doc["symbol"])
    listing = "\n".join(f"{i+1}. {x['title']}" for i, x in enumerate(items))
    ns_user = f"Asset: {doc['symbol']}\nHeadlines:\n{listing}\n\nReturn {len(items)} labels as a JSON array."
    print(f"\nNEWS SENTIMENT CALL: input {n(MD.NEWS_SENTIMENT_SYS) + n(ns_user)} tokens "
          f"({len(items)} headlines), output ~{len(items) * 4} tokens")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "NVDA"))
