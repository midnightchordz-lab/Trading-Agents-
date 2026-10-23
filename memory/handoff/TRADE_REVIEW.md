# Trade Review (new REVIEW tab)

Branch `feature/trade-review` (built on `feature/health-check`, so it carries
Health Check, chart analysis and scanners too).

## What it is
A new bottom tab, **REVIEW** (separate from Portfolio). The user uploads their
own broker statements (PDF) and sees how they actually trade: results by
position size, trades per day, trade number in the day, after a loss, holding
time, entry time, days to expiry, weekday, market, underlying, month, plus a
tax-filing view (F&O turnover, net result, charges). Observations are
descriptive only — tested to contain no buy/sell/should/stop/avoid wording —
and every report ends with SEBI's F&O risk line. No AI call; pure arithmetic.

Free for now (rate-limited). Pricing (free preview + paid full report) is a
pending business decision.

### Added after comparing with Vibe-Trading's Shadow Account
1. **Winners vs losers · how long held** table + an observation when losers are held ≥1.3× longer (the disposition effect) — or the reverse.
2. **Share my review** button → image (react-native-view-shot + expo-sharing, already in the app) built from `report.share`: win rate, ₹-won-per-₹1-lost ratio, counts, green days and up to 3 percentage lines. **Never a rupee amount** (tested).
3. **Hindsight** card (`report.what_if`): the user's own positions recalculated, before charges — sizes capped at their median size; without re-entries within 30 min of a loss; only the first two positions of each day. Labelled hindsight, "not a forecast and not a suggestion"; wording tested for no advice terms.

## Supported statements (v1)
| Broker | Statement | Kind | Notes |
|---|---|---|---|
| Upstox | Trade report PDF | every fill with time | no charges in the file → shown "before charges" |
| Kotak Neo | Transaction statement PDF | every fill with time + exact charges/STT | rows wrap over 3–5 lines; parser stitches them |
| Kotak Neo | Gain/Loss statement PDF | one row per contract, exact | "Total" is already net of GST+brokerage+misc but not STT (verified against the transaction statement) |

### Excel / CSV (any broker) — added
`backend/tradebook_tabular.py` reads .xlsx and .csv by matching the heading row
loosely (Zerodha `trade_type`/`order_execution_time`/`expiry_date`, Upstox
`Side`/`Instrument Type`/`Strike Price`, Kotak `Transaction Type`, etc.). Two
shapes: a trade list (→ fills) or a per-contract P&L sheet with buy/sell value
(→ contracts; Zerodha P&L, Kotak gain/loss). Contract symbols are understood in
Zerodha monthly `NIFTY24OCT24500CE`, Zerodha weekly `NIFTY24O1024500CE`, Kotak
`OPTIDXNIFTY22SEP2026PE` and spaced `NIFTY 23000 PE 19-10-2026` styles, or from
separate underlying/expiry/strike/type columns. Commodity quantities without a
cash-value column are read as lots (× MCX lot size) only when not already a whole
multiple of the lot — the report then says so. Files with dates but no times get
no timing tables (and a note). Old .xls and macro workbooks are refused.
The real Excel exports from Upstox/Kotak/Zerodha have not been tried yet — only
synthetic sheets in their documented layouts; first real files should be checked.

Up to 3 files per run (8 MB each), any mix of PDF / .xlsx / .csv. Upload the Kotak transaction statement AND the
gain/loss statement for the same period → money figures come from the gain/loss
statement (exact), habits from the transaction file.

Verified on real statements (not in the repo): Upstox 318 fills, Kotak 583 fills,
Kotak gain/loss 252 contracts — the gain/loss gross matched the transaction
statement to the rupee, net within ₹4 rounding.

## Privacy
- Files are read into memory, parsed, and dropped. Nothing stored: not the file, the trades or the report.
- Statements include name, PAN and client code; the parsers never read them into output (tested).
- Play Console Data safety: add "Financial info — processed, not stored" for this feature.

## Files
- `backend/tradebook_parsers.py` — PDF → lines (pdfplumber), format detection, 3 parsers (work on text lines, so tests need no PDFs)
- `backend/trade_review.py` — positions (flat-to-flat per contract), tables, observations, tax view, report
- `backend/routes/trade_review.py` — `POST /api/trade-review` (multipart field `files`, up to 3), session required
- `backend/limits.py` — `RL_TRADE_REVIEW_PER_MIN`=3, `RL_TRADE_REVIEW_PER_DAY`=20, `RL_TRADE_REVIEW_GLOBAL_PER_MIN`=10
- `backend/requirements.txt` — **new dependency `pdfplumber==0.11.7`** (pulls pdfminer.six, pypdfium2)
- `backend/tests/test_trade_review.py` — 16 tests (synthetic lines in each broker's exact layout)
- `backend/tradebook_tabular.py` + `backend/tests/test_trade_review_sheets.py` — Excel/CSV, 17 tests
- `frontend/app/(tabs)/review.tsx` — the screen; `(tabs)/_layout.tsx` — REVIEW tab (Notebook icon); i18n `tabs.review` in en/hi/es/zh/ar; `src/api.ts` `tradeReview()` + `TradeReview` type; firebase event `trade_review`

## Please verify (Emergent)
1. `pip install -r backend/requirements.txt` (pdfplumber), restart backend.
2. `cd backend && python -m pytest tests/test_trade_review.py tests/test_portfolio_health.py tests/test_chart_analysis.py tests/test_scanners.py tests/test_trade_review_sheets.py -n 0 -q` → 126 passed.
3. App: SHARE MY REVIEW opens the image with no ₹ amounts; the share sheet opens on Android. The HINDSIGHT card shows under the observations.
4. App: the REVIEW tab appears between PORTFOLIO and ALERTS; the tab bar still fits on a small Android screen (6 tabs).
5. The owner will test with their own statements on the phone. Do NOT ask for, upload or paste anyone's real statement in chat.
6. Error paths: an old .xls → 400 asking for .xlsx/CSV; a sheet without the needed columns → 400 "Couldn't find the columns"; an unrelated PDF → 400 "doesn't look like a supported statement"; a password-protected PDF → 400 asking for one without a password.

Do not force-push or rewrite history.
