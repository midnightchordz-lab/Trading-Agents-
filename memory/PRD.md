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

## Redesign polish + Multi-language i18n (2026-06-17, session 7) — DONE
- **Part A**: QuoteCard 52-week range bar (marker between 52w low/high; hidden gracefully if bounds absent) + category-chip right-edge LinearGradient fade on Analyze.
- **Part B i18n** (English/Hindi/Spanish/Mandarin): backend `language_directive(lang)` appended to all 9 agent system prompts when lang!=en (returns '' for en → English byte-identical); `AnalyzeRequest.language` threaded through `run_analysis`. Frontend i18next+react-i18next+expo-localization; `LanguagePicker` in Agents tab (persists via `settings:language`, device-locale default); translated tab labels, Analyze strings, QuoteCard 52w labels, VerdictBadge confidence. JSON keys + BUY/SELL/HOLD enums always English.
- Verified by testing agent (iteration_9): 10 i18n unit + 82 full + 2 live e2e (Hindi run keeps English keys/enums, translates summary/thesis to Devanagari); live UI switch + persistence + revert; Part A visuals render; no regressions. server.py diff = only the B1a-d edits.

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

## Login gate + wallet pricing + Razorpay payments (2026-06-18, session 8) — DONE
- **Login required** (`AUTH_REQUIRED = true` in `app/_layout.tsx`, `AUTH_REQUIRED_ENABLED=true` server-side). Email OTP (Emergent-managed Resend), SMS OTP (Twilio, live creds), and Google sign-in via Emergent-managed auth (`POST /api/auth/session` exchanges the one-time session_id for our own pyjwt token). Apple still 501. Session token stored in the device Keychain (`storage.secureSet`). Codes: bcrypt-hashed, 5-min TTL, 5/hour rate limit, max 5 attempts. `AUTH_DEBUG_RETURN_OTP=false` (flip to true to read codes during testing).
- **Protected endpoints**: `/analyze`, `/wallet/balance`, `/wallet/topup`, `/pay/order`, `/pay/status` (401 without Bearer). Market data stays public.
- **Wallet in USD** (was briefly INR; Razorpay charges in USD — the account accepts USD orders), keyed to the account (`user:<id>`), merged from the anonymous device wallet on first login. Prices: analysis $0.25, compare $0.39, portfolio $0.05. Re-check is free unless price moved >1.5% or crossed the verdict's target/stop (`wallet.should_charge_for_recheck`). `WALLET_ENFORCEMENT_ENABLED=true` — pricing is **LIVE**. The free re-check only reuses a cached run in the SAME language.
- **Razorpay live payments** (`razorpay_pay.py`): `/api/pay/order` (amount validated against `wallet.TOPUP_PACKS` = $5/$10/$25) → backend-hosted `/api/pay/checkout/{order_id}` (Razorpay Standard Checkout; WebView on native, popup on web) → `/api/pay/callback` (HMAC signature + payment re-fetched from Razorpay) and `/api/pay/webhook` (raw-body HMAC, event-id dedupe). Credits go through `credit_wallet_once` guarded by a unique `wallet_ledger.payment_id` index, so a double credit is impossible. `/api/pay/status/{order_id}` is polled by the app and self-heals an abandoned WebView by asking Razorpay directly. `RAZORPAY_WEBHOOK_SECRET` is empty → webhooks are rejected until the user sets it (callback + status polling cover crediting meanwhile).
- Demo free top-up (`/api/wallet/topup`) is now **disabled** unless `ALLOW_DEMO_TOPUP=true`.
- UI: terminal-themed login screen, dark `ScreenHeader` + tab bar (`TERMINAL` tokens in theme.ts), `WalletCard` (balance, ₹ packs, Razorpay checkout), `WalletBalanceChip` in the Analyze header (hidden while pricing is off), `AccountCard` (identity + two-tap sign out), low-balance gate on the Execute button, news sentiment badges, welcome email on first sign-up, TradingAgents paper citation on the Agents tab.
- Tests: 135 passing (`backend/tests/`, incl. test_auth, test_wallet, test_mailer, test_sms, test_razorpay, test_news_sentiment).

## Admin bypass (2026-06-18)
- `ADMIN_PHONES` in backend/.env (currently `+918446307145`). `server.is_admin(user)` →
  `wallet.is_admin_phone(phone, ADMIN_PHONES)`. Admins: `/analyze` skips wallet enforcement
  entirely (no charge, no 402, always a fresh run — no cached free re-check), and
  `/api/wallet/balance` reports `enforcement_enabled: false, is_admin: true` so the frontend
  gate and balance chip hide themselves with no client-side admin logic.

