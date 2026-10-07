# Bullish / Neutral / Bearish outlook instead of BUY / SELL / HOLD (branch `feature/outlook-labels`)

Builds on `fix/import-upload` @ `d87ae0a` (which builds on your `1bd496d`).
If you already merged fix/import-upload, this fast-forwards:

```bash
cd /app
git fetch https://github.com/midnightchordz-lab/trading-agents-.git feature/outlook-labels
git merge --ff-only FETCH_HEAD
```
Do not force-push, rebase or rewrite history. Save to GitHub from the UI.

## Why

In India a public buy/sell call is SEBI Research Analyst activity, and an
"educational only" disclaimer does not exempt it. The app now presents a
market outlook (the bullish and bearish case, where the outlook points and
what would invalidate it) instead of instructing anyone to buy or sell.

## Design: display changes, data does not

- The stored verdict keeps `decision: BUY | SELL | HOLD`. The re-check cache
  (`wallet.py`), price alerts, the portfolio optimizer, the compare score and
  the Play build already on phones all read it. Nothing is migrated.
- New field `outlook: BULLISH | NEUTRAL | BEARISH` on the verdict and on each
  timeframe. The app derives the label from `decision` via `outlookLabel()`,
  so old analyses display correctly too.
- The LLM is now ASKED for BULLISH|NEUTRAL|BEARISH. `normalize_decision()`
  maps either vocabulary to the internal enum, so an older-style answer still parses.

## Changes

- `backend/pipeline.py`
  - New `OUTLOOK_NOT_ADVICE` rule on the Bull, Bear, Research Manager,
    Trader, Risk, PM, debate and timeframe prompts: never tell the reader to
    buy/sell/hold/enter/exit/add/trim/short, never "we recommend", never size a position.
  - PM/timeframe JSON asks for BULLISH|NEUTRAL|BEARISH. target_price means
    "level the outlook points toward"; stop_loss means "level that would invalidate it".
  - The decision message reads "FINAL OUTLOOK: BULLISH — CONFIDENCE n%".
  - Grounding-check messages are reworded (outlook level / invalidation level / upside-downside).
  - The language directive's enum example is updated.
- `backend/mailer.py`: welcome email says "Bullish, Neutral or Bearish… analysis, not investment advice".
- Frontend:
  - `theme.ts`: `outlookLabel()`; `verdictColors` also accepts the new words.
  - Verdict block: "DESK OUTLOOK".
  - Badges, history, compare and timeframes show BULLISH/NEUTRAL/BEARISH.
  - Wherever these labels appear (levels row, stats grid, chart price lines, compare, timeframes, position sizer):
    - TARGET → OUTLOOK LEVEL
    - STOP LOSS → INVALIDATION
    - "BUY ENTRY" → "BULLISH · REFERENCE".
  - Alert test IDs are unchanged (`alert-toggle-target`, `alert-toggle-stop-loss`).
  - Saved alerts with the old "TARGET"/"STOP LOSS" labels still colour and read correctly.
  - Agent blurbs, the "1 OUTLOOK" subtitle, the OUTLOOK tab and "SHARE THIS OUTLOOK" are updated.
  - i18n `verdict.buy/sell/hold/target/stop_loss` updated in en/hi/es/zh/ar.
- Tests: `tests/test_outlook_labels.py` (new); `test_i18n.py` and `test_mailer.py` updated.
  114 backend tests pass locally.

## Not changed (flagged to the owner for a legal view)

- **Position Sizer** still computes "N shares" against the levels.
- **Portfolio tab** "Keep, trim or sell — optimized" and the ADD/HOLD/TRIM/SELL actions.
- Stored debate messages in OLD analyses still say "FINAL VERDICT: BUY". They are history; new runs say outlook.

## Verify

```bash
cd /app/backend && sudo supervisorctl restart backend && sleep 4
python -m pytest -n 0 -q tests/test_outlook_labels.py tests/test_timeframes.py tests/test_i18n.py \
  tests/test_grounding.py tests/test_mailer.py tests/test_pipeline_technicals.py
```
Then run ONE fresh analysis (e.g. RELIANCE.NS) and paste verbatim:
- the stored `verdict` JSON, including `decision` + `outlook`;
- the Portfolio Manager's decision message;
- the Bull, Bear and Trader messages.

Confirm none of them tells the reader to buy, sell, hold, enter, exit or size a position.

In the web preview, check that the verdict block, history pill, timeframes, levels rows and an old analysis all show
BULLISH/NEUTRAL/BEARISH and OUTLOOK LEVEL / INVALIDATION. Check Hindi too.

No iOS build. Android build only after the owner reviews.
