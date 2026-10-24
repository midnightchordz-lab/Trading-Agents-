"""When the AI service is down every agent call "succeeds" with fallback text.
The run must then end as an error (and so be refunded), not as a completed
report of fallbacks; one agent hiccup must not sink an otherwise good run.
Runs the real pipeline offline with the LLM, market data and database faked."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pipeline  # noqa: E402
from routes import analysis as an  # noqa: E402

PM_JSON = json.dumps({"decision": "BULLISH", "confidence": 62, "summary": "Steady.", "target_price": 110,
                      "stop_loss": 90, "time_horizon": "1-3 months"})


class FakeAnalyses:
    def __init__(self):
        self.docs = {"a1": {"id": "a1", "status": "running", "messages": []}}

    async def update_one(self, flt, upd):
        doc = self.docs.get(flt["id"])
        if not doc or any(doc.get(k) != v for k, v in flt.items() if not isinstance(v, dict)) \
                or ("refunded" in flt and doc.get("refunded") is True):
            return SimpleNamespace(modified_count=0)
        for k, v in upd.get("$push", {}).items():
            doc.setdefault(k, []).append(v)
        doc.update(upd.get("$set", {}))
        return SimpleNamespace(modified_count=1)


def fake_llm(behaviour):
    """behaviour(system_message) -> text, or raises."""
    class Chat:
        def __init__(self, api_key, session_id, system_message):
            self.sys = system_message

        def with_model(self, *a):
            return self

        async def send_message(self, msg):
            return behaviour(self.sys)
    return Chat


@pytest.fixture
def env(monkeypatch):
    analyses = FakeAnalyses()
    wallets = SimpleNamespace(docs={"user:u1": {"device_id": "user:u1", "balance": 80.0}})

    async def wallet_update(flt, upd):
        d = wallets.docs[flt["device_id"]]
        for k, v in upd.get("$inc", {}).items():
            d[k] = d.get(k, 0) + v
        return SimpleNamespace(modified_count=1)
    wallets.update_one = wallet_update
    db = SimpleNamespace(analyses=analyses, wallets=wallets)
    monkeypatch.setattr(pipeline, "db", db)
    monkeypatch.setattr(an, "db", db)

    def offline(*a, **k):
        raise RuntimeError("offline")
    for name in ("fetch_quote_sync", "fetch_fundamentals_sync", "fetch_daily_bars_sync"):
        monkeypatch.setattr(pipeline, name, offline)
    return SimpleNamespace(db=db, monkeypatch=monkeypatch)


def run(env, behaviour):
    env.monkeypatch.setattr(pipeline, "LlmChat", fake_llm(behaviour))
    asyncio.run(pipeline.run_analysis("a1", "RELIANCE.NS"))
    return env.db.analyses.docs["a1"]


def healthy(sys_msg):
    return PM_JSON if sys_msg.startswith(pipeline.PM_SYS[:40]) else "Fine. SIGNAL: NEUTRAL"


def test_service_down_ends_as_an_error_before_the_extra_calls(env):
    calls = []

    def down(sys_msg):
        calls.append(sys_msg)
        raise ConnectionError("401 invalid key")
    doc = run(env, down)
    assert doc["status"] == "error" and "couldn't be reached" in doc["error"]
    assert doc.get("verdict") is None
    # Analysts (4) + bull/bear (4) + research manager, trader, risk, PM = 12; the
    # round-table and timeframe calls are never made.
    assert len(calls) == 12


def test_a_failed_final_verdict_alone_fails_the_run(env):
    def pm_breaks(sys_msg):
        if sys_msg.startswith(pipeline.PM_SYS[:40]):
            raise TimeoutError("pm timed out")
        return "Fine. SIGNAL: NEUTRAL"
    assert run(env, pm_breaks)["status"] == "error"


def test_one_agent_hiccup_still_completes(env):
    def one_bad(sys_msg):
        if sys_msg.startswith(pipeline.NEWS_SYS[:40]):
            raise ConnectionError("blip")
        return healthy(sys_msg)
    doc = run(env, one_bad)
    assert doc["status"] == "completed" and doc["verdict"]["decision"] == "BUY"


def test_empty_answers_count_as_failures(env):
    assert run(env, lambda s: "")["status"] == "error"


def test_a_dead_desk_is_refunded_end_to_end(env):
    env.monkeypatch.setattr(pipeline, "LlmChat", fake_llm(lambda s: (_ for _ in ()).throw(ConnectionError("down"))))
    charge = {"kind": "wallet", "wallet_key": "user:u1", "amount": 20.0, "currency": "INR"}
    asyncio.run(an._run_and_refund("a1", "RELIANCE.NS", "en", None, charge))
    assert env.db.analyses.docs["a1"]["refunded"] is True
    assert env.db.wallets.docs["user:u1"]["balance"] == 100.0


def test_threshold():
    assert pipeline.desk_unavailable({"calls": 12, "failed": 6}, False)
    assert not pipeline.desk_unavailable({"calls": 12, "failed": 5}, False)
    assert pipeline.desk_unavailable({"calls": 12, "failed": 1}, True)
    assert not pipeline.desk_unavailable({"calls": 0, "failed": 0}, False)