## Free trial credits (2026-06-18)
- 10 free analyses per new account. `wallet.FREE_CREDITS_ON_SIGNUP=10`, `wallet.should_use_free_credit()`,
  `server.get_free_credits_remaining()` (backfills legacy accounts) and `server.consume_free_credit()`
  (atomic `$inc -1` guarded by `free_credits_remaining > 0`, so concurrent runs can't double-spend the last one).
- Precedence in /analyze: admin bypass -> free cached re-check -> free credit -> wallet charge -> 402.
- `/api/wallet/balance` adds `free_credits_remaining` and reports `enforcement_enabled: false` while credits
  remain, so the frontend paywall/chip hide themselves with no client-side logic. /analyze response adds
  `used_free_credit` and `free_credits_remaining`.

## Chart overlay + alert history + localized verdict card (2026-06-19, session 9) — DONE
- **Chart overlay**: `TimeframesCard` is now a controlled component (`active` / `onChange`); the analysis
  screen owns the selected horizon and derives the chart's levels from it, so switching the MULTI-HORIZON
  tabs redraws TARGET / STOP price lines on the candles (titles prefixed with the horizon, e.g.
  `1-3 MONTHS TARGET`; chart header reads `CHART · <HORIZON> LEVELS`). Horizon levels fall back to the
  primary verdict's when a horizon has none; levels are suppressed when grounding failed.
  Because the free TradingView widget can't draw price lines, `TradingViewChart` gained
  `preferOwnChart` + `levelsLabel`: widget-supported symbols show a `LEVELS · <HORIZON>` / `TRADINGVIEW`
  toggle (testIDs `chart-mode-levels`, `chart-mode-tradingview`), defaulting to LEVELS (our own OHLC chart).
  The 1D/1W/1M/1Y widget range chips only show in TRADINGVIEW mode.
- **Alert history**: `src/alerts.tsx` writes a permanent `FiredAlert` log (`alert_history_v1`, capped at 100)
  the moment an alert fires — symbol, label, alert price, price at fire, timestamps — exposed as
  `history` / `clearHistory`. The Alerts tab has an `ACTIVE · n` / `HISTORY · n` segmented control
  (testIDs `alerts-tab-active`, `alerts-tab-history`); history rows tag the outcome `TARGET HIT` /
  `STOP HIT` and survive CLEAR FIRED (separate store). `CLEAR LOG` (testID `clear-history`) wipes it.
- **Localized verdict card**: `ShareCard` uses react-i18next; new `share.*` keys in all four locales
  (en/hi/es/zh) cover `// AI DESK`, `THE DESK SAYS`, `CONFIDENCE`, `THESIS`, `10-AGENT ANALYSIS`,
  `NOT FINANCIAL ADVICE`; the date formats with the active locale. BUY/SELL/HOLD stays English by design.
- **Free credits re-verify**: the two concurrency tests in `tests/test_free_credits.py` were flaky (cached
  ORCL/IBM/INTC/AMD analyses are served free, so no credit is spent); they now clear those cached analyses
  first. 9/9 pass. Testing agent (iteration_12) also added `tests/test_wallet_sanity_iter12.py` (4/4).

## Live price refresh on the analysis screen (2026-06-19, session 9, LIVE_PRICE_REFRESH.md) — DONE
- Implemented exactly as specced; `git diff` limited to `frontend/app/analysis/[id].tsx` (+89/-4).
- `AnalysisScreen` now runs a second, independent loop: `liveQuote` state + `liveFailures` /
  `liveIntervalRef` refs, one effect resetting `liveQuote` whenever a new analysis snapshot arrives,
  and one effect keyed on `[analysis?.symbol, analysis?.status]` that fires an immediate
  `api.quote(symbol)` then every 30s, pauses on `AppState` != "active", resumes (with the failure
  count reset) on foreground, and clears the interval permanently after 3 consecutive failures.
- Only `QuoteCard` (`liveQuote ?? analysis.quote`) and the chart's `livePrice` prop (threaded
  VerdictView -> TvSection -> TradingViewChart) use the refreshed value. `VerdictLevels`,
  `PositionSizer`, `ShareCard` and `TimeframesCard` still read the frozen `analysis.quote`
  snapshot, and the 1.5s pipeline poll (lines 59-82) is untouched — it still stops at completion.
- Verified by testing agent (iteration_13): quote calls at t=0/30/60s, verdict + target/stop/horizon
  byte-identical across two refresh cycles, 0 calls while the tab is hidden and an immediate call on
  foreground, exactly 3 failed attempts then a full stop (restarts only on the next foreground).
- Known trade-off: `LightweightChart` re-keys its WebView/iframe on the generated HTML, so the
  own-data LEVELS chart reloads (mild flicker) on each 30s tick. Fixing it needs a price-line
  injection path inside `LightweightChart`, which this spec's diff scope excluded.

## Razorpay live-key swap + checkout hardening (2026-06-19, session 9) — DONE
- New LIVE keys in `backend/.env` (`rzp_live_TcXwvcOkbzuvGv`), verified against the Razorpay API and by
  creating a real $5 order. Webhook secret still empty (callback + `/api/pay/status` polling cover it).
- **Root cause of the user's failed payments**: Razorpay rejected them with *"Payment blocked as website
  does not match registered website(s)"* — the checkout host must be added under Razorpay Dashboard →
  Account & Settings → Websites & API keys. Nothing was ever charged.
- **Root cause of the "Couldn't start checkout · HTTP 502" alert**: that is our own error from
  `/api/pay/order` when `rzp.create_order` raises. The deployed container still held the OLD (now
  deactivated) keys, so Razorpay returned 401. Needs a redeploy, no code change.
- Callback hardened: `/api/pay/callback` is now `api_route(["POST","GET"])` and reads the `razorpay_*`
  fields defensively from form / JSON / query params. Razorpay only sends them on a *successful*
  authorisation, so the old `Form(...)` signature showed customers a raw FastAPI 422 inside the checkout
  WebView on cancel/failure. Cancels now render "Payment wasn't completed — nothing was charged" (200)
  and mark the order failed; forged/unsigned attempts still 400; crediting still requires a valid
  signature + a re-fetch from Razorpay. `checkout_html` now sets `redirect: true` (required for
  `callback_url` to POST at all) and appends `?order_id=` so bodyless returns can be matched.
- Checkout + callback URLs are now derived from the incoming request (`public_base()`, honouring
  `x-forwarded-*`) instead of the static `PUBLIC_BASE_URL`, which was baked to the preview host and would
  have sent paying customers to the wrong origin in production.
