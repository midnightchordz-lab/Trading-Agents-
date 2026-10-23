# Portfolio Health Check

Branch `feature/health-check` (built on `feature/chart-analysis`, so it carries
scanners + chart analysis too — deploy from this branch once verified).

## What it is
A free, no-AI report on what a portfolio **is** over the past year. It never
says what to buy, sell or change (SEBI): every line is an observation, and the
efficient frontier is shown as points only, never the weights behind them.

| Section | What it shows | Idea from |
|---|---|---|
| Diversification score 0–100 | 4 visible parts: real number of bets (effective holdings), how independently holdings move (weighted avg correlation), sector spread, asset classes | Yale / MPT |
| What we see | Plain-language observations (tested to contain no buy/sell/should wording) | — |
| Market vs your picks | Beta, R², correlation vs Nifty 50 (INR) or S&P 500 (USD) | Mladina & Coyle |
| Return per unit of risk | Yearly return, volatility, Sharpe, worst fall — you vs benchmark | MPT |
| Efficient frontier | Curve from the user's OWN holdings + their dot; "gap at same risk" | MPT |
| Move together | Clusters of holdings with pairwise correlation ≥ 0.7 | Yale |
| Mix vs your target | User sets their own target % per asset class (stored on device); drift ≥ 5 pts flagged | Yale rebalancing |
| Sectors | From Yahoo assetProfile (cached 7 days) | — |
| Liquidity | Avg daily traded value (20 sessions); flags thin stocks (< ₹1 Cr / $1M a day) and positions > 10% of a day's trading | Yale (illiquidity) |

## Files
- `backend/portfolio_health.py` — pure calculations + `build_report()`
- `backend/routes/health.py` — `POST /api/portfolio/health` (session required; limits `RL_HEALTH_CHECK_PER_MIN`=3 per user, `RL_HEALTH_CHECK_GLOBAL_PER_MIN`=20)
- `backend/market_data.py` — `fetch_dated_history_sync`, `fetch_sector_sync`
- `backend/limits.py`, `backend/server.py` — limiter + router registration
- `backend/tests/test_portfolio_health.py` — 21 tests (offline, synthetic data)
- `frontend/app/health.tsx` — the screen; `frontend/app/(tabs)/portfolio.tsx` — "HEALTH CHECK" button (`testID="open-health"`)
- `frontend/src/api.ts` — `portfolioHealth`, `HealthReport` type; firebase event `health_check`

## Request
```json
POST /api/portfolio/health   (Authorization: Bearer <session>)
{"holdings":[{"symbol":"TCS.NS","quantity":10},{"symbol":"AAPL","quantity":2}],
 "cash":5000,
 "targets":{"in_equity":70,"us_equity":20,"cash":10}}
```
Target keys: in_equity, us_equity, other_equity, debt, gold, crypto, cash (must total 100, or omit).

## Details worth knowing
- Base currency: INR if any holding is Indian, else USD. Foreign holdings are converted with Yahoo FX history (`USDINR=X`), carrying the last rate over FX holidays. London prices in pence (GBp) are converted to pounds.
- Returns are lined up by each exchange's local DATE and inner-joined (no invented 0% days on holidays).
- Uses today's weights over the whole year ("how what you own now behaved"), since we don't know purchase dates.
- Sharpe risk-free rate: 6.5% INR / 4% USD, overridable with `HEALTH_RF_INR` / `HEALTH_RF_USD`.
- Needs ≥ 60 shared trading days for risk/market/frontier figures; otherwise says so.
- Free: no LLM call. Not charged through the wallet.
- Nothing is stored server-side.

## Please verify (Emergent)
1. `cd backend && python -m pytest tests/test_portfolio_health.py -n 0 -q` → 21 passed. Also re-run test_chart_analysis.py and test_scanners.py.
2. Live: run the request above with a real session and a mixed portfolio, e.g. HDFCBANK.NS, ICICIBANK.NS, KOTAKBANK.NS, TCS.NS, INFY.NS, GOLDBEES.NS, AAPL, BTC-USD. Check: base INR; the three banks appear as one cluster; sectors filled for the stocks; beta/R² vs Nifty 50 present; frontier has ~10+ points; no holding skipped.
3. A US-only portfolio (AAPL, MSFT, GLD, TLT) → base USD, benchmark S&P 500, GLD in Gold, TLT in Debt & bonds.
4. Bad input → 400 (duplicate symbol; targets totalling 40).
5. App: Portfolio tab → HEALTH CHECK → RUN HEALTH CHECK; set a target mix and re-run; check the frontier chart renders on Android and web.
6. Report the full `observations` list from the live run — no line should contain buy / sell / should / add / trim.

Do not force-push or rewrite history.
