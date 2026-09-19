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
- New LIVE keys in `backend/.env` (key id redacted — lives only in `backend/.env`), verified against the Razorpay API and by
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
`REVENUECAT_IOS_KEY` (the public SDK key) is now in `backend/.env`, so
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

## SECURITY_FIXES.md remediation (2026-06-20, session 14) — DONE
The user supplied a remediation document written against commit `1d90583` — i.e. against the OLD
monolithic `server.py`, before the refactor, before private history and before consent. Its stated
scope ("exactly two files: backend/server.py and backend/tests/test_tradingagents.py") no longer
maps onto this codebase. Following its OWN instruction — verify each finding against current HEAD
before touching anything, never batch-assume — each of the five was checked against the real code
first. Three were already closed; four edits were applied (3a, 3b, 4, 6 — finding 3 is three
separate races and only 3c was already done).

### Already fixed — verified, not assumed, and deliberately NOT re-touched
- **Finding 1 (admin self-promotion via `/pay/order`)**: closed in session 9, and closed *better*
  than the document's fix. Unverified client-supplied contact is stored under separate
  `billing_email` / `billing_phone` fields and never over the verified `email` / `phone` the admin
  allowlist matches on (`routes/payments.py:170-200`). The document's version (`email = email or
  normalized`) would additionally have stopped a user correcting a wrong address for the current
  payment — a behaviour regression for no extra safety. Covered by `test_security_fixes_iter16.py`.
- **Finding 2 (unauthenticated read/delete of analyses)**: closed in session 9 (mandatory
  `get_current_user`) and since hardened to per-account private history. The document flagged a
  real tension — per-owner scoping vs the privacy commitment not to link analyses to identity —
  and it is resolved rather than traded away: the stored `owner_hash` is an HMAC of the account id
  keyed with `JWT_SECRET`, so a record can be matched to its owner by the server without an
  identity being written onto it. Covered by `test_private_history.py`.
- **Finding 3c (charge race)**: closed in session 13, currency-aware. Covered by
  `test_security_fixes_iter20.py`.

### Applied
- **3a — OTP verify race** (`routes/auth_routes.py`): `verified` is now flipped with an atomic
  claim, `update_one({"id": ..., "verified": False}, ...)`, and `modified_count == 0` returns "this
  code was already used". Previously two requests carrying the same valid code both passed the
  check and each ran a device-wallet merge.
- **3b — wallet merge race** (`routes/auth_routes.py`): `find_one_and_update` claims and zeroes the
  device wallet in one operation, and the account wallet is credited with `$inc` rather than a
  total computed from a separate read. Only the request that actually zeroed a positive balance
  credits anything, and it credits exactly what it claimed.
- **4 — NoSQL injection in `/pay/callback`** (`routes/payments.py`): every value taken from a JSON
  body is dropped unless it is a plain string, so `{"razorpay_order_id": {"$ne": ""}}` can no
  longer be read by MongoDB as an operator matching an arbitrary payment record. A non-dict body
  (e.g. a JSON list) is also handled — the document's `fields.items()` version would have thrown.
- **6 — `JWT_SECRET` fail-closed** (`deps.py`): now refuses to start when the secret is unset,
  under 32 characters, or the old default. Made **unconditional** rather than gated on
  `AUTH_REQUIRED_ENABLED`, because tokens are issued and `owner_hash_for` is keyed regardless of
  that flag. The existing iter16 tests still pass (the message keeps the "JWT_SECRET must be set"
  phrase they assert on).

### Corrected: the document's "urgent finding" that consent had disappeared
It reported that `POST /consent`, the `/analyze` consent gate and `DELETE /account` were "not
present in the current file" because `grep` of `server.py` returned nothing. That grep was correct
and the conclusion was wrong: `server.py` is 97 lines since the refactor. All of it is present and
live — `CONSENT_VERSION` in `deps.py:108`, the gate at `routes/analysis.py:96`, `record_consent` at
`routes/auth_routes.py:283`, `delete_account` at `routes/auth_routes.py:303`, both endpoints
answering 401 unauthenticated — with 15 passing tests in `test_consent.py`. Nothing was lost and no
branch needs investigating.

### Not touched, per the document's explicit "nothing else changes"
`/pay/webhook` passes `link_entity["id"]` from its JSON body into a query — the same class as
finding 4 — but it sits behind HMAC verification of the raw body, so a value can't be forged
without the secret. Flagged for its own verify-fix-test cycle rather than batch-patched.
Remaining open items from the document's own list: OTP request pumping, free-credit farming via
repeated device ids, webhook/ledger reconciliation, `/pay/status` ownership for anonymous wallets,
device-id namespace collisions, market-symbol validation, and the Starlette version.

- Tests: `backend/tests/test_security_fixes_iter21.py` (20) — three simultaneous OTP verifies
  redeeming one code exactly once and merging a $10 device wallet to exactly $10 (not $30), reuse
  after success rejected, a wrong code still failing normally, zero-balance merge a no-op, a
  legitimate merge still moving the money, merging into an existing balance adding rather than
  replacing, four operator-injection payloads leaving a seeded pending payment `created` and its
  wallet at 0.00, a list body not crashing the callback, legitimate callback behaviour unchanged,
  and the secret check refusing four weak values (including with enforcement off) while starting
  on a real one. Full suite: **430 passed, 9 skipped**.

## Webhook injection hardening + signup abuse guard (2026-06-20, session 15) — DONE

### Webhook hardening (the deferred half of SECURITY_FIXES finding 4)
`/pay/webhook` passed `entity["id"]`, `entity["order_id"]` and `link_entity["id"]` straight from the
JSON body into Mongo lookups — the same shape as the callback bug, where a dict is read as a query
OPERATOR and can match an arbitrary payment record. All three are now coerced to str-or-None
before use, and a non-dict top-level body returns early instead of raising.

Honest severity: this body is verified against the raw bytes with an HMAC signature BEFORE it is
parsed, so nobody can reach the parser without the webhook secret. This is defence in depth, not a
reachable hole — the tests say so explicitly, and because the signature gate means operator
payloads never reach the parser, one test asserts the coercion against the source so the class
can't pass with the fix reverted.

### Signup abuse guard — free-credit farming
**What the finding actually is, checked before building anything:** free credits live on the USER
(`users.free_credits_remaining`), not the device, so rotating a device id on its own grants nothing
— and an anonymous caller gets 0. The real farming path is creating another ACCOUNT: every
disposable email address is worth `FREE_CREDITS_ON_SIGNUP = 10` real LLM analyses. The device id is
a signal for catching that, not the vulnerability itself.

Two caps, in `wallet.py`, enforced by `signup_free_credits()` in `routes/auth_routes.py` and applied
at all THREE account-creation sites (OTP, Google, Apple):
- `FREE_CREDIT_GRANTS_PER_DEVICE = 1` — a device may seed a free-credit grant once, ever.
- `FREE_CREDIT_GRANTS_PER_IP_PER_DAY = 5` — the backstop for the named attack, rotating the device
  id every time. Per day, not forever, so an office isn't permanently barred.
Grants are recorded in a new `free_credit_grants` collection (`{user_id, device_id, ip,
created_at}`, indexed on `device_id` and `(ip, created_at)`) and ONLY when credits were actually
granted — a withheld grant can't inflate the counter that denied it.

**The deliberate shape: exceeding a cap costs FREE CREDITS, never ACCESS.** The account is created
and fully usable, it just starts at zero and pays like everyone else. Blocking sign-in on a shared
office or carrier-NAT address would lock out genuine users — a far worse outcome than someone
getting ten free analyses. A missing device id is likewise not treated as abuse.

**IP source verified against this deployment, not assumed**: probed the live ingress, which sends
`x-forwarded-for: <client>, <cloudflare>, <load-balancer>` and no trusted single-client header, so
the left-most entry is the right one to read. It is client-supplied and therefore spoofable —
documented in the code as the reason it is one of two signals and the reason a breach costs credits
rather than access. (The temporary probe endpoint used to confirm this was removed; `git diff` on
`routes/market.py` is clean.)

- Tests: `backend/tests/test_signup_abuse_and_webhook.py` (14) — a genuine first signup unaffected,
  a signup with no device id unaffected, re-signing-in never re-granting, a second account from the
  same device getting 0, a withheld grant recording nothing, device-id rotation caught by the
  address cap at exactly the cap, an innocent address unaffected by someone else's abuse,
  three-day-old grants not counting, four operator payloads settling nothing, an unsigned webhook
  always rejected, and the coercion asserted in source. Full suite: **446 passed, 9 skipped**.

## SECURITY_FIXES_PART2 section 1 — findings 5, 7, 9, 10, 11, 12, 13 (2026-06-20, session 16) — DONE
The document's diff was again written against the old monolithic `server.py` and had **never been
applied here** — verified by grepping for each fix before touching anything. Only finding 12's
holdings cap already existed (added during the session-13 audit). Applied one at a time, each
adapted to the current file layout and tested before moving on.

- **F5 — OTP pumping** (`routes/auth_routes.py`): added `OTP_MAX_PER_IP_PER_HOUR = 20` alongside the
  existing 5/hour per-identifier limit, reusing `au.is_rate_limited`; the request's IP is now stored
  on the `otp_requests` row so the limit has something to count. Per-identifier limiting never fires
  against an attacker cycling fresh addresses. Honest scope: per-IP, not global — a distributed
  attacker needs a shared counter, which is a separate decision.
- **F7 — free-credit farming via address variants** (`auth.py`): `normalize_identifier` now
  canonicalizes Gmail/Googlemail (strips dots and `+tag`) and refuses a starter list of disposable
  domains. Only Gmail, because dot-insensitivity is Gmail-specific. A dots-only local part is
  refused rather than collapsing to `@gmail.com`.
  **Regression this could have caused, found and closed**: anyone already stored as
  `first.last@gmail.com` would no longer be found by the canonical lookup and would be handed a
  brand-new empty account — silently losing a paid wallet balance. `find_or_create_user` now falls
  back to the pre-canonical address and returns the existing record untouched. Checked the live DB:
  0 of 3 Gmail accounts here would have changed identity, but the deployed database is a different
  one, so the fallback matters. Test asserts the same account id AND the same balance.
- **F9 — a payment could be permanently lost** (`routes/payments.py`): the webhook now does a
  duplicate *check* up front and records the event only at the end of each completed path (5 call
  sites; the malformed-body early return deliberately does NOT mark, since nothing was handled).
  Recording it first meant a crash mid-processing answered Razorpay's retry "duplicate" forever.
  `credit_wallet_once` now rolls back its ledger claim if the balance increment raises — otherwise
  the unique index blocks every future retry while the money was never added.
- **F10 — `/pay/status` ownership**: takes an optional `device_id` and the check is unconditional
  (`if not key or ...`) instead of skipped whenever the key was falsy. **Verified against the real
  deployment: not exploitable today** — the endpoint already sits behind a mandatory session, so an
  anonymous caller is refused at the auth gate. The fix matters because the hole would reopen the
  moment `AUTH_REQUIRED_ENABLED` were turned off. Tests assert 401 anonymous, 403 for another
  account's order, 200 for the owner, and that a supplied `device_id` can't override a session.
- **F11 — anonymous `device_id` naming an account wallet** (`deps.py`): `wallet_key_for` rejects a
  client-supplied id starting with `user:`, fixed in the one shared function so every endpoint is
  covered. (`/wallet/balance` already required auth, so this too was defence for the
  auth-disabled configuration.)
- **F12 — unbounded work/cache**: holdings cap already present; the news cache now evicts its 100
  oldest entries at 500, instead of growing by one per distinct symbol forever on a login-free
  endpoint that makes a real LLM call on a miss. Not done, flagged not decided: requiring auth on
  `/news/{symbol}` would break anonymous ticker browsing — a product call.
- **F13 — symbol validation**: `TICKER_RE` now lives in `market_data.py` (the layer that builds the
  Yahoo URLs) and is applied on `/quote`, `/chart`, `/ohlc`, `/news` and the portfolio holdings.
  **Matched on the UPPER-CASED form** — the app does request lowercase symbols, and rejecting those
  would have been a regression, not a fix (caught while testing).
- **One existing test updated for intended behaviour, not to hide a break**:
  `test_ohlc_bad_symbol` expected 404 for `THIS_DOES_NOT_EXIST_XYZ`; that string isn't a ticker
  shape, so it is now a 400 before any lookup. Renamed to `test_ohlc_malformed_symbol`, and a new
  `test_ohlc_unknown_but_wellformed_symbol` keeps the original intent (a plausible but non-existent
  ticker still 404s).

**Finding 14 deliberately untouched**, per the document: the FastAPI + Starlette upgrade needs its
own tested pass. `requirements.txt` was not edited.

- Tests: `backend/tests/test_security_part2.py` (67). Full suite: **514 passed, 9 skipped**.

### Still open after all three documents
F14 (FastAPI/Starlette upgrade), F8 (chart scripts sharing the web origin — frontend), and the
low-severity list: CORS scope, `public_base()` forwarded-header trust, empty-KEY_SECRET HMAC edge
case, internal error text reaching clients, non-atomic OTP attempt counting, `Linking.openURL`
scheme checks, inline `<script>` escaping in the chart components, unused requirements, and
NaN/Infinity floats 500-ing portfolio input.

## UPI fix + FastAPI upgrade + chart isolation + Live P/L + RTL (2026-06-21, session 15) — DONE

### 1. "GPay and UPI are not visible on Razorpay" — the real cause was the WebView
The user's wallet was already INR-locked and the page already showed ₹ amounts, so the currency
wasn't the problem. Razorpay's own account was checked against `GET /v1/methods`: `upi: true`,
`upi_intent: true` — UPI is enabled. What was wrong is WHERE the page opened. UPI / Google Pay pay
by handing off to the UPI app through an Android app intent, which a bare `<WebView>` cannot
launch, so Razorpay's checkout hides those methods inside one. `WalletCard` now opens the hosted
link with `WebBrowser.openBrowserAsync` (Chrome Custom Tab / SFSafariViewController, which CAN hand
off), falling back to `openExternalUrl`. The in-app SECURE CHECKOUT modal and the
`react-native-webview` import are gone; the existing `/pay/status` polling still settles the
payment when the browser is dismissed. **Only verifiable on a real device** — Expo Go's web
preview can't show a UPI hand-off.

### 2. Currency is now decided, not asked (user: "INR for Indian users and $ USD for anyone else")
- `routes/payments.suggest_currency(user, region)` — INR when the account's VERIFIED `phone` (or
  the `billing_phone` given for a receipt) starts with `+91`, else when the device region is IN,
  else USD. Consulted ONLY for a wallet with no locked currency; a locked wallet still ignores the
  region entirely, so a real $12.50 balance can never be reinterpreted as ₹12.50 (test asserts the
  balance too, not just the code).
- `/wallet/balance` takes `region` and reports the currency the wallet WILL get, so the packs on
  screen are the packs that get charged. `/pay/order` resolves the same way when the app sends no
  currency.
- `api.ts` sends the device region from `expo-localization` on both calls; the one-time
  "$ USD / ₹ INR" prompt (`currency-choice`) is deleted — the app holds no currency knowledge.
- Tests: `tests/test_currency_by_region.py` (16).

### 3. F14 — FastAPI 0.110.1 → 0.141.1, Starlette 0.37.2 → 1.6.0, uvicorn 0.25 → 0.38
Installed, `pip freeze`d, full suite re-run: zero behaviour changes. Pydantic untouched.

### 4. F8 — chart code no longer shares the app's origin
Both chart iframes used `sandbox="allow-scripts allow-same-origin"`, and `allow-scripts` +
`allow-same-origin` together is not a sandbox at all: a `srcDoc` document inherits the parent
origin, so the TradingView bundle and the unpkg Lightweight Charts script could read the session
token out of `localStorage` on web. `allow-same-origin` removed from both (TradingView keeps
`allow-popups`); verified both still paint. `src/utils/scriptJson.ts` escapes `<`, `>` and U+2028/9
in the JSON inlined into the `<script>` blocks, so a value containing `</script` can't break out.

### 5. Hardening
- `deps.public_base()` only trusts a `Host` / `X-Forwarded-Host` that matches `PUBLIC_BASE_URL`,
  localhost, or a suffix in `PUBLIC_HOST_SUFFIXES` (default `emergentagent.com,tradingagents.in`);
  anything else falls back to the configured base. Without it, `X-Forwarded-Host: evil.example`
  made Razorpay send a paying customer to an attacker's page after checkout. The allowlist is
  suffix-anchored, so `emergentagent.com.evil.example` is refused.
- Atomic OTP attempt counting: `find_one_and_update` with `$inc`, so N parallel wrong guesses cost
  N attempts (read-then-write let a burst all record the same number and spend the cap repeatedly).
- `RequestValidationError` handler returns only loc/msg/type. NaN / Infinity in a portfolio holding
  used to 500 — not from the maths but because the default handler echoes the input and those
  values can't be serialized into a JSON response. `quantity` / `avg_price` / `cash` are now
  `allow_inf_nan=False` (422), and no request content bounces back to the caller any more.
- Optimizer failures return a fixed sentence; the library's exception text (internal matrices, file
  paths) goes to the log only.
- CORS narrowed to the verbs and headers this API actually uses (`allow_origins` stays `*` —
  sessions are bearer tokens and `allow_credentials` is False).
- `src/utils/openExternalUrl.ts` refuses anything that isn't http(s), used for the two URLs that
  come from the network (Yahoo headlines, the Razorpay link).
- Tests: `tests/test_hardening_iter21.py` (18).

### 6. Live P/L on the Portfolio screen
Per-holding live price, market value and gain/loss vs average price (coloured, with %), plus an
INVESTED / MARKET VALUE / TOTAL P/L block and pull-to-refresh (quotes also refresh on focus).
**The total only appears when every priced holding shares one currency** — an NSE holding and a US
one added together would be a fabricated number, so that case shows `pl-mixed-note` instead. A
symbol whose quote call fails keeps its last price rather than blanking the panel.
The Portfolio tab itself is still hidden (`href: null` in `(tabs)/_layout.tsx`); the screen is
reachable at `/portfolio`. **Open question for the user: unhide it?**

### 7. RTL support, with Arabic so it's real rather than theoretical
- `src/i18n/locales/ar.json` (all 68 keys; BUY/SELL/HOLD stay English by design) and `ar` added to
  `SUPPORTED_LANGUAGES` here and in `pipeline.SUPPORTED_LANGUAGES`, so agents answer in Arabic
  while JSON keys and enums stay English.
- `RTL_LANGUAGES` + `applyLayoutDirection()` drive `I18nManager.allowRTL/forceRTL`. React Native
  fixes the direction when the view tree is built, so `setLanguage` now returns whether a relaunch
  is needed and `LanguagePicker` says so (`language-restart-note`) instead of leaving the user with
  Arabic text in an unflipped layout. There is no `expo-updates` in this project to reload for them.
- Physical style props swapped for logical ones (`marginStart/End`, `paddingStart/End`,
  `borderStartWidth/Color`…) across the 13 files that used them, so the layout actually mirrors.
  Two exceptions are deliberate and commented: the 52-week and Fear&Greed markers keep `marginLeft`
  because they are positioned with a physical `left: %`.

- Verified by the testing agent (iter20): 554 passed / 9 skipped, Live P/L numbers and the
  mixed-currency note, no `currency-choice` in the wallet card, Arabic switch + restart note, and
  both chart iframes still painting with `allow-same-origin` gone.

## "Signed in as someone else's gmail" — identity display bug (2026-06-21, session 15) — FIXED
The user signed in with a phone number and the ACCOUNT card showed
`lloydmasih1976@gmail.com`. Not an auth bug — the right account was logged in. Two causes, both
closed:
- **Bad data**: before the session-9 SEC-001 fix, `/pay/order` wrote the email a user typed for
  their Razorpay receipt straight onto `users.email`. Their phone-signup record therefore held an
  address they never verified. A startup migration (`server.migrate_unverified_billing_email`)
  moves that email to `billing_email` for accounts that have a phone, an email, and NO
  Google/Apple identity — exactly the accounts whose email cannot have come from a verified
  sign-in. Google and email-OTP accounts are untouched (asserted in tests, since that half is what
  could silently lock someone out of their own account). Idempotent, keeps an existing
  `billing_email`, ran on the preview DB and moved 3 rows.
- **The app was guessing**: `AccountCard` rendered `user.email || user.phone`. The backend now
  decides: `identity_type_for()` / `identity_for()` in `routes/auth_routes.py`, returned as
  `identity` + `identity_type` from `/auth/me` and from all three login responses. New accounts
  store `identity_type` at creation; older ones are inferred (Google/Apple ⇒ email, otherwise a
  stored phone is the only field that can be a verified sign-in).
- Tests: `tests/test_identity_display.py` (8). Verified end-to-end by the testing agent
  (iteration_21): a real phone sign-in shows the phone, an email sign-in shows the email, and the
  reported account id now reports `+918291026526`.

## FINDINGS_4_14_REVERIFIED — the two genuinely open findings (2026-06-21, session 15) — DONE
The document's own verification was right: of F4–F14, nine were already closed on this tip
(F4, F5, F6, F8, F10, F11, F12, F13, F14 — the last three closed earlier today). Re-checked each
against the real files before touching anything. Two were open, both applied as written:

### F7 — account deletion reset the free-credit grant
`delete_account` removed the user record, so signing back in with the same address ran
`find_or_create_user`, found nothing, and minted a brand-new account with a fresh
`FREE_CREDITS_ON_SIGNUP = 10` — worth ten real LLM analyses, repeatable forever. The existing
device/IP caps don't help: both signals are trivially rotated, while the address is the one thing
the attack still needs.
- `free_credit_tombstone_hash_for()` — the same keyed-HMAC pattern as `owner_hash_for`, keyed on
  the identifier instead of an account id, so the tombstone outlives the account **without storing
  the address**. `delete_account` upserts one (`$setOnInsert`, so a second deletion can't refresh
  the timestamp); `signup_free_credits` checks it FIRST and returns 0 regardless of device or
  network. New `free_credit_tombstones` collection, unique index on `hash`.
- **Extended beyond the document's diff, deliberately**: the diff only patched the OTP path, and
  the identical exploit exists via Google (delete, sign in with Google again). The identifier is now
  passed at all three account-creation sites — same function, same logic, no new behaviour. A test
  asserts all three call sites pass it, so a future sign-in route can't silently reopen the hole.
  Apple can withhold the address (private relay); there is then nothing to key on and the device/IP
  caps are all that apply — `signup_free_credits` skips the check on a falsy identifier rather than
  treating a missing signal as abuse.
- **Known limit, not papered over**: the tombstone is keyed on the identifier AS STORED by each
  path. An email-OTP address is Gmail-canonicalized, a Google address is not, so deleting a Google
  account and re-signing-up by email OTP with the same address would still grant. Closing that
  means canonicalizing at the Google path too — a behaviour change, so not done unasked.
- `delete_account` tombstones `identity_for(user)` (the sign-in identity) rather than
  `email or phone`: after today's identity fix, `email` can hold a payment-receipt address, which is
  not what the next sign-in is looked up by. Asserted by a test.

### F9 — `credit_iap_once` was missing `credit_wallet_once`'s rollback
It inserted the ledger row first but had no compensating delete, so a failed balance increment
would leave a row claiming the Apple purchase was credited while the money was never added — and
the unique index on `payment_id` then blocks every retry from ever fixing it. The identical,
already-proven `try/except: delete_one(...); raise` from `credit_wallet_once` applied verbatim.
Test injects a failing wallet write (at the DATABASE level — motor rebuilds the collection object
on every attribute access, so patching `db.wallets.update_one` silently does nothing), asserts no
ledger row and no wallet survive, then asserts the retry credits exactly 5.00 once.

### Closed the document's "unresolved limitation": the SRI hash
It couldn't compute one without network access and rightly refused to fabricate one. Computed from
the real file (`openssl dgst -sha384`, byte-identical across refetches) and pinned on the
`lightweight-charts@4.2.3` CDN tag with `crossorigin="anonymous"`. Verified in a browser inside the
same `sandbox="allow-scripts"` iframe the app uses: the real hash renders a chart, a tampered hash
gives `SRI_BLOCKED` — so it is genuinely enforced, not an ignored attribute. TradingView's `tv.js`
is unversioned and changes under us, so it cannot be pinned; the sandbox is its only containment.

- Tests: `backend/tests/test_findings_4_14.py` (12). Also fixed a test-infra trap the new async
  tests exposed: `tests/async_loop.py` now provides ONE process-wide event loop, because motor hands
  each operation to whichever loop is current and `asyncio.run` closes its loop on exit, so the
  second `asyncio.run` anywhere in a worker died with "Event loop is closed".
  Full suite: **575 passed, 9 skipped**.

### Still open from that document (untouched, not in scope)
F15 (currency race — the report's own top priority), F16 (launch-free counter race), F17 (sandbox
IAP cap), F18 (refund clawback), F19 (committed Razorpay test secret — rotate regardless), the
`/news` authentication product decision, and the remaining low-severity list.

## F15 currency race + refund clawback + committed-secret cleanup (2026-06-21, session 15) — DONE

### F15 — the currency race (the report's own top priority)
`/pay/order` read the wallet, decided a currency, then wrote it — three steps. Two first top-ups
arriving together both saw an unlocked wallet and both wrote, last writer winning, while the OTHER
customer's payment link had already been created in the losing currency. That link then settled
into a wallet locked to the other currency, and `credit_wallet_once` added the bare number with no
currency check at all: **₹99 could add 99 to a USD balance** (≈$99 of analyses for ₹99), or $5
could add 5 to an INR balance (≈1/80th of what was paid).
- **Atomic claim**: the wallet doc is ensured with `$setOnInsert`, then the currency is claimed with
  `find_one_and_update({"device_id": key, "currency": {"$in": [None, ""]}}, ...)`. Only the request
  that actually sets the field uses its own choice; every other one re-reads and ADOPTS what is
  locked, so the link is always created in the currency the wallet really has (and the pack
  validation then runs against that same currency — asked for $5 on an INR-locked wallet, the user
  gets the ₹ packs in the error, not a mispriced link).
- **Damage control at credit time**: `credit_wallet_once` now compares the payment's currency with
  the wallet's locked currency and, on a mismatch, credits NOTHING and marks the payment
  `status: "currency_mismatch"`, `needs_review: true`. Refusing beats guessing a conversion rate.
- **Ledger bug found while fixing it**: every ledger row was written with a hardcoded
  `wal.CURRENCY` ("USD"), so INR top-ups were recorded as dollars — wrong in the books, and the
  refund path reads these rows back. Now records what was actually paid, plus `source: "razorpay"`.
- A first top-up into a wallet with no currency also locks it, matching what the IAP path does.
- Tests: `tests/test_currency_race_and_refunds.py` — 20 wanting INR and 20 wanting USD in parallel
  produce exactly ONE currency and one wallet doc; a second order adopts the lock instead of its own
  region; the mismatch guard credits nothing and flags; the ledger records INR.

### F18 — refund and chargeback clawback
A refunded payment left the credit on the balance, so **a refund was a free top-up**.
`claw_back_once()` mirrors the credit path exactly: same `wallet_ledger`, same unique index on
`payment_id` (so `refund.created` and `refund.processed` for one refund claw back once, whichever
arrives first), a NEGATIVE row so the ledger still sums to the balance, and the same compensating
rollback if the wallet write fails.
- **A balance can be lower than the refund** — the analyses were already run and the LLM already
  paid for. We take what is there, set the balance to 0 and record `requested` / `shortfall` on the
  row. A negative balance would lock a user out of the app over someone else's refund.
- Keyed on the REFUND id, not the payment id: partial refunds are each their own clawback, and the
  payment is marked `refunded` or `partially_refunded` accordingly.
- Chargebacks (`payment.dispute.lost`) claw back the full payment — the bank took the money and
  there is no refund entity.
- Apple/RevenueCat: refunds arrive as `CANCELLATION` (confirmed against RevenueCat's docs — there
  is no `REFUND` type, though one is accepted so a future rename can't silently stop the clawback).
  `cancel_reason: UNSUBSCRIBE` is auto-renew being turned off — no money moved, so nothing is taken
  back. The amount comes from **our own ledger row**, not the price table, since the pack price may
  have changed and the only honest amount to reverse is the one that was added.
- A refund for a payment we never credited (unknown payment, sandbox cap, another product) is a
  logged no-op, answered 200 so the provider stops retrying.
- **Owner action required**: `refund.created`, `refund.processed` and `payment.dispute.lost` must be
  ticked on the Razorpay dashboard webhook (same secret as `RAZORPAY_WEBHOOK_SECRET`), or none of
  these events ever arrive.

### F19 — the committed Razorpay secret
Found it: `test_reports/iteration_15.json` (tracked) contained a test key id AND secret pasted in by
a testing agent. Redacted, along with a live key ID and the RevenueCat public SDK key in
`memory/PRD.md` / `memory/test_credentials.md`.
- **Checked every one of the 75 commits** for the values currently in `backend/.env`: the live
  Razorpay key id/secret, `JWT_SECRET`, Twilio auth token, Emergent LLM key and the RevenueCat
  webhook auth were **never committed**. Only the older TEST credentials were.
- **Still required from the owner**: rotate that test key in the Razorpay dashboard. Redacting the
  working tree does not remove it from git history, and rewriting history here would be worse than
  the exposure.
- `tests/test_no_committed_secrets.py` (8) is the guard: it scans every tracked file for
  secret-shaped strings AND for the real current `.env` values, asserts the env files are untracked,
  and asserts the scan itself actually read something (so a broken `git ls-files` can't make it pass
  vacuously).

- Full suite: **603 passed, 9 skipped**.

## Key-security audit (2026-06-21, session 15) — DONE
User declined further Razorpay dashboard changes and asked only that the keys be secure. Audited
every path a credential can actually escape by, rather than re-reading the code:
- **The downloadable app bundle** (the one that matters most — anything in it is published): pulled
  the real 19 MB web bundle from the preview URL and searched it for all 10 credential values in
  `backend/.env`. **None present.** Only the app name and the public backend URL, both non-secret.
- **API responses**: probed 10 endpoints (`/api/`, wallet, quote, news, pay/status, analyze,
  `/openapi.json`, `/docs`) for the same 10 values. The only hit is the **RevenueCat iOS SDK key**
  on `/api/pay/iap/config`, which is public by design — it ships inside every App Store binary and
  can only start a purchase, never read or move money. Left served from the backend deliberately so
  it can be rotated without a new App Store build.
- **FastAPI's own docs are not reachable**: `/api/docs`, `/api/redoc`, `/api/openapi.json` are all
  404, and non-`/api` paths go to the frontend, so the API surface isn't published either.
- **Logs**: no secret present. Twilio's SDK was logging its request URL at INFO, which embeds the
  account SID (an identifier, not the secret — the auth token is never logged); its logger is now
  WARNING. No OTP code has ever been logged. The only phone number in the logs is the admin's, and
  only because admin account ids are literally `admin-<identifier>`.
- **Flags**: `AUTH_DEBUG_RETURN_OTP=false`, `AUTH_REQUIRED_ENABLED=true`,
  `WALLET_ENFORCEMENT_ENABLED=true`. `backend/.env` is untracked and now `chmod 600`.
- `tests/test_no_committed_secrets.py` grew to 11 and **immediately earned its keep**: it failed on
  `test_reports/iteration_22.json`, where the testing agent had pasted the Razorpay key ids back in
  while verifying the previous redaction. Redacted. It now also asserts no backend credential is
  referenced from `frontend/`, that every `EXPO_PUBLIC_*` value is just a URL, and that the debug
  switches are off.
- Full suite: **606 passed, 9 skipped**.

### Still owner-side (unchanged)
Rotate the OLD Razorpay TEST key — it remains in git history. The live secret was never committed.
The Razorpay webhook URL still points at the PREVIEW host: after publishing, refunds and
chargebacks for real customers would be processed against the preview database. Top-ups still
settle on production through `/pay/status` polling, so this affects clawbacks only. User chose to
leave the dashboard as it is.

## Razorpay live-key rotation (2026-06-21, session 15) — DONE
User rotated their Razorpay keys and supplied the new pair; wired into `backend/.env` (only those
two lines edited). Verified rather than assumed:
- Authenticates against `/v1/payments`; `/v1/methods` confirms `upi: true`, `upi_intent: true`, so
  the UPI fix still applies to the new key.
- **Same merchant account** (`owner_id TbmS3S5JAvaeYU` on the webhook listing), so the webhook and
  `RAZORPAY_WEBHOOK_SECRET` are untouched and the three refund events stay enabled. Pre-rotation
  payment links and a 16 Sept captured payment are still readable with the new key, so no customer
  mid-payment is stranded — checked explicitly because a key that belonged to a DIFFERENT account
  would have silently orphaned every open link.
- End-to-end: a real ₹99 INR link created through `/api/pay/order` and then cancelled.
- Unpaid links carry a ~1h `expire_by` and self-close; 427 historical test links were already
  closed/unreachable, so nothing needed tidying.
- Full suite still **606 passed / 9 skipped** (two LLM-load flakes under xdist passed alone).
- **Told the user**: production still holds the OLD key until they redeploy, so a deployed build
  would fail Razorpay calls until then; and the new secret was pasted into chat, so if they want it
  out of any transcript they should rotate once more and set it from the deployment panel.

## Razorpay 502 on checkout (RAZORPAY_502_NEW_USERS.md) — FIXED from the log, not the guess
The uploaded report named the Razorpay customer `name` field as the likely cause and — to its
credit — insisted the real log line be read before closing it out. It was right to insist. **The
log already had the answer on every failure, and it was not the name:**

```
38x  BAD_REQUEST_ERROR: Too many requests                                  <- Razorpay throttling
 4x  BAD_REQUEST_ERROR: Recurring digits in customer contact are disallowed <- the phone typed
 6x  400 with an EMPTY description                                          <- unreadable
 0x  anything about the name
