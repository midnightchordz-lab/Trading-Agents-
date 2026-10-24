# Portfolio report, with mutual funds

Branch: `feature/portfolio-report`. The owner wanted the Portfolio tab to read like a statement: one row per stock or fund showing type, invested, worth now, return %, and a short verdict.

The owner's example had a "Review Action" column (Hold / Add / Exit). That is SEBI research-analyst activity, so it is replaced by a factual **"what we see"** line plus the AI desk's latest outlook. A test fails if any row says buy, sell, hold, add, exit, remove, trim, switch, should, recommend, multibagger or accumulate.

## What users see (Portfolio tab)
- **Add holding** has a STOCK / MUTUAL FUND switch.
  - Fund mode: search the fund by name (from AMFI's list of every scheme), pick it, then enter units and amount invested. The app stores the average NAV paid.
- **YOUR PORTFOLIO** replaces the old holdings list.
  - Summary at the top: invested, worth now, return (% and ₹), "3 up · 2 down".
  - One row per holding, biggest first:
    - name and type ("Large-cap · Utilities", "Mid-cap fund");
    - "₹50,000 → ₹72,000" and the return in green or red;
    - the notes line, e.g. "Your biggest gainer · AI desk: Bullish (12 Oct)".
  - Possible notes:
    - your biggest gainer or biggest fall;
    - "33 of every 100 of your money" (20% or more of the portfolio);
    - "The company is losing money (latest results)";
    - for a fund, "Past year +21% · Nifty 50 +8%" (equity) or "Past year +6.8%" (debt and others);
    - the AI desk outlook, if a stock was analysed in the last 45 days.
  - Rupee amounts use Indian grouping (₹3,74,000).
- **Funds are left out** of the split comparison and the Health Check for now. Both screens say so.

## Backend
- `backend/mutual_funds.py`:
  - Parses AMFI's daily `NAVAll.txt` (scheme code, name, NAV, date, and the category from the header line). The file is cached for 4 hours, and a failed refresh keeps the last good copy.
  - Gives each category a plain name and an asset kind (equity, debt, hybrid).
  - `search()` puts Direct and Growth plans first.
  - `year_return()` uses mfapi.in history, cached for 12 hours. It is optional: the note is left out if mfapi fails.
- `backend/portfolio_report.py`: pure builder for rows, notes, totals and summary.
  - Company size bands (approximate): ≥ ₹1 lakh crore is large-cap, ≥ ₹30,000 crore mid-cap, below that small-cap. For US stocks: $10bn and $2bn.
- `backend/routes/portfolio.py`:
  - `POST /api/portfolio/report`: up to 50 holdings, rate-limited like `/portfolio/optimize`. Fund symbols are `MF:<scheme code>`.
    - Stocks: Yahoo quote, fundamentals (cached 12 h) for market value and profit margin, and sector.
    - Funds: AMFI NAV and mfapi 1-year return. The Nifty 50's 1-year change is the comparison.
    - The AI desk outlook comes from the latest completed analysis for that stock.
  - `GET /api/mf/search?q=`: rate-limited like `/search`.
- **No new packages and no new environment variables.** It needs outbound access to `www.amfiindia.com` and `api.mfapi.in`. My sandbox blocks both, so they were tested offline with a copy of the file format.

## Tests
- `backend/tests/test_portfolio_report.py`, 32 offline tests:
  - AMFI parsing, plain category names, fund symbols, search, 1-year change, last-good-copy fallback;
  - the owner's 5-holding example (₹3,40,000 invested → ₹3,74,000, +10%, 3 up / 2 down);
  - each note, debt funds not compared with the Nifty, unpriced rows, mixed currencies, size bands, outlook age;
  - the no-advice check;
  - the endpoint with all upstream calls faked, bad requests refused, and fund search.

## Emergent: please verify
1. Merge `main` and restart the backend and Expo. No `yarn install` is needed.
2. Run `pytest backend/tests/test_portfolio_report.py backend/tests/test_portfolio_optimizer.py backend/tests/test_portfolio_health.py`.
3. Live checks, which only you can run (my sandbox can't reach AMFI or mfapi):
   - `curl 'localhost:8001/api/mf/search?q=parag%20parikh%20flexi'` returns results with NAV and type "Flexi-cap fund".
   - `POST /api/portfolio/report` with `{"holdings":[{"symbol":"TATAPOWER.NS","quantity":125,"avg_price":400},{"symbol":"MF:<a code from the search>","quantity":100,"avg_price":50}],"cash":0}` returns both rows priced, each with a type. The fund row should have a "Past year …" note if mfapi answers.
   - Report how long the report call takes with about 8 holdings.
4. In the preview (390 px):
   - Add 3 stocks and 2 funds (use MUTUAL FUND mode, search by name, enter units and amount).
   - Screenshot the YOUR PORTFOLIO card in full.
   - Confirm the Health Check and the split comparison say funds aren't included.
   - Report any text that overflows.
5. Clean up test data by ID only. Leave `AUTH_DEBUG_RETURN_OTP=false`, and never print tokens or secrets.