- Deployment blockers fixed (via deployment_agent): root `.gitignore` was excluding `.env` / `.env.*` /
  `*.env` from the deploy build context, and the platform readiness probe was 404ing — added an
  app-level, DB-free `GET /health`. Re-scan came back with no blockers.
- Tests: new `backend/tests/test_pay_callback.py` (6 cases); 37/37 pass across
  test_pay_callback + test_razorpay + test_wallet + test_free_credits.

## Razorpay: migrated to Payment Links (2026-06-19, session 9) — DONE
- **Why**: every live payment was rejected with *"Payment blocked as website does not match registered
  website(s)"* — Standard Checkout (checkout.js) validates the page origin against the account's
  registered-websites list, which we can't change at runtime. Payment Links are hosted by Razorpay
  (rzp.io / razorpay.com) so the origin check doesn't apply.
- Removed: `rzp.checkout_html`, `rzp.create_order`, and `GET /api/pay/checkout/{id}` (now 404).
- Added: `rzp.create_payment_link` / `fetch_payment_link` / `verify_link_signature`. Link signature is
  `link_id|reference_id|status|payment_id` — different from checkout's `order_id|payment_id`.
- `POST /api/pay/order` returns `{order_id: "plink_…", checkout_url: "https://rzp.io/…"}`. The payments
  doc now carries `razorpay_payment_link_id`, `reference_id`, `short_url`; `razorpay_order_id` is
  **omitted** at creation (a link's order only exists once the customer starts paying) and bound later by
  `bind_order_id()`, so the unique indexes on `payments` are now sparse (the old non-sparse
  `razorpay_order_id_1` index is dropped on startup).
- `/api/pay/callback` gained a payment-link branch (`link_callback`), `/api/pay/webhook` handles
  `payment_link.paid|expired|cancelled`, and `/api/pay/status/{id}` accepts a plink id (fetches the link,
  settles on `paid` with matching amount+currency). Crediting still only ever happens through
  `settle_payment` -> `credit_wallet_once`.
- **This account requires customer email AND contact on every link**, but users sign in with only one.
  `resolve_payment_customer()` fills what's known, accepts what the app supplies, stores it on the user,
  and otherwise 400s with `detail: "contact_required:email|phone"`. `WalletCard` shows a ONE-TIME DETAIL
  sheet (testIDs `contact-prompt`, `contact-email-input`, `contact-phone-input`, `contact-continue`,
  `contact-error`) with inline validation, then retries the top-up.
- Verified (iteration_14): 68/68 existing tests plus a new `tests/test_pay_links_iter14.py` (16 tests);
  frontend confirmed opening a real Razorpay-hosted link, no payment completed (LIVE keys).
- Webhook events to enable in the dashboard are now `payment_link.paid` (plus optionally
  `payment_link.expired` / `payment_link.cancelled`); `RAZORPAY_WEBHOOK_SECRET` is still unset.

### Deployed-vs-preview payment 502 (same session) — ROOT CAUSED
- The user's "Couldn't start checkout · HTTP 502" on their phone came from the **deployed** container, whose
  logs show Razorpay replying **401 "The api key provided by you has expired and cannot be used"** — the
  deployed image still holds the OLD key pair. The preview backend creates links fine (verified three real
  links for the user's own `admin-+918446307145` account). Only a redeploy can fix the deployed env.
- The root `.gitignore` `.env` / `.env.*` / `*.env` rules had **reappeared** and were stripping env files
  from the deploy build context again — removed (verify with `git check-ignore -v backend/.env`). If a
  deploy ever ships without secrets, check this first.
- Diagnostics added so this is never guesswork again: `rzp.RazorpayError` carries Razorpay's own
  description, `/pay/order` now returns `detail: "Razorpay: <description>"` (the app shows it verbatim
  instead of "HTTP 502"), and startup probes the credentials once, logging
  `RAZORPAY CREDENTIALS REJECTED [code]: description` when they're stale.
- 84/84 payment + wallet + free-credit tests pass.

## Launch-free month (2026-06-19, session 9, LAUNCH_FREE_MONTH.md) — DONE
- `wallet.is_launch_free_period(launch_free_until, now)` added verbatim from the spec (date-based,
  auto-expiring; empty/garbage value => no free period, so billing can never be freed by accident).
  The spec's `FREE_TRIAL_CREDITS = 5` / `PRICE_MOVE_THRESHOLD` snippet was NOT applied — both already
  exist in this codebase (`FREE_CREDITS_ON_SIGNUP = 10`, `PRICE_MOVE_THRESHOLD = 0.015`) and changing
  them would have silently cut the shipped free-credit allowance in half.
- `server.py`: `LAUNCH_FREE_UNTIL = os.environ.get("LAUNCH_FREE_UNTIL", "")` (next to
  `WALLET_ENFORCEMENT_ENABLED`, since this repo has no `REVIEWER_FIXED_OTP`); `/analyze` computes
  `launch_free_now` and ORs it into `admin_bypass`, and the analysis doc carries `launch_free_active`.
  `/wallet/balance` returns `launch_free_active`.
- **One deliberate addition beyond the spec**: `enforcement_enabled` is now also false while the window
  is open. Without it the home screen's `needsFunds` check (`app/(tabs)/index.tsx:178`) greys out the
  ANALYZE button for a drained wallet that the backend would have run for free — acceptance criterion 2
  would have passed on the API and failed in the product.
- `WalletCard`: reads `launch_free_active` and renders a `FREE DURING LAUNCH` banner
  (testID `launch-free-banner`) *instead of* the +$5/+$10/+$25 buttons (now testIDs `topup-5|10|25`),
  and hides the Razorpay note. Not platform-branched, so iOS is covered — which is the actual
  requirement for Apple 3.1.1: the external purchase path must not be reachable, not merely un-charged.
- Verified: window on -> banner present, `topup-*` count 0, Razorpay note gone; window off -> buttons
  back, banner gone (screenshots + rendered-tree assertions). Suite: 230 passed / 4 skipped with the
  default parallel config; `RUN_LAUNCH_FREE_E2E=1 pytest tests/test_launch_free.py -o addopts=""` runs
  the 3 env-flipping e2e tests (10/10) — they're skipped in the default run because they restart the
  backend and would disturb the other xdist worker.
- **To go live**: set `LAUNCH_FREE_UNTIL=YYYY-MM-DD` in `backend/.env` and redeploy. Currently UNSET,
  so billing is exactly as before.

### Launch-free daily cap: 10 analyses/day (same session) — DONE
- `wal.LAUNCH_FREE_DAILY_LIMIT = 10`. Enforced in `/analyze` **only while the launch window is open**
  (outside it, normal wallet/free-credit billing is untouched) and admins are exempt.
- Counter lives in `usage_daily` as `_id: "<wallet_key>:<YYYY-MM-DD>"` (UTC day, so it resets at
  midnight UTC). `consume_daily_free_run()` increments atomically and rolls the increment back when it
  lands over the cap — two concurrent requests can't both take the last slot, and a blocked attempt
  doesn't eat one.
- Over the cap => **429** "Daily limit reached — 10 free analyses per day during launch. Resets at
  midnight UTC." The home screen now alerts on this (and on any other start-analysis failure — it
  previously swallowed every error except "insufficient balance").
- `/wallet/balance` adds `launch_free_daily_limit` and `launch_free_runs_left` (both null outside the
  window); WalletCard shows "Free during launch — N of 10 analyses left today".
- Verified: 12/12 in `tests/test_launch_free.py` (serial, `RUN_LAUNCH_FREE_E2E=1`), full suite
  230 passed / 6 skipped, and the wallet card rendering confirmed by screenshot with the window on.

## Combined launch-free design (2026-06-19, session 9, ALREADY_BUILT_AND_LAUNCH_FREE_COMBINED.md) — DONE
- **Part A check**: `DELETE /account` and real Sign in with Apple are NOT in this repo — there is no
  account-deletion route at all and `/api/auth/apple` still calls `au.verify_apple_id_token_stub`
  (501). `IOS_BLOCKERS_ALL_SIX.md` has never been uploaded here, so its code could not be applied;
  asked the user for that file. Do NOT hand-roll either one without it (and route auth through
  `integration_expert` if building from scratch).
- **Part B replaced the previous session's daily cap** with the spec's design:
  - `wal.LAUNCH_FREE_DAILY_CAP = 10`, plus `wal.launch_free_daily_state()` (derives the rollover from
    the stored date on every read — no scheduled job, bad data reads as 0) and
    `wal.has_launch_free_daily_quota()`.
  - Counter now lives ON THE WALLET DOC (`launch_free_daily_date`, `launch_free_daily_count`), per the
    spec's privacy rationale: a count on an already-per-user document, never a user_id on `analyses`.
    The previous `usage_daily` collection and its 429 hard block are gone (collection dropped).
  - Exhausting the day's allowance is **not** a block: `launch_free_daily_ok` simply stops feeding
    `admin_bypass`, so the request falls through to free credits, then the wallet (drained wallet =>
    the ordinary 402; funded wallet => it just pays). `/analyze` reports
    `launch_free_active: launch_free_daily_ok`, i.e. what happened on THIS run.
  - `/wallet/balance` returns `launch_free_active`, `launch_free_daily_remaining` (null outside the
    window) and `launch_free_daily_cap`; `enforcement_enabled` stays off only while remaining > 0.
- Frontend: Analyze screen shows `FREE DURING LAUNCH · 7 OF 10 FREE TODAY` under the ticker input
  (testID `launch-free-countdown`, wallet now fetched on mount/focus rather than only after a ticker is
  picked) plus `N of 10 free today` above the execute CTA (testID `launch-free-cta-note`). WalletCard
  still replaces the +$5/+$10/+$25 buttons with the FREE DURING LAUNCH banner — countdown replaces
  purchase UI, never sits beside it.
- Verified: 19/19 in `tests/test_launch_free.py` (serial, `RUN_LAUNCH_FREE_E2E=1`) covering helpers,
  counter increment, 11th-run fall-through to 402, funded account paying past the allowance, a
  **simulated day rollover** granting a fresh 10, and admin staying unlimited with a count of 60.
  Full suite 234 passed / 9 skipped. `LAUNCH_FREE_UNTIL` left unset; wallet docs cleaned of test state.

## iOS blockers 1-5 (2026-06-19, session 9, IOS_BLOCKERS_ALL_SIX.md) — DONE
- **1. `DELETE /api/account`** added right after `/auth/me`, verbatim from the spec: deletes the user
  doc + `wallets` doc for `user:<id>`, keeps `payments` / `wallet_ledger` (financial recordkeeping),
  keeps analyses (never linked to identity). The old bearer token dies with the user record because
  `get_current_user` looks the user up on every request — proven by test, not assumed.
  Frontend: `AccountCard` has a two-tap `DELETE ACCOUNT` (testID `delete-account-button`) that calls
  `api.deleteAccount()` then signs out locally; inline confirm + error text (Alert is a no-op on web).
- **2. Sign in with Apple is real now**: `au.verify_apple_id_token(token, audience, jwks)` replaces
  `verify_apple_id_token_stub` (JWKS kid match, RS256 signature, issuer + audience + expiry, never
  raises). `server.fetch_apple_jwks()` caches Apple's keys for an hour and is isolated so the one
  untestable thing (the network fetch) is separate from the crypto. `/auth/apple` now 401s an invalid
  token and 502s a JWKS fetch failure; it still 501s until `APPLE_SERVICES_ID` is set — **user must
  set their real Apple Services ID**.
- **3. Privacy Policy link** in `AccountCard` -> `https://tradingagents.in/privacy.html`
  (testID `privacy-policy-link`). The same URL still has to go into App Store Connect's own field.
- **4. Privacy manifest**: `frontend/app.json` `ios.privacyManifests` declares
  `NSPrivacyAccessedAPICategoryUserDefaults` with reason `CA92.1` (AsyncStorage/SecureStore). JSON
  re-validated after the edit.
- **5. Reviewer login: NOT APPLICABLE — the mechanism does not exist in this repo.** There is no
  `PLAYSTORE_REVIEWER_IDENTIFIERS` / `PLAYSTORE_REVIEWER_FIXED_OTP` anywhere (the spec assumed an
  earlier Play Store doc that was never applied here). The only bypass is `ADMIN_IDENTIFIERS`
  (`+918446307145`), which does not solve OTP delivery for a reviewer. Still an open blocker.
- **6. IAP: deliberately NOT built** per the spec and the user's instruction (needs real App Store
  Connect consumable product IDs first). The launch-free window is the interim cover.