```

### 1. Throttling — 38 of 48 recorded 502s
A brand-new payment link was created on every tap, so tapping ₹99 twice, or backing out of the
browser and tapping again (what people actually do), asked Razorpay for another link each time
until it throttled the account. `create_topup_order` now **reuses the open link** for the same
wallet + amount + currency when one exists and hasn't expired, which also makes the second tap
instant because it skips the network call entirely. Reuse is deliberately narrow: never across
amounts (would charge the wrong money), never across accounts, never a `captured` link, and never
past `expires_at` — a new field read back from Razorpay's own `expire_by` rather than recomputed,
so a reused link can't be one Razorpay has already closed. `razorpay_request` also retries
throttling **once** after 700ms; nothing else is ever retried, because a rejected field would fail
identically and a payment must not be created twice on a guess.

### 2. The phone — why it looked like "new users specifically"
Only a user with no phone on their account gets asked for one, and a made-up `9999999999` is what
people type into a field they didn't expect. Razorpay rejects the entire payment-link request over
it, which surfaced as a 502 that said nothing about the number they'd just entered.
`contact_rejection()` now catches that shape (≤2 distinct digits, sequential runs, too short)
before any API call and returns `400 contact_invalid:phone:<plain English>`; the app keeps the
sheet open with the reason **on the field**. Deliberately narrow — falsely rejecting a real
customer's number would be a worse bug than the one being fixed, so real Indian/US/UK/SG numbers
are asserted to pass. `WalletCard.submitContact` mirrors the rule client-side so the common case
never needs the round trip at all.

### 3. Errors that could not be read or acted on
- Razorpay error text naming a customer field (`contact`, `email`, `name`) → `400
  contact_invalid:<field>:<Razorpay's own words>`, so even a rejection our own check misses becomes
  a fixable field error instead of a 502.
