# Stock scanners (branch `feature/scanners`)

Builds on `feature/email-only-login` @ `d8ab4f7` (your latest code). Merge:

```bash
cd /app
git fetch https://github.com/midnightchordz-lab/trading-agents-.git feature/scanners
git merge --ff-only FETCH_HEAD || git merge --no-edit FETCH_HEAD
```
Never force-push or rebase. No new Python or JS dependencies.

## What it is

The new screen **MARKET SCANS** (opened from a row on the Analyze tab) lists stocks that match
factual conditions. Tapping a stock runs the normal desk analysis, which fetches the current price itself.

- **Daily scans** (India, US, crypto), computed from daily closes and refreshed every 6h:
  - near 52-week high, near 52-week low;
  - RSI below 30, RSI above 70;
  - MACD crossed up, MACD crossed down;
  - unusual volume (2× the 20-day average).
- **Live scans** (India, during the NSE session 9:15–15:30 IST):
  - top gainers, top losers;
  - new 52-week high today;
  - volume surge (2× normal pace, shown from about 30 minutes after the open).
- **Data sources:**
  - **Upstox** (Analytics Token, read-only) every 90s while the market is open.
  - If Upstox is unavailable, **Yahoo** every 15 min, marked `delayed: true`. The app then shows "15-MIN DELAYED".
- Every list carries `as_of`, `source` and `delayed`, so the app never shows an old list as live.
- Titles state facts only ("RSI below 30"), never buy/sell language. A test enforces this.

## Files

- `backend/scanner.py`: universe, daily snapshot, scan definitions, live rows, NSE session clock. Pure functions.
- `backend/upstox_feed.py`: the ONLY Upstox code.
  - Instrument list: `assets.upstox.com` NSE.json.gz, refreshed daily.
  - Batch quotes: `GET api.upstox.com/v2/market-quote/quotes`, up to 500 instruments per request.
  - Anything wrong raises `UpstoxUnavailable`, and the job falls back to Yahoo.
- `backend/scan_jobs.py`: the background loop, started at app startup.
  - A Mongo lease (`scan_meta`) means only one process runs it.
  - Results are stored in `scan_results`, snapshots in `scan_snapshots`.
  - A holiday is detected as no trade for 20 min during session hours; the last real lists are kept.
- `backend/routes/scans.py`: `GET /api/scans` (catalog plus counts and freshness) and `GET /api/scans/{id}` (items).
  Public and rate-limited like the market endpoints.
- `backend/market_data.py`: `fetch_quote_sync` now also returns `volume` (Yahoo `regularMarketVolume`).
- `backend/server.py`: registers the router and starts the loop. `SCANNER_ENABLED=false` turns it off.
- `frontend/app/scans.tsx` (new screen), `app/_layout.tsx` (route), `app/(tabs)/index.tsx` (entry row,
  `testID="open-scans"`), `src/api.ts` (`scans()`, `scan(id)`).
- `backend/tests/test_scanners.py`: 26 tests. Everything is offline: fake bars, fake quotes, fake DB.
  It never calls Upstox or Yahoo.

## Deployment secrets (the owner enters these directly — never in chat, code, commits or replies)

- `UPSTOX_ANALYTICS_TOKEN`: the owner's Upstox Analytics Token (valid until 20 Sep 2027).
  Without it, live scans run on delayed Yahoo data automatically.
- Optional: `SCAN_UNIVERSE_IN`, `SCAN_UNIVERSE_US`, `SCAN_UNIVERSE_CRYPTO` (space- or comma-separated)
  to change the stock lists without a release.
- Optional: `SCANNER_ENABLED=false` to switch the job off.

## Verify

```bash
cd /app/backend && sudo supervisorctl restart backend
python -m pytest -n 0 -q tests/test_scanners.py tests/test_no_committed_secrets.py
sleep 120   # first daily refresh takes ~1-2 minutes (one Yahoo request per asset, spaced out)
curl -s localhost:8001/api/scans | python3 -m json.tool | head -40
curl -s localhost:8001/api/scans/in.near_52w_high | python3 -m json.tool | head -30
```

Report the following:

1. Test summary (26 + secrets test).
2. For the daily scans: how many have `count > 0` and a non-null `as_of`.
   From the backend log, list any "daily bars failed for X" symbols.
3. If the NSE market is open when you test, report for `in.live_gainers`: `source` (should be `upstox` if the
   token is set), `delayed`, `as_of` and the first 3 items.
   - List any "upstox: no NSE instrument for …" symbols from the log.
   - Never print the token.
   - If the market is closed, say so; live lists stay empty until the next session.
4. Web preview checks:
   - The MARKET SCANS row on Analyze opens the screen.
   - The INDIA / US / CRYPTO tabs work.
   - Each card shows a freshness label (LIVE / 15-MIN DELAYED / UPDATED time / NOT YET AVAILABLE).
   - Expanding a card lists stocks.
   - Tapping one starts an analysis.

No iOS build. No secrets in commits or replies.

## Update (23 Oct): close-based timing and live trend scans

Two problems surfaced in the first live session, and both are fixed:

1. **Daily scans refreshed at 9:18 AM using today's unfinished candle.** "Unusual volume" compared 3 minutes of
   trading with a full day's average. The refresh was a 6-hour timer started whenever the server woke up.
   Now:
   - each market refreshes **once after its close** (`scanner.MARKET_CLOSE`):
     - India 15:45 IST on weekdays;
     - US 16:30 New York time on weekdays;
     - crypto 00:15 UTC for the previous day's candle.
   - `scanner.completed_bars` drops any bar for a session that hasn't closed.
   - Per-market state is in `scan_meta` `daily:<group>` (`{"close": iso}`). The old `daily` doc is unused.
   - Results carry `session_date`, and the app shows "CLOSE OF 22 OCT".
2. **RSI / momentum / 52-week lists weren't live in India.** During the NSE session, `refresh_live` now also
   recomputes them every 90s:
   - it uses today's live price (Upstox, or delayed Yahoo) as a provisional close;
   - see `scanner.intraday_trend_row` / `INTRADAY_TREND_SCANS`; unusual volume stays session-based.
   - Stored with `provisional: true`; the app shows "LIVE · TODAY SO FAR".
   - The post-close refresh replaces them with final values (`provisional: false`, `session_date` set).
   - Snapshots now store the last 300 completed `closes` for this.
3. `tick()` runs the live refresh **before** the daily rebuild, so a server waking mid-session isn't held back.

Tests: `tests/test_scanners.py` now has 35 tests, all offline.

### Verify (during NSE hours)

```bash
cd /app/backend && sudo supervisorctl restart backend
python -m pytest -n 0 -q tests/test_scanners.py   # 35 passed
sleep 150
```

1. For `in.rsi_oversold`, `in.rsi_overbought`, `in.macd_bullish`, `in.near_52w_high`, report:
   `provisional` (true), `source` (upstox), `as_of` (moving every ~90s) and `count`.
2. For `in.volume_spike`, `provisional` must be false and `session_date` must be the previous trading day.
3. For `us.*` and `crypto.*`, `session_date` must be set and `provisional` false.
4. In the app, India's trend section reads "TREND · RECALCULATED WITH TODAY'S LIVE PRICE", with
   "LIVE · TODAY SO FAR" badges. US and crypto show "CLOSE OF …".
5. **After 15:45 IST:** the India trend lists flip to `provisional: false` with today's `session_date`.
