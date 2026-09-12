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

## News · Alerts · Fear & Greed (2026-06-17, session 7) — DONE
- **News feed**: new backend `GET /api/news/{symbol}` (Yahoo Finance search, no key) + `NewsList` component showing headline, thumbnail, publisher and relative time in the VERDICT tab; tapping opens the article via `Linking`.
- **Fear & Greed gauge**: `src/sentiment.ts` derives a 0-100 score from the committee verdict + confidence, the bull/bear balance of agent signals, and 1-day momentum. `FearGreedGauge` renders a 5-zone bar (red→green) with a marker + label under the verdict block. No extra data source.
- **Price alerts (in-app, Expo-Go friendly)**: `src/alerts.tsx` store (AsyncStorage) + new **ALERTS tab**. Tap the bell on a TARGET / STOP LOSS level to set/clear an alert; alerts persist and are evaluated whenever a fresh live price arrives (analysis poll + Alerts-tab pull-to-refresh). On a crossing the user gets a haptic + in-app dialog "ping" (no push notifications / background work). Alerts tab shows live "now" price, delete, and clear-fired.
- Verified end-to-end on the preview: gold analysis showed gauge 88/Extreme Greed, both alert bells active, alerts listed with live prices, headlines with thumbnails.

## Next Tasks
- Offer Kotak Neo wiring once the user shares credentials.
- Add watchlist + chart range toggle if requested.

## Multi-Timeframe Verdicts — PHASE 8 (2026-06-17, session 7) — DONE
- **First pipeline-extending change**: new PHASE 8 "Multi-Horizon Desk" LLM call after the round-table debate (TOTAL_STEPS 12→13). Produces a separate BUY/SELL/HOLD call for short (1-2wk) / medium (1-3mo) / long (6-12mo) horizons, each with confidence + optional target/stop + thesis. Never mutates the primary verdict/debate/grounding. `parse_timeframes()` requires all three horizons or returns None → `fallback_timeframes()` reuses primary decision/confidence, null levels, explicit "unavailable" note (never invents prices). New additive `analysis.timeframes` field (initial doc + $set). Only the genuinely-new pieces were applied — the spec's raw diff also referenced an unrelated `technical_factors.py` (absent here) which was intentionally skipped.
- **Frontend**: `TimeframesCard` (MULTI-HORIZON VIEW) under PositionSizer — three tabs colored by each horizon's decision; body shows decision badge + confidence + thesis + TARGET/STOP (or fallback note). New `Timeframes`/`TimeframeCall` types + `Analysis.timeframes`.
- Updated 3 stale tests (12→13 steps/messages). Verified by testing agent (iteration_8): 6 unit + 63 full + 9 e2e pass; live AAPL split short HOLD / med BUY / long BUY; card renders + tab-switches; no regressions.

## Portfolio Tab — Fix Pass (2026-06-17, session 7) — DONE
- Replaced `portfolio.tsx` in full per `PORTFOLIO_TAB_FIX.md`: (1) symbol search-as-you-type in ADD HOLDING (api.search dropdown, tap fills field), (2) single-holding hint "Add at least one more holding to run the optimizer.", (3) "ANALYZE MISSING (N) & RE-RUN" button that runs the existing /api/analyze→poll flow per missing symbol ("ANALYZING {symbol}…") then auto re-optimizes. Frontend-only; `git diff` outside this file empty; lint clean.
- Verified by testing agent (iteration_7): all 6 criteria pass, incl. real PLBY/BBAI analyze-missing → auto re-run.
- Open minor findings (in spec-verbatim code, NOT changed pending user OK): (a) search dropdown re-opens ~300ms after tap-select as the debounce effect refires; (b) ADD HOLDING form row clips on <400px until a field is focused; (c) analyzeMissing polling has no timeout guard.

## Portfolio Tab (PyPortfolioOpt) — (2026-06-17, session 7) — DONE
- **Additive**: new `backend/portfolio_optimizer.py` (HRP / max-Sharpe / min-vol via PyPortfolioOpt 1.6.0; Black-Litterman blending cached agent verdicts as Idzorek views; discrete allocation; ADD/HOLD/TRIM/SELL classifier) + `POST /api/portfolio/optimize` (reads latest completed verdict per symbol, never runs the pipeline). server.py change = import line + one block before Routes (0 removed lines). New dep `pyportfolioopt`. Tests: 8 module tests + 51 full suite pass.
- **Frontend**: new **PORTFOLIO** tab (lime wallet) — add holdings (manual + watchlist quick-add, persisted via storage util), objective picker, "Use agent views" toggle, Run Optimizer → sorted SELL/TRIM/ADD/HOLD list with current→suggested weights + exp return/vol/Sharpe + notes. Types/method added to api.ts; tab registered in _layout.tsx.
- Verified by testing agent (iteration_6): HRP+max_sharpe weights sum ~1.0, actions valid, Black-Litterman used/missing-view populated after seeding an AAPL analysis, duplicate→400, single→422 (expected), persistence across reload, no regressions.
- Known minor (spec-verbatim, not changed): ADD HOLDING form row clips on <400px screens until a field is focused — offered as optional follow-up.