- Tests: new `tests/test_ios_blockers.py` — 10 Apple tests using a REAL per-class RSA keypair
  (valid token, wrong audience, non-Apple issuer, expired, unknown kid, **forged signature from a
  different private key**, garbage, empty JWKS, no-email token, no-sub token) + 4 delete-account tests
  (user+wallet gone, old token 401, payments retained, clean re-signup, unauthenticated 401).
  `tests/test_auth.py` placeholder assertion updated to the new signature. Full suite: 249 passed,
  9 skipped. Note: `test_wallet_sanity_iter12` / `test_wallet_razorpay_admin` intermittently collide
  under xdist because they share the same admin user — pre-existing, passes in isolation.

## Security audit + fixes (2026-06-19, session 9) — DONE
Audit verdict was FAIL / DO-NOT-LAUNCH with two CONFIRMED HIGH findings. All three fixed and
independently re-verified by the testing agent (iteration_16, 274 passed / 9 skipped).
- **SEC-001 (HIGH) privilege escalation -> unlimited free paid usage.** `/pay/order`'s
  `resolve_payment_customer` wrote the caller-supplied email/phone straight onto their own user doc, and
  `au.is_admin` matches `user.phone` / `user.email` against `ADMIN_IDENTIFIERS` — so ANY signed-in user
  could send `{"phone": "+918446307145"}` and inherit the owner's billing bypass. **Introduced by me**
  when adding the Razorpay payment-link contact requirement. Fix: unverified contact now goes to
  `billing_email` / `billing_phone` and NEVER overwrites the verified sign-in identity (which only the
  OTP / Google / Apple flows set). Also cleaned two already-escalated accounts
  (`users.update_many({phone: admin, id: !/^admin-/}, {$unset: {phone}})`) — re-count now 0.
  **Rule going forward: never write to `users.email` / `users.phone` outside a verified auth flow.**
