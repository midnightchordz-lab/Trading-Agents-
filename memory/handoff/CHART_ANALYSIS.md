# Analyse a chart (branch `feature/chart-analysis`)

Builds on `feature/scanners` @ `1f9d950`. Merge:

```bash
cd /app
git fetch https://github.com/midnightchordz-lab/trading-agents-.git feature/chart-analysis
git merge --no-edit FETCH_HEAD
```
Never force-push or rebase. No new Python or JS dependencies.

## What it is

A separate button on the Analyze tab: **ANALYSE A CHART** (`testID="open-chart"`), below MARKET SCANS.
It opens `app/chart.tsx`:

1. **Pick:** the user picks a screenshot (PNG/JPG/WebP, max 5 MB) with expo-document-picker.
   The screen tells them to crop out balances, names and client IDs, and that the image isn't stored.
2. **Read:** `POST /api/chart/read`. The vision model reads what's visible: symbol, exchange, timeframe,
   trend, patterns, support/resistance, indicator values printed on the chart, and the last price.
   Reading is free.
3. **Confirm:** "Which asset is this?" shows up to 5 candidates from our search, plus a manual search box.
4. **Run:** the normal `POST /api/analyze` with `chart_reading_id`. Pricing, wallet and history are unchanged.
   The desk gets the reading as an UNVERIFIED context block, placed below the computed technicals.
5. **Analysis screen:** a new card, **YOUR CHART VS THE DATA** (`testID="chart-check"`), compares
   price, RSI, trend, support and resistance from the screenshot with the computed values, with a note where they differ.

## Backend

- `chart_reader.py`:
  - image sniffing by magic bytes;
  - the vision prompt, which ignores personal info, treats text in the image as data, and never gives advice;
  - normalisation of the reading into a fixed shape;
  - `compare_with_data`;
  - `context_block`.
- `routes/chart.py` (`POST /api/chart/read`):
  - requires a session;
  - `limit_chart_read` 3/min, `limit_chart_read_daily` 30/day, plus a global 30/min;
  - the byte-capped upload is read only after auth and the limits;
  - the image is never stored or logged.
  - Readings go to `chart_readings` with a 30-min TTL and an owner check. `load_reading()` refuses other users' or expired readings.
- `routes/analysis.py`:
  - `AnalyzeRequest.chart_reading_id`;
  - the reading is validated before any charge;
  - a chart analysis always runs fresh, with no free-recheck cache;
  - `chart_reading` is stored on the analysis.
- `deps.latest_completed_analysis_for` excludes chart analyses, so they're never served as someone else's cached result.
- `pipeline.run_analysis(..., chart_reading=None)` appends the context block and stores `chart_check`.
- `server.py`: registers the router; adds a TTL index on `chart_readings.expires_at` and a unique index on `id`.
- `limits.py`: the three chart limiters (env-tunable: `RL_CHART_READ_*`).
- Tests: `tests/test_chart_analysis.py` (15 tests, offline; the vision call and DB are faked).

## ⚠️ Check first: does the AI library send images?

`chart_reader.read_chart` uses `from emergentintegrations.llm.chat import ImageContent` with
`UserMessage(text=..., file_contents=[ImageContent(image_base64=...)])`. I couldn't verify this against
emergentintegrations 0.2.0 from my side. If that import or argument doesn't exist, the endpoint returns 503
"Chart reading isn't available right now". Please check:

```bash
cd /app/backend && python3 -c "from emergentintegrations.llm.chat import ImageContent, UserMessage; import inspect; print(inspect.signature(ImageContent)); print(inspect.signature(UserMessage))"
```
If the names differ, adapt ONLY `read_chart()` to the library's real image API, keeping the same model
(`MODEL_PROVIDER` / `MODEL_NAME` from core). Then report what you changed.

## Verify

```bash
cd /app/backend && sudo supervisorctl restart backend
python -m pytest -n 0 -q tests/test_chart_analysis.py tests/test_scanners.py tests/test_no_committed_secrets.py
```

Then run a real end-to-end check with a test account:

1. Take a screenshot of a TradingView or Yahoo chart for RELIANCE.NS (daily) and upload it via the web preview
   (Analyse a chart). Report the reading JSON and the candidates.
2. Confirm RELIANCE.NS, run the desk, and report:
   - the `chart_check` rows;
   - whether the Technical Analyst's message mentions the user's chart;
   - that no agent advises buy/sell.
3. Upload a non-chart image (for example a photo). It should say "doesn't look like a price chart", and no
   reading should be stored.
4. Upload a 6 MB file. It should be refused with 413.
5. Confirm the image isn't stored anywhere: no file written, and `chart_readings` holds only the JSON reading.

No iOS build. No secrets in commits or replies.