## Position Sizer — Part 1d (2026-06-17, session 7) — DONE
- **Frontend-only, additive**: new `PositionSizer.tsx` under the verdict. Deterministic sizing `qty = min(floor(capital×risk%/|price−stop|), floor(capital×maxPos%/price))` — LLM never involved. Inputs Capital/Risk%/MaxPos% (defaults 100000/1/20) persist via the `storage` util (keys `sizer:*`). Shows qty, notional, at-risk (+% of capital), to-target, R:R, and a note stating which limit clamped. Disabled with a reason for HOLD, `grounding.status==='failed'`, no price, or no stop. Header "RISK DISPOSES · NOT ADVICE".
- Only edits: new component + 2 lines in `app/analysis/[id].tsx` (import + `<PositionSizer/>` after GroundingBadge). `git diff backend/` empty; lint clean; worked examples verified (100000/1/20 → 666 risk-limited; price2500/stop2480 → 8 position-cap-limited).
- Verified by testing agent (iteration_5): live NVDA SELL sized to 91 shares (cap-limited), live edits recompute, inputs persist across reload, HOLD shows disabled reason, no regressions.

## Verdict Grounding Gate — Part 1c (2026-06-17, session 7) — DONE
- **Additive** `ground_verdict(verdict, quote)` in server.py checks the agents' target/stop against the live quote (direction vs price, magnitude 0.5x–2x, within 25% of 52w range, risk/reward ≥1, levels present) and stores a `grounding` report `{status, checks[], evidence}` in the existing completion `$set`. Status: failed / warning / grounded / unverified. Verdict is never mutated. Pipeline/prompts/parse_verdict/parse_debate/build_context untouched; `git diff server.py` = new function + the one `$set` line only.
- **Frontend**: `GroundingBadge` under the verdict block (LEVELS VERIFIED / · CHECK / REJECTED / UNVERIFIED, tap to expand the individual checks + evidence). Chart entry/target/stop lines are suppressed when `grounding.status==='failed'` (`chartLevels` gate); the VerdictLevels strip + stats stay visible. New api types `Grounding`/`GroundingCheck` + `Analysis.grounding`.
- Verified by testing agent (iteration_4): 9 grounding unit tests + 6 e2e + 37 full suite pass; badge renders/expands on a live HOLD (warning) analysis; existing endpoints/features intact.

## News relevance fix (2026-06-17, session 7) — DONE
- **Bug**: the news feed showed generic filler (Trump statues, Cardi B) for suffixed/symbolic tickers (RELIANCE.NS, BTC-USD) because Yahoo search was queried with the raw ticker and fell back to trending news.
- **Fix** (`fetch_news_sync` in server.py): query Yahoo by a smart per-asset term (`_news_query`: company name / coin name / index name / suffix-stripped symbol) and then keep only stories whose `relatedTickers` include the exact researched symbol. Result: Apple→Apple, Tesla→Tesla, BTC-USD→Bitcoin, GC=F→gold, ^NSEI→Nifty/India; when Yahoo genuinely has no coverage the feed is honestly short/empty instead of junk. Verified via curl across 6 symbols + frontend render.

## NSE/BSE in-app OHLC fallback chart — Part 1b (2026-06-17, session 7) — DONE
- **New additive backend endpoint** `GET /api/ohlc/{symbol}` (appended after `/chart`; `fetch_ohlc_sync`) returning `{symbol, range, interval, currency, bars[]}` with time/open/high/low/close/volume. No pipeline/prompt/existing-endpoint changes; `git diff server.py` is purely additive. New tests `backend/tests/test_ohlc.py` (3) pass; full suite 28 pass.
- **Frontend**: `TradingViewChart` now routes via `widgetSupports()` — `.NS` / `.BO` / `^NSEI` / `^BSESN` fall back to the new `LightweightChart` (TradingView open-source Lightweight Charts in a WebView/iframe, no new deps): candles + volume, EMA 20/50, Bollinger 20, RSI 14 pane, MACD 12/26/9 pane, own 1D/1W/1M/1Y chips, and the committee's entry/target/stop drawn as price lines. Non-Indian symbols keep the TradingView widget unchanged. Analysis screen passes `levels`+`livePrice` and hides its outer range bar for fallback symbols. Verified end-to-end by testing agent (iteration_3): canvases paint, widget branch untouched.

## TradingView Charts (2026-06-17, session 7) — DONE
- Wired the interactive TradingView Advanced Chart into the Analysis screen **VERDICT tab** (frontend only, no backend changes).
- New files in use: `src/tv.ts` (Yahoo→TV symbol mapping + range→interval + default studies), `src/components/TradingViewChart.tsx` (WebView widget, iframe fallback on web), `src/components/VerdictLevels.tsx` (BUY/SELL entry, target, stop-loss with % deltas).
- Chart shows RSI/MACD/EMA(20)/BB studies + volume, light theme; a 1D/1W/1M/1Y range bar drives the widget interval; `VerdictLevels` sits directly beneath.
- Commodity symbols mapped to free `TVC:` index symbols (GOLD/SILVER/USOIL/UKOIL/PLATINUM) so the free widget renders without the "subscription required" popup. Verified rendering on the preview (gold analysis shows full candles + indicators).