- **SEC-002 (HIGH) unauthenticated data destruction.** `GET /analysis/{id}`, `GET /history` and
  `DELETE /analysis/{id}` had no auth: anyone could enumerate every analysis and delete any of them.
  All three now `Depends(get_current_user)` (401 otherwise). Delete is deliberately NOT per-user —
  history is a shared cache, so an owner check would 403 people deleting rows their own history shows;
  `owner_hash` (HMAC of the account id with JWT_SECRET, never returned to clients, projected out of
  both read endpoints) is now recorded on new analyses so per-account history can be enabled later
  without a migration. **Making history private is an open product decision.**
- **SEC-003 (MEDIUM) forgeable sessions.** `JWT_SECRET` fell back to the in-repo default
  `dev-only-change-me`; combined with the recurring `.gitignore` `.env` exclusion that nearly shipped.
  Now fails closed: the app refuses to start when auth is enabled and the secret is missing/default.
- Hardening: CORS `allow_credentials` -> False (bearer tokens only, never cookies).
- Test updates: existing e2e tests now send `AUTH_HEADERS` to the newly authenticated endpoints, and the
  two contact-persistence tests assert `billing_phone` with `phone is None`.
- Accepted/left open: Razorpay-for-digital-content vs Apple IAP, no reviewer login, `.gitignore`
  regenerating its `.env` exclusion (platform-side), `server.py` now 2264 lines.

## Apple IAP + private history + server.py refactor (2026-06-20, session 10) — DONE

Three things, in the order the user picked them.