- Throttling → `503 busy:Payments are busy for a moment — tap again in a few seconds.` Nothing is
  wrong with the request, so the message no longer implies there is.
- Everything else stays a 502 with Razorpay's description — we don't blame the customer for our
  own problems (an auth failure is asserted to stay a 502).
- An empty error body can no longer log nothing: the fallback includes the status code and the raw
  body, which is the whole reason this cause had to be guessed the first time.

### The name, honestly
`sanitize_customer_name()` was still added (letters/spaces, 3-50, generic fallback) because Google
sign-ins DO store a provider name verbatim, so the report's concern is plausible — but it is
insurance, and the comment in the code says so rather than implying it was the fix.

- Tests: `tests/test_checkout_502.py` (43). Full suite **649 passed, 9 skipped**.
- Verified by the testing agent (iteration_23): 43/43 plus 13 independent checks, and the real UI
  flow — fresh email sign-in, tap a pack, type 9999999999, sheet stays open with the inline error,
  replace with a real number, checkout opens. Their code audit confirms no remaining path turns
  user input into a 502.
- **A trap worth remembering**: patching `rzp.create_payment_link` in the test process does nothing
  to the running server. My first version of the three error-mapping tests did that and created
  REAL live payment links while asserting a 502. They now call the route function in-process
  (`call_route`), and the stray links were cancelled.

