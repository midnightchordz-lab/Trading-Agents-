# 1-day change: follow-up to Emergent's fix (branch `fix/daily-change`)

Builds on `feature/portfolio-import` @ `4725d43` (your "Bug Fix Implemented for
1-Day Change" commit). Nothing to re-do by hand — fetch and fast-forward:

```bash
cd /app
git fetch https://github.com/midnightchordz-lab/trading-agents-.git fix/daily-change
git merge --ff-only FETCH_HEAD
```

Do not force-push, rebase or rewrite history. Save to GitHub from the UI as usual.

## What was still wrong after 4725d43

1. **Fallback picked the wrong day.** `spark[-2]` is taken after dropping null
   closes. Yahoo often returns today's bar with `close: null` (early in the
   session, and regularly on NSE), so `spark[-2]` became the close from TWO
   sessions ago. Example: closes `[100, 101, 102, null]`, price 104 ->
   previousClose 101 (wrong), should be 102.
2. **Algebra in the `regularMarketChangePercent` path.** `prev = price - price*pct/100`
   is not the previous close; it is `price / (1 + pct/100)`. At +5% on 105 that
   gave previousClose 99.75 and change 5.25 instead of 100 and 5.00.
   `changePercent` was right, `change` and `previousClose` were not.
3. **The 1M tab on the analysis screen** used `quote.changePercent`. That was
   accidentally a 1-month number before the fix; after it, the 1M chip showed
   the 1-day move next to a 1-month sparkline.

## What changed

- `backend/market_data.py`: new `_previous_session_close(result, meta)` — the
  close of the last bar whose trading DATE (exchange local time, via
  `meta.gmtoffset`) is before the date of `meta.regularMarketTime`. Robust to
  null bars, pre-market (shows the last session's move, as Yahoo does),
  weekends, NSE (IST) and crypto (UTC midnight bars). Only if there are no
  usable timestamps does it fall back to `regularMarketChangePercent`, with the
  correct algebra. `chartPreviousClose` is no longer used for the quote.
  `fetch_chart_sync` is unchanged (its 1D range uses range=1d, where
  chartPreviousClose IS yesterday's close; other ranges use the first point).
- `frontend/src/components/QuoteCard.tsx`: on the 1M tab, change/% are computed
  from the quote's own sparkline (first close to last). Other tabs and all the
  list/compare/portfolio cards keep showing the 1-day change.
- `backend/tests/test_quote_change.py`: 8 tests with fake Yahoo responses
  (live session, null today bar, pre-market, NSE, crypto, never the month-old
  close, percent fallback, no data -> None). Two of them fail on 4725d43.

## Verify

```bash
cd /app/backend && python -m pytest -q tests/test_quote_change.py tests/test_indicators.py \
  tests/test_pipeline_technicals.py tests/test_sms.py tests/test_portfolio_import.py \
  tests/test_portfolio_import_route_guard.py
```
Expected: 65 passed.

Then live, via `/api/market/quote/<sym>` for AAPL, RELIANCE.NS, BTC-USD, GC=F:
report `price`, `previousClose`, `change`, `changePercent` and check
`price - previousClose == change` and that the % matches Yahoo/Google's "today"
figure. On the analysis screen, tap 1D / 1W / 1M / 1Y and confirm each chip's
% matches its chart.

Still outstanding from before: the stored `technicals` JSON for the AAPL
analysis plus the last 10 daily bars (time, high, low, close).

No iOS build. Android build only after these checks pass.