### 1. Apple In-App Purchase on iOS, via RevenueCat (the last App Store blocker)
Razorpay for wallet credit is an automatic rejection on iOS (guideline 3.1.1 — digital content
consumed in-app must use Apple's IAP). Android and Web keep Razorpay untouched.
- `backend/iap.py` — product-id → USD map (`credits_5`/`credits_10`/`credits_25` = 5/10/25, the same
  packs as Razorpay), webhook auth compare, and `classify_event()` as a pure function so the
  "what may credit" decision is unit-testable: only `NON_RENEWING_PURCHASE` from `APP_STORE` /
  `MAC_APP_STORE` with a known product and a `user:<id>` App User ID credits. Renewals, Play Store,
  Stripe, unknown products and anonymous ids never do.
- `GET /api/pay/iap/config` → `{enabled, ios_api_key, packs}`. The public SDK key is served from the
  backend, NOT bundled, so it can be rotated without a new App Store build. Reports `enabled: false`
  and an empty key until both env vars are set.
- `POST /api/pay/iap/webhook` — the ONLY thing that credits an Apple purchase. The app's own purchase
  callback is never trusted. Auth is the dashboard-configured `Authorization` header value
  (`REVENUECAT_WEBHOOK_AUTH`), compared in constant time; with no secret set NOTHING is accepted.
  Credits via `credit_iap_once()`, which shares `wallet_ledger` (and its unique `payment_id` index)
  with Razorpay — one place in the whole system a balance can grow — keyed `apple:<transaction_id>`,
  so RevenueCat's at-least-once delivery and duplicate event ids both credit exactly once.
- `frontend/src/iap.ts` — `react-native-purchases@10.9.1` is required LAZILY inside try/catch:
  it's a native module, absent in Expo Go and on web, and an unavailable module must degrade to
  "purchases off on this build", never crash the wallet screen. Configured with `user:<id>` only
  after sign-in (the App User ID is what says whose wallet a real payment credits); `resetIap()`
  on sign-out so the next account can't inherit it.
- `WalletCard.tsx` — on iOS renders the StoreKit packs, or an explanatory line when IAP isn't
  configured/available. A Razorpay button can never render on iOS now.
- **Still needed from the app owner** (feature is inert until then): App Store Connect Consumable
  products with those exact ids, a RevenueCat project wired to the App Store app, then
  `REVENUECAT_IOS_KEY` (iOS public SDK key) in `backend/.env`, and the generated
  `REVENUECAT_WEBHOOK_AUTH` pasted into RevenueCat → Integrations → Webhooks
  (URL `<deployed>/api/pay/iap/webhook`). Cannot be tested in Expo Go — needs a build.
- Tests: `backend/tests/test_iap.py` (24). Webhook secret is set, so crediting/idempotency/
  ignore/reject paths are covered against the real running backend.

### 2. History is now private per account (was a product decision left open by the security audit)
Any signed-in account could previously read, open and delete every analysis anyone had run.
- `deps.own_analyses_filter(user)` = `owner_hash` match OR `viewer_hashes` contains the caller.
  `viewer_hashes` is the new part: a free re-check serves ANOTHER account's cached document, and
  from the caller's side that was still their own re-check — so `/analyze` now `$addToSet`s their
  hash onto the cached doc, keeping it visible and openable without duplicating it.
- `/history` and `/analysis/{id}` are filtered; a foreign id returns **404, not 403**, so a real id
  is indistinguishable from a made-up one. Both projections strip `owner_hash` and `viewer_hashes`
  (the cache-hit response leaked `owner_hash` before — fixed).
- DELETE deletes only runs you own; for a cache-served row it just `$pull`s your viewer mark, so
  deleting from your history can't destroy someone else's record or the shared re-check cache.
- Legacy analyses (no `owner_hash`) are visible to nobody, which is the safe direction.
- Tests: `backend/tests/test_private_history.py` (13, two-account Alice/Bob).

### 3. server.py refactored: 2387 lines → 97
Pure structural move, no behaviour change (all 312 tests green before and after).
- `core.py` (36) — env, logging, Mongo handle, model config, `now_iso`. Imports nothing local, so
  no cycle is possible.
- `market_data.py` (377) — all Yahoo feeds + headline sentiment.
- `pipeline.py` (599) — prompts, parsers, grounding gate, `run_analysis`, i18n directive.
- `deps.py` (200) — auth config + session dependencies, wallet/admin/free-credit reads,
  `owner_hash_for`, `own_analyses_filter`. The JWT fail-closed check moved here and still fires on
  `import server`.
- `routes/market.py` (107), `routes/analysis.py` (213), `routes/auth_routes.py` (271),
  `routes/payments.py` (543), `routes/portfolio.py` (137) — each owns an `api_router`, all included
  by `server.py`.
- Tests that reached into `server` internals now import the real module (`pipeline`,
  `market_data`, `deps`, `routes.analysis`); the source-scanning privacy tests iterate
  `BACKEND_SOURCES` so they can't be defeated by moving code to a new file.
- Also fixed a genuinely flaky test: `test_security_fixes_iter16.teardown_module` deleted ALL
  `^TEST_sec16-` users, wiping the other xdist worker's in-use account ("User not found" 401).
  It now only removes ids that process created.

### Still open
- Live P/L for the (hidden) Portfolio tab. RTL layout support.

### Test-suite note (2026-06-20)
338 passed / 9 skipped when green. The pipeline-dependent e2e tests
(`test_wallet_sanity_iter12`, `test_tradingagents`, `test_pay_links_iter14`) are LOAD-flaky under
xdist: ~1-2 of them fail in a different place on each full run and every one of them passes in
isolation. Cause is external rate limiting (Yahoo + the LLM + Razorpay) when 380 tests fire real
analyses in parallel, not app behaviour. Re-run the failing file alone before investigating.

### RevenueCat key received (2026-06-20)
`REVENUECAT_IOS_KEY=appl_PdgYyIGhXzhIdLrwVzBbWOCwaPO` is now in `backend/.env`, so
`/api/pay/iap/config` returns `enabled: true` and `/api/wallet/balance` returns
`iap_enabled: true` — the iOS wallet card renders the StoreKit packs instead of the
"not switched on" line. Two regression assertions that pinned `False` now follow
`iap.configured()` instead, so flipping the path on is not a test failure.
Remaining owner-side steps before a real purchase can succeed: the three Consumable products
in App Store Connect imported into RevenueCat, and an App Store Connect In-App Purchase `.p8`
uploaded to RevenueCat (Emergent does not store that file). Needs a real iOS build to test.

## Two currencies — USD / INR, locked once per account (2026-06-20, session 11) — DONE
Implemented from the user's `TWO_CURRENCY_USD_INR.md` spec, exactly as written (adapted only to the
post-refactor file layout: the spec's `server.py` edits landed in `routes/payments.py`,
`routes/analysis.py` and `deps.py`).