## "Payments aren't switched on yet" on the deployed build (2026-06-21, session 15) — root-caused, made self-reporting
The user published, then sent a screenshot of the deployed app: no balance, no packs,
"Usage-based pricing isn't active in this build yet", "Payments aren't switched on yet". Their
reasonable read was that the Razorpay work hadn't taken effect.

**What the screenshot actually showed.** The dash beside WALLET is our own `wallet == null`
marker, so `GET /api/wallet/balance` had FAILED on their device. `WalletCard.refresh()` swallowed
it — `catch {} // supplementary display; fail quietly` — and a null wallet renders identically to a
wallet from a server with no payment config: no balance, no price line, no packs, footer saying
payments are off. **A transport failure was being presented to the user as a deliberate product
state**, which is why it looked like the fix hadn't landed.

**What I could and couldn't determine.** Verified from outside:
- Preview backend, that same account: ₹99, packs [99,199,499], `payments_live: true`, currency
  locked INR, identity correct. Healthy.
- Deployed backend (`trade-agent-app.emergent.host`): running that morning's code (a NaN payload to
  `/api/portfolio/optimize` returns my new 422 handler, not a 500), `/api/pay/iap/config` 200, ten
  consecutive `/wallet/balance` probes 401 in ~0.15s each, `/api/` 200, OTP POST 200 in ~1.5s.
  Stable and fast.
