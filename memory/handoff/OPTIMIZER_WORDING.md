# Portfolio optimizer: model comparison, not trade labels

Branch `feature/optimizer-wording` (from main ee24d9c).

The optimizer was the last screen with advice-style wording: per-holding
ADD / TRIM / SELL / HOLD badges in buy/sell colours under "SUGGESTED ACTIONS",
the tab subtitle "Keep, trim or sell — optimized", and an API field with share
counts to buy (`suggested_shares`, `leftover_cash`). As an unregistered app that
is SEBI risk.

## Changes
- `backend/portfolio_optimizer.py`: `classify_actions` now returns how the user's
  weight compares with the model's — `ABOVE MODEL`, `IN LINE` (±5 pts),
  `BELOW MODEL`, `NOT IN MODEL` (model weight < 1%). The field keeps its name
  `action` so installed app builds still render it. `discrete_allocation`
  (shares to buy) removed.
- `backend/routes/portfolio.py`: `/portfolio/optimize` no longer returns
  `suggested_shares` / `leftover_cash`.
- `frontend/app/(tabs)/portfolio.tsx`: "YOUR WEIGHTS VS THE MODEL", rows read
  "30.0% yours · 12.0% model", neutral badge colours, "COMPARE WITH A MODEL MIX" /
  "COMPARE WITH MODEL", stats labelled MODEL RETURN (PAST) / MODEL VOLATILITY /
  MODEL SHARPE, note "…shows how your weights differ from the model's — not what
  to buy or sell. Not investment advice." Subtitle "Your holdings, P&L and model
  comparison".
- Also fixed: P/L gain/loss text had no colour (it read `verdictColors.BUY`,
  which doesn't exist on the function); now green/red.

## Tests
`tests/test_portfolio_optimizer.py` — labels, in-line band, and a check that no
label contains BUY/SELL/ADD/TRIM/HOLD/EXIT and `discrete_allocation` is gone
(the old discrete-allocation test was removed with the feature).
`tests/test_portfolio_api.py` (live) — action values updated; asserts no share
counts in the response. Offline suites: 184 passed.

## Verify (Emergent)
Run tests/test_portfolio_optimizer.py and tests/test_portfolio_api.py (live).
In the app: Portfolio → add 3+ holdings → COMPARE WITH MODEL → screenshot the
"YOUR WEIGHTS VS THE MODEL" card (no ADD/TRIM/SELL anywhere) and the P/L
colours (green for gain, red for loss).
