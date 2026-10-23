# Refund on failed analysis

Branch `feature/analysis-refund` (from `main` 51873cf).

## Problem
`POST /api/analyze` takes payment BEFORE the multi-agent run starts (wallet
debit, a free signup credit, or a launch-free daily slot). If the run then
failed (`run_analysis` marks the analysis `status: "error"`), nothing gave it
back — the user paid ₹20 for nothing.

## Fix (backend/routes/analysis.py)
- `analyze()` records what the run took in a small in-memory `charge` dict:
  `wallet` (key, amount, currency), `free_credit` (user id) or `launch_free`
  (wallet key, day). Admin / billing-off runs have no charge.
- The run is started through `_run_and_refund()`, which awaits `run_analysis`
  and then calls `refund_if_failed()`.
- `refund_if_failed()` first marks the analysis `refunded: true` with a
  conditional update (`status == "error"` and not already refunded) — only the
  call that wins pays out, so a refund can never happen twice — then returns
  the wallet amount / free credit / launch-free slot (the slot only if it is
  still the same day). The analysis gets `refund: {kind, amount, currency}`
  (no identity on it).
- The charge info is never stored: storing it would link an account to an
  analysis, which the privacy design avoids. Limitation: if the server process
  dies mid-run, that run stays "running" and is not refunded (rare; same as
  before).

## App
`app/analysis/[id].tsx` failure box now says "₹20 has been returned to your
wallet." / "Your free analysis credit has been given back." / "This run didn't
count towards today's free analyses.", or a general line if the refund field
hasn't arrived yet.

## Tests
`backend/tests/test_analysis_refund.py` — 6 tests (refund once, never for
completed/running runs, free credit, launch-free same-day only, the wrapper,
free runs). Offline suites: 159 passed.

## Please verify (Emergent)
1. `python -m pytest tests/test_analysis_refund.py -n 0 -q` → 6 passed; plus the
   existing analysis/billing suites you normally run.
2. Live check with a TEST account and WALLET_ENFORCEMENT_ENABLED=true locally:
   give the test wallet ₹100, start an analysis on a symbol forced to fail
   (e.g. temporarily make the LLM key invalid in a local shell only, or an
   invalid-but-regex-valid ticker that breaks the pipeline), confirm the
   analysis ends `status: "error"` with `refunded: true` and the wallet is back
   to ₹100. A normal successful run must still cost ₹20. Restore everything.