- I could NOT reproduce their failure: a deployed environment has its own `JWT_SECRET` and its own
  database, so no token can be minted for it from here, and the deployment logs available to me
  showed only health checks. Guessing a cause and calling it fixed would have been dishonest.

**So the fix is to make it diagnose itself, in one tap:**
- `WalletCard` now shows `wallet-load-error` — "Couldn't load your balance — <server's words>
  (HTTP <status>)" — plus a RETRY button. "Not authenticated (HTTP 401)" and "Not Found (HTTP 404)"
  look identical without the status and mean completely different things, and the user can only
  report what they can see.
- `api.ts` attaches `error.status` **without touching `error.message`**, because the checkout flow
  matches machine-readable prefixes (`contact_required:`, `contact_invalid:`, `busy:`) on the
  message — appending anything there would have broken the field-list parsing. Verified.
- New unauthenticated `GET /api/pay/health` → `{razorpay, razorpay_mode, razorpay_webhook_secret_set,
  apple_iap, currencies, wallet_enforcement}`, no secrets (a test greps the response for every
  `.env` value, and for `rzp_live_`/`rzp_test_`). Added because the half hour I spent unable to
  answer "are payments configured on THAT deployment" was the actual bottleneck.
- Reworded the two dead-end messages. A 200 response with no prices now says "Top-ups are
  unavailable right now. Your balance and free analyses still work." — that shape only happens when
  a build and its backend are out of step, and the old wording read like a decision rather than a
  mismatch.
