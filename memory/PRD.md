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

## Added (2026-06-16, session 4)
- **Shareable Verdict card**: a "SHARE THIS VERDICT" button in the analysis VERDICT view opens a preview modal rendering a branded card (symbol, live price + sparkline, colored BUY/SELL/HOLD block with confidence bar, thesis, date + "NOT FINANCIAL ADVICE"). Captured to PNG via `react-native-view-shot` and shared through the native sheet via `expo-sharing` (`src/components/ShareCard.tsx`). Web preview shows the card but sharing is native-only (Expo Go / device).

## Restyled (2026-06-17, session 5) — Vibrant refresh
- New colorful palette in `src/theme.ts` (indigo/violet/pink/amber/teal/lime/orange accents) + helpers `accentAt`, `PHASE_COLORS`, `CATEGORY_COLORS`, `HEADER_GRADIENT`, `CTA_GRADIENT`.
- Shared gradient `ScreenHeader` (indigo→violet→pink) across all screens; StatusBar switched to light.
- Colorful details: category chips get per-category color + dots; market cards get rotating accent top-bars; bottom tab active color per tab (blue/pink/teal); brand-blue active segmented control + chart-range chips; agent message cards get a colored left accent by sentiment/phase; history rows get a verdict/accent left bar; agent roster icons colored by team; gradient EXECUTE / RUN COMPARISON CTAs. Kept the crisp brutalist structure.

## Upgraded (2026-06-17, session 6) — Expo SDK 57
- Bumped Expo SDK 54 → **57** (react-native 0.81 → **0.86**, react 19.1 → **19.2**) via `expo install expo@^57` + `expo install --fix`, because the latest Expo Go (SDK 57) can't open SDK 54 projects. All expo-* + third-party libs (reanimated 4.5, worklets 0.10, keyboard-controller 1.21, view-shot 5.1, svg 15.15, linear-gradient 57, router 57) aligned. Web bundle + all screens verified on the app's own preview domain; lint clean.
- **Removed the "SELECT A TICKER" placeholder CTA** — the gradient EXECUTE button now only appears once a ticker is selected.
- NOTE: user must **redeploy** so production runs SDK 57 (matching their Expo Go).

## Backlog / Remaining
- **P1**: Kotak Neo integration for live India-broker quotes + (optional) order placement — needs user credentials; complex session auth.
- **P1**: Price chart range toggle (1D/1W/1M/1Y) on the quote card.
- **P2**: Watchlist of favorite tickers with quick re-run.
- **P2**: Compare two tickers side-by-side; share a verdict as an image.
- **P2**: Re-run "reflection" (learn from realized return vs prior verdict) like the original framework's decision log.

## Next Tasks
- Offer Kotak Neo wiring once the user shares credentials.
- Add watchlist + chart range toggle if requested.

## TradingView Charts (2026-06-17, session 7) — DONE
- Wired the interactive TradingView Advanced Chart into the Analysis screen **VERDICT tab** (frontend only, no backend changes).
- New files in use: `src/tv.ts` (Yahoo→TV symbol mapping + range→interval + default studies), `src/components/TradingViewChart.tsx` (WebView widget, iframe fallback on web), `src/components/VerdictLevels.tsx` (BUY/SELL entry, target, stop-loss with % deltas).
- Chart shows RSI/MACD/EMA(20)/BB studies + volume, light theme; a 1D/1W/1M/1Y range bar drives the widget interval; `VerdictLevels` sits directly beneath.
- Commodity symbols mapped to free `TVC:` index symbols (GOLD/SILVER/USOIL/UKOIL/PLATINUM) so the free widget renders without the "subscription required" popup. Verified rendering on the preview (gold analysis shows full candles + indicators).
