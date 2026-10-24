# Chart-pattern scans + India VIX tile

Branch: `feature/pattern-scans`. Ideas from je-suis-tm/quant-trading (Apache 2.0); the code is written fresh, with tests.

## What users see (Scans screen)
- **India VIX tile** at the top of the INDIA tab: value, % change, and freshness (LIVE / 15-MIN DELAYED / AS OF time).
- **New section "CHART PATTERNS · FROM THE LAST CLOSE"** on all three tabs (India, US, Crypto), with 10 lists:
  - Shooting star, Hammer.
  - Bollinger breakout up / breakdown (only after a squeeze).
  - W-bottom (double low), M-top (double high).
  - Parabolic SAR flipped up / down.
  - Heikin-Ashi turned up / down.
- Each list says "CLOSE OF <date>". The titles describe the shape only; there is no buy/sell wording (a test guards this).

## Backend
- `backend/patterns.py`: pure functions. `detect(opens, highs, lows, closes)` returns `{candle, bb_break, bb_shape, sar_flip, ha_turn}`.
- `backend/scanner.py`:
  - `daily_snapshot` adds `patterns`.
  - New `PATTERN_SCANS` (kind `pattern` in the catalog).
  - `CLOSE_SCANS = DAILY_SCANS + PATTERN_SCANS`.
- `backend/scan_jobs.py`:
  - The post-close refresh runs `CLOSE_SCANS`.
  - Patterns are never recomputed with the live price, because a candle only exists once the session closes.
  - `refresh_vix`: Upstox `NSE_INDEX|India VIX` first, Yahoo `^INDIAVIX` (delayed) as the fallback.
    - In session it refreshes every 90 s, or every 15 min while on Yahoo.
    - After the NSE close it takes the closing value.
    - It is stored in `scan_meta._id = "india_vix"`. A failure keeps the last value.
- `backend/upstox_feed.py`: `fetch_india_vix()` (it shares the quote code with `fetch_quotes`).
- `GET /api/scans` now also returns `india_vix: {value, change_pct, as_of, source, delayed}`, or `null` before the first fetch.

## Tests
- `tests/test_patterns.py` has 13 tests.
- `tests/test_scanners.py` gains 9 tests: patterns in the snapshot and catalog, no live recompute, VIX from Upstox / Yahoo fallback / throttle / failure / closing value, and the index quote parse.

## Emergent: please verify
1. Pull `main` and restart the backend. No new env vars or packages are needed.
2. Run `pytest backend/tests/test_patterns.py backend/tests/test_scanners.py`.
3. Check the first daily refresh:
   - Wait for it, or trigger it by deleting the `scan_meta` docs `daily:in`, `daily:us` and `daily:crypto` (by ID only).
   - Confirm that `scan_results` contains `in.pat_*`, `us.pat_*` and `crypto.pat_*` docs with a `session_date`.
4. `GET /api/scans`: confirm `india_vix` has a value (source `upstox` if the token is set, otherwise `yahoo`/delayed), and that 30 scans have `kind: "pattern"`.
5. In the Expo preview, open Scans:
   - The VIX tile shows on INDIA only.
   - The CHART PATTERNS section shows on every tab.
   - The lists open, and tapping a stock starts an analysis.
6. Do not print the Upstox token. Only check that it is present.