- Tests: `tests/test_pay_health.py` (4). Full suite **653 passed, 9 skipped**. Verified by the
  testing agent (iteration_24): forced 401 shows the new error + status + RETRY, RETRY recovers,
  stripped prices show the new wording, and the checkout error protocol still parses cleanly.

**Reported by the testing agent, NOT fixed (same class of silent failure, worth a decision):**
`QuoteCard.tsx:39` sets the chart to null on failure, so a failed range switch looks like "no
chart"; `portfolio.tsx:237` keeps the previous price per symbol, so a persistent quote failure is
invisible. Both are read-only displays rather than money, so they were left alone.

**A mistake to not repeat:** while probing the deployed OTP endpoint I sent three real SMS codes to
+919812345678, a number used in tests. Probe with email identifiers, never a phone.

## Deployed payments 502 — ROOT CAUSED AND FIXED (2026-06-22, session 16) — DONE
The user reported, again, "HTTP 502 / couldn't start checkout" on the deployed build, right after
being asked to type an email at top-up. Reproduced first, not guessed: a fresh phone-signup account
on the PREVIEW backend goes 400 `contact_required:email` -> supplies email -> **200 with a real
Razorpay link** (created and cancelled). So the code path is fine and the difference is the
environment.

**Root cause (confirmed by the deployed container's own logs, via deployment_agent):**
`/api/pay/order` on the deployed host fails with Razorpay **"Authentication failed"** — it is still
running the OLD, deactivated key pair. And the reason the deploy never picked up the new one:
the root **`.gitignore` was excluding `.env` / `.env.*` / `*.env` again** (the deploy build context
is the repo, so the container shipped with a stale environment). Independent proof before touching
anything: `GET /api/pay/health` returned `razorpay_webhook_secret_set: false` on
`trade-agent-app.emergent.host` and `true` on the preview — two different environments.
- This is the THIRD time that .gitignore pattern has caused a production payment outage. Removed,
  and replaced with a comment saying why it must not come back, plus
  `tests/test_pay_health.py::test_env_files_are_not_git_ignored`, which runs
  `git check-ignore -v backend/.env` and fails the suite if the pattern reappears.
- **The user must REDEPLOY.** Verification is now one command, no log access needed:
  `curl https://<deployed>/api/pay/health` must show `razorpay_credentials_ok: true` and
  `razorpay_key_tail: "2LyM"` (the live pair in `backend/.env`; preview already reports both).

**Two supporting changes:**
- `/api/pay/health` gained `razorpay_credentials_ok` (the startup probe's verdict — null before it
  runs, false when Razorpay REJECTED this container's keys) and `razorpay_key_tail` (last 4 chars of
  the public key id, enough to tell a stale environment from a current one, still no secret).
  `rzp.CREDENTIALS_OK` is set by the startup probe in `server.py` and also flipped to False the
  moment `/pay/order` sees an auth failure.
- A Razorpay auth failure on `/pay/order` is now **503 "Payments are temporarily unavailable —
  nothing was charged. Please try again later."** It used to be `502 "Razorpay: Authentication
  failed"`, which the app showed verbatim — that reads to a customer like their own card was
  declined, when it is entirely our own broken credentials. Any OTHER Razorpay failure still returns
  the 502 with Razorpay's description (test split in two accordingly).

## Portfolio tab unhidden (2026-06-22, session 16) — DONE
`options={{ href: null }}` removed and `portfolio` added to the tab-bar map (Wallet icon,
lime accent, existing `tabs.portfolio` i18n key in all five locales). Five tabs now: ANALYZE /
HISTORY / PORTFOLIO / ALERTS / AGENTS. Screen code untouched. Verified by the testing agent
(iteration_25): all five render at 390px with no label clipping, `tab-portfolio` navigates, two
holdings added -> Live P/L per holding + totals -> RUN OPTIMIZER returns TRIM/ADD actions, zero
console errors, other four tabs unregressed. Pre-existing minor: the ADD HOLDING row clips below
400px until a field is focused.

## Test suite: 4m22s -> 2m33s (2026-06-22, session 16) — DONE
The suite was **110 seconds of pure network latency**. Most modules talk to the backend over
`EXPO_PUBLIC_BACKEND_URL`, i.e. the public preview host, so every request left the container and
crossed the ingress: measured **146ms vs 2ms** on the loopback, and the analysis-polling tests poll
for a minute each.
- `tests/conftest.py` (new) points `EXPO_PUBLIC_BACKEND_URL` at `http://localhost:8001` before the
  test modules are imported (they read it at import time, and conftest is imported first).
  `TEST_VIA_INGRESS=1` restores the old behaviour; `TEST_BACKEND_URL` overrides the target.
  Same backend process, so nothing about what is asserted changes.
- `tests/test_ingress.py` (new, 3) keeps the proxy path covered on purpose — `/api/*` reaching port
  8001, a quote, and `X-Forwarded-For` surviving the proxy — through the public host.
- **Measured, not assumed: more workers is the WRONG lever.** `-n 4 --dist loadscope` came out
  SLOWER (276s) *and* failed 5 tests, exactly the external rate-limiting the suite note warns
  about. `addopts` stays `-n 2 --dist loadscope`.
- Result: **660 passed, 9 skipped in 2m33s** (was 657 in 4m22s).

## The actual cause of the deployed 502: the key SECRET was in RAZORPAY_KEY_ID (2026-06-22, session 16)
After the user updated the deployment secrets, `/api/pay/health` on the deployed host read
`razorpay_key_tail: "2LyM"` (the RIGHT key id) but still `razorpay_credentials_ok: false`. At the
same time `backend/.env` in the workspace had been rewritten (mtime 8 minutes old) with
**`RAZORPAY_KEY_ID` set to the 24-char key SECRET** — the same value as `RAZORPAY_KEY_SECRET`. So
the secret had been pasted into the key-id field, in both places. Restored
`RAZORPAY_KEY_ID=rzp_live_Tds0LAGnVO2LyM`; the preview backend authenticates again
(`razorpay_credentials_ok: true`).
- That mistake is indistinguishable from an expired key from the outside: Razorpay answers
  "Authentication failed" either way, which is why it was chased as a stale-deploy problem.
  So it is now detected rather than diagnosed: `rzp.key_id_malformed()` (a key id always starts
  `rzp_live_` / `rzp_test_`), logged loudly at startup, and reported as
  `razorpay_key_id_malformed` by `/api/pay/health`.
- **The root `.gitignore` `.env` exclusion regenerated AGAIN** during this session and was caught
  by the new `test_env_files_are_not_git_ignored` rather than by a customer — exactly what it was
  written for. Removed again.
- A test of mine briefly contained the real key secret as a fixture value;
  `test_no_committed_secrets.py` failed the build over it, as designed. Replaced with a fake.
- Full suite: **662 passed, 9 skipped in 2m27s**.
- Still owner-side on the DEPLOYED environment: `RAZORPAY_KEY_SECRET` (the 24-char value, NOT the
  key id) and `RAZORPAY_WEBHOOK_SECRET` (48 chars) must be set in Deployment -> Secrets.
