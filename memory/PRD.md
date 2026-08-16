# TradingAgents — Product Requirements Document

## Original Problem Statement
"Build a mobile app: https://github.com/TauricResearch/TradingAgents.git — use this GitHub to create a mobile app for trading."

TradingAgents (TauricResearch) is a multi-agent LLM framework that mirrors a real trading firm: specialized AI agents debate and produce a trading decision for a ticker. This app is the mobile incarnation of that idea.

## User Choices (from intake)
- AI model: **OpenAI GPT-5.4** (via Emergent Universal Key)
- Market data: user offered a Kotak Neo key; shipped MVP uses **Yahoo Finance public data** (free, no key, instant) so the app is fully functional now. Kotak Neo can be added later for live India-broker quotes / order execution.
- Auth: **None** — opens straight to Analyze.
- Features: Analyze a ticker · History / decision log · Live agent debate view.
- Design: "surprise me" → **Brutalist trading terminal** (light, Space Grotesk + JetBrains Mono, 0-radius, 2pt borders).

## Architecture
- **Frontend**: Expo Router (React Native), 3 bottom tabs (Analyze / History / Agents) + `analysis/[id]` detail. Custom brutalist tab bar, Phosphor icons, react-native-svg sparklines, reanimated message entrance, react-native-keyboard-controller sticky CTA. Custom fonts via expo-font.
- **Backend**: FastAPI (`/api/*`) + MongoDB (motor). Multi-agent pipeline via `emergentintegrations` LlmChat (GPT-5.4). Market data from Yahoo Finance (quote/search/chart) fetched via `asyncio.to_thread`.
- **Pipeline (12 steps)**: 4 analysts (parallel) → Bull vs Bear debate (2 rounds) → Research Manager → Trader → Risk Manager → Portfolio Manager (final JSON verdict). Runs as a background asyncio task; frontend polls `/api/analysis/{id}` for a live feed.

## User Personas
- **Retail investor / trader** wanting a fast, opinionated second look at a ticker.
- **Curious learner** who enjoys watching AI agents reason and debate a trade.

## Core Requirements (static)
- Enter/search any Yahoo-covered ticker (US, HK, India .NS, crypto BTC-USD, etc.).
- Run a multi-agent analysis and stream the agents' reports + bull/bear debate live.
- Deliver a clear BUY/SELL/HOLD verdict with confidence, target, stop, horizon, thesis and key risks.
- Persist every run in a decision log (history) with delete.

## Implemented (2026-06-16)
- Backend endpoints: `GET /`, `GET /search`, `GET /quote/{symbol}`, `GET /trending`, `POST /analyze`, `GET /analysis/{id}`, `GET /history`, `DELETE /analysis/{id}`.
- Full 12-step GPT-5.4 multi-agent pipeline with structured verdict parsing + graceful fallbacks.
- Analyze screen: search (debounced) + 2-col trending grid w/ sparklines + sticky EXECUTE CTA.
- Analysis screen: LIVE DEBATE thread (phase dividers, role tags, bull/bear signals, thinking footer) + VERDICT tab (big color block, confidence bar, stat grid, thesis, risks, per-phase transcript accordions). Haptics on new message / verdict.
- History tab (list, pills, delete, empty state) and Agents tab (pipeline + 10-agent roster + disclaimer).
- Verified: 12/12 backend pytest pass (incl. full async analysis to completion); Analyze/Debate/Verdict/History/Agents screens verified via screenshots.

## Added (2026-06-16, session 2)
- **Market categories**: `GET /api/markets/{category}` for `trending | stocks | crypto | commodities` (90s cache, 404 on unknown). Kept `/api/trending`.
- **Commodities**: Gold, Silver, Crude (WTI), Brent, Natural Gas, Copper, Platinum, Wheat via Yahoo futures tickers (`GC=F` etc.) with friendly display names.
- **Analyze screen category chip row** (horizontal, brutalist, no-wrap) switching the browse grid between the four categories; verified commodity analysis completes end-to-end (asset-class-aware agent context).
- User asked for "live rates from Google"; after a feasibility check the user chose to keep the existing reliable live feed (Google request dropped).

## Added (2026-06-16, session 3)
- **Watchlist**: local (storage-backed) watchlist with star toggle on the Analyze preview + analysis header; a "WATCHLIST · TAP TO RE-RUN" row for one-tap re-analysis; persists across reloads (`src/watchlist.tsx`).
- **Compare**: `app/compare.tsx` — pick two tickers, run both analyses in parallel, side-by-side verdict columns + a "DESK LEANS" conviction callout. Entry via COMPARE button in the Analyze header.
- **Chart ranges**: `GET /api/chart/{symbol}?range=1D|1W|1M|1Y` (fallback 1M) + range chips on `QuoteCard` (used in Analyze preview and analysis detail).
- **Round-Table Debate**: extra step in the pipeline generates a structured `debate` (Bull/Bear/Fundamentals arguments + agreements/disagreements + recommendation), shown as a "ROUND TABLE DEBATE" section in the VERDICT view BELOW the existing single-verdict flow (kept intact). Backend stores `analysis.debate`.
- Verified: 13/13 new backend pytest pass; all four features validated by the testing agent + screenshots.

## Backlog / Remaining
- **P1**: Kotak Neo integration for live India-broker quotes + (optional) order placement — needs user credentials; complex session auth.
- **P1**: Price chart range toggle (1D/1W/1M/1Y) on the quote card.
- **P2**: Watchlist of favorite tickers with quick re-run.
- **P2**: Compare two tickers side-by-side; share a verdict as an image.
- **P2**: Re-run "reflection" (learn from realized return vs prior verdict) like the original framework's decision log.

## Next Tasks
- Offer Kotak Neo wiring once the user shares credentials.
- Add watchlist + chart range toggle if requested.