**Why INR exists at all:** UPI settles INR only — a hard constraint of the payment rails, not a
Razorpay setting. A Payment Link created with `currency: "INR"` shows UPI automatically; a USD one
cannot. So there is NO "enable UPI" code anywhere; setting the currency correctly is the entire
mechanism. **Verified on real live checkouts**, not inferred: the ₹99 link showed a UPI QR plus UPI
as the first payment option (then Cards / Netbanking / Wallet); the $5 link showed **Cards only**.
Both links were cancelled via the Razorpay API afterwards.

- `wallet.py`: added `PRICES_INR` (₹20 / ₹30 / ₹5), `TOPUP_PACKS_INR` (₹99/₹199/₹499),
  `CURRENCY_SYMBOL_INR`, `SUPPORTED_CURRENCIES`, and `prices_for` / `topup_packs_for` /
  `currency_symbol_for`. `get_price`, `has_sufficient_balance`, `new_balance_after_charge` and
  `is_valid_topup` all take an OPTIONAL `currency` defaulting to `"USD"`, so every existing
  no-argument call site behaves byte-identically (the existing `tests/test_wallet.py` 14 tests pass
  unchanged). Anything that isn't exactly `"INR"` resolves to USD — failing toward the *more
  expensive* table, so a corrupt stored value can never devalue a balance.
- `razorpay_pay.create_payment_link` now takes `currency` explicitly instead of reading
  `wal.CURRENCY`.
- `create_topup_order` locks the currency once, and **the safety case is the point**: a wallet with
  a nonzero balance and no `currency` field predates this feature, so it can only have been earned
  in USD → forced to USD regardless of what the request asks. Without it, someone holding a real
  $12.50 could pick INR and have it silently become ₹12.50. Locked accounts ignore `currency` on
  every later request.
- `/wallet/balance` returns the account's real currency, its prices/packs/symbol, plus
  `currency_locked` and `currency_options` (code + symbol + packs + prices for both). The app
  renders those directly and holds NO currency knowledge of its own — no hardcoded amounts.
- `/analyze`'s charge, its 402 message and `price_charged` all use the account's currency.
- Frontend: `WalletCard` shows a one-time "$ USD / ₹ INR" choice before the first top-up
  (Android/Web only — Apple bills in the buyer's own storefront currency, so iOS has nothing to
  ask), then renders whatever the backend resolved.

### iOS/RevenueCat interaction (asked for explicitly, beyond the spec)
An INR-locked wallet receiving a USD-denominated Apple purchase must NOT have the USD face value
added to it — that would credit ₹5 for a $5 purchase. `iap.PRODUCT_CREDIT_INR` mirrors the Razorpay
INR packs and `iap.credit_amount_for(product_id, wallet_currency)` resolves the amount from the
immutable product id + the account's own locked currency, never from the webhook body.
`credit_iap_once` now takes that resolved currency (so the ledger records INR honestly), logs when
Apple's charge currency differs from the wallet's (expected — Apple bills per storefront), and
locks a previously-unlocked wallet to the credited currency so a balance is never left unlabelled.
`/pay/iap/config?currency=INR` serves ₹ pack amounts for the iOS buttons.

### Bug found and fixed while testing (introduced by this change)
The currency-lock upsert could create a wallet document containing only `{device_id, currency}` —
no `balance`. `deps.get_wallet_balance` did `doc["balance"]`, so `/pay/status` and the link callback
500'd for any account whose first-ever action was a top-up. Fixed at the source
(`$setOnInsert: {balance: 0.0}`) and hardened both readers (`get_wallet_balance`, the sign-in wallet
merge) to treat a missing balance as zero — the pre-existing launch-free counter upsert can create
the same shape.

### Deliberately NOT changed
`credit_wallet_once` is untouched per the spec, which means its `wallet_ledger` row still records
`currency: wal.CURRENCY` ("USD") even for an INR payment. The `payments` row for the same
transaction DOES record the real currency, so nothing is lost — but if the ledger is ever used for
reporting, that one field should be switched to `order.get("currency")`. Flagged to the user rather
than changed unilaterally.

- Tests: `backend/tests/test_two_currency.py` (47) — backward compatibility, currency resolution,
  cross-currency pack rejection, INR balance math, first-top-up locking, the existing-balance
  protection (including that the balance itself is never touched), `/wallet/balance` shapes, the
  ₹/$ 402 messages, and the IAP amount resolution + crediting. Full suite: **385 passed, 9 skipped**.

## Consent recording + consent gate + account deletion (2026-06-20, session 12) — DONE
Implemented from the user's `CONSENT_AND_ACCOUNT_DELETION.md`. DPDP (India) requires informed,
specific, **affirmative** consent presented with the data request itself — not implied by continued
use — plus withdrawal "as easy as giving consent".

**Part A — the three backend edits, exactly as specced** (the spec was written against the old
monolithic `server.py`; the same three edits landed in the post-refactor files):
- `deps.py`: `CONSENT_VERSION = "1.0"`. A stored consent only counts for the version it was given
  against, so bumping this re-asks everyone — someone who agreed to an older notice hasn't agreed
  to a materially different one.
- `routes/auth_routes.py`: `/auth/me` now returns `consent_given` + `consent_version_required`, and
  `POST /consent` records `{agreed, agreed_at, version}`. `agreed: false` is **rejected with 400,
  never stored** — this endpoint only records agreement; withdrawal is account deletion.
- `routes/analysis.py`: `/analyze` returns **403 `consent_required`** for a signed-in account
  without current consent. Specific reason, not a generic error, so the app knows it still owes the
  screen.
- `DELETE /account` already existed from the iOS-blockers work, byte-identical to the spec's
  version (deletes `users` + `wallets`, deliberately NOT `payments` / `wallet_ledger` / `analyses`),
  so it was left untouched rather than rewritten.

**One deliberate deviation, and why:** the spec's gate reads `if user and not admin_bypass`. In this
codebase `admin_bypass = is_admin(user) or launch_free_daily_ok`, so during a launch-free promotion
that expression would switch the legal gate OFF for every ordinary user. Gated on `is_admin(user)`
specifically instead, which is what the spec's own comment describes ("admin/reviewer accounts
aren't real end-users"). A promotion must never quietly disable a consent gate.

**Part B — the consent screen** (`frontend/src/components/ConsentScreen.tsx`): the notice text
verbatim from the spec, rendered in the app's brutalist style, shown from `app/_layout.tsx` when
`user.consent_given === false` — after sign-in, before anything else renders. The checkbox
**starts unchecked always** and Continue is disabled until it's ticked (belt and braces with the
backend's own affirmative-only rule). Links out to the Privacy Policy and Terms.

**Test fixtures had to change (not production behaviour):** every signed-in test account now seeds
a `consent` sub-document, because an account that hasn't consented genuinely can't analyse any more
— that's the feature. 10 helper inserts touched, no assertions weakened.

- Tests: `backend/tests/test_consent.py` (15) — fresh account blocked with the specific reason,
  an older-version or `agreed: false` record not counting, `agreed: false` → 400 with nothing
  stored, a missing `agreed` field → 422 (never a silent yes), consent recorded + `/analyze`
  unblocked, anonymous requests never consent-gated (and market endpoints untouched), admin not
  gated, deletion removing both records, and the pre-deletion token rejected on `/auth/me`,
  `/history` and `/analyze` afterwards. Full suite: **399 passed, 9 skipped**.

## Security audit (2026-06-20, session 13) — DONE
Full read-only audit of all 27 routes. Verdict: CONDITIONAL PASS. The three fixes from the
session-9 audit were confirmed NOT regressed by the big refactor (wallet key still derived from the
session, `hmac.compare_digest` on admin/webhook checks, fail-closed on a default `JWT_SECRET`). No
secret leakage, no cross-account data access (owner_hash/viewer_hashes IDOR checks held), no
unauthenticated state-changing endpoint. Two real money-flow findings, both fixed:

### SEC-001 [HIGH] Apple sandbox purchases credited a real, spendable wallet
Apple's sandbox completes a purchase for free, and a sandbox event is identical to a paid one apart
from `environment` — which nothing checked. Anyone with a sandbox tester account had an unlimited
free top-up button.

Not fixed by simply refusing them: **App Store reviewers purchase in sandbox and reject apps that
take a purchase without delivering the content**, so a blanket rejection trades a security bug for a
review rejection. Instead sandbox purchases still credit, but only
`iap.SANDBOX_CREDIT_LIMIT = 5` times per account, after which the webhook answers 200 with
`ignored` (a 4xx would make RevenueCat retry forever) and credits nothing. Every credit now records
its `environment` on the ledger row, so sandbox-funded balance is auditable and reversible, and each
one logs a warning. A missing `environment` normalises to `"UNKNOWN"` — i.e. fails toward the capped
path, never the unlimited one. Production credits are never capped and do NOT consume the sandbox
allowance (a paying customer keeps their headroom; paying once buys an abuser none).

### SEC-002 [MEDIUM, confirmed] Non-atomic wallet debit funded several analyses per charge
`/analyze` read the balance, checked affordability, then wrote the reduced value. N requests
arriving together each read the same balance, each passed, and each launched a paid LLM run off one
charge. Replaced with a single conditional update —
`update_one({device_id, balance: {$gte: price}}, {$inc: {balance: -price}})` — so "can they afford
it" and "take it" are the same operation and exactly one request can win. `modified_count != 1` is
the insufficient-funds path.

### Hardening applied
`POST /api/portfolio/optimize` needs no session and fanned every holding out to two external Yahoo
calls with no cap — a free upstream-quota amplifier. `holdings` is now `Field(max_length=50)`.

### Hardening noted, deliberately not changed
- CORS `allow_origins=["*"]` is safe here: sessions are bearer tokens and `allow_credentials=False`.
  If origins are ever narrowed, keep credentials off.
- Symbol path params interpolate into a FIXED Yahoo host, so this is not SSRF; an allowlist/format
  check would be defence in depth.
- **Owner action, not code**: confirm the per-storefront App Store price tiers for `credits_5/10/25`
  line up with `iap.PRODUCT_CREDIT` / `PRODUCT_CREDIT_INR`, so a cheap storefront tier can't buy
  richer-currency credit. IAP crediting is intentionally decoupled from Apple's charged amount.

- Tests: `backend/tests/test_security_fixes_iter20.py` (10) — sandbox still credits for review, stops
  at the cap, unlabelled environment treated as sandbox, production uncapped and not consuming the
  sandbox allowance, environment recorded on every ledger row; and for SEC-002, four and five
  concurrent analyses against one and two runs' worth of balance funding exactly one and two runs,
  a balance that can never go negative, and the 402 still naming the right currency.
  Full suite: **412 passed, 9 skipped**.
