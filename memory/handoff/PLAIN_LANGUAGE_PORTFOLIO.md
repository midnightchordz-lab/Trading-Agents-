# Plain language on Portfolio and Health Check

Branch: `feature/plain-language-portfolio`. The owner's feedback: most users are not finance people, and terms like "Sharpe", "HRP" and "SBIN 17.8% yours · 8.4% model" mean nothing to them.

## Portfolio: the model comparison card
- **Title:** "Compare with a model mix" is now "How is your money split?", with one line explaining what the sample mix is.
- **Mix types:** HRP / MAX SHARPE / MIN VOL are now BALANCED / BEST FOR RISK / STEADIEST, each with a one-line plain blurb.
- **Black-Litterman toggle:** "Also use our AI desk's latest read on these stocks, where there is one".
- **Results:**
  - A one-sentence summary at the top, e.g. "Compared with the sample mix, you have more money in SBIN, and less in SUNPHARMA".
  - Each row shows the stock without ".NS", a badge (MORE THAN MIX / LESS THAN MIX / ABOUT THE SAME / NOT IN MIX), and "You: 18 of every 100 invested · Sample mix: 8".
- **Stats:** "Sample mix: past yearly gain" and "Typical yearly swing". The model's Sharpe figure is gone from this card.
- **"About the same" rule (backend):** within 2 points, or within a quarter of the model's weight if that is larger. It used to be a fixed 5 points, which called 10.3% vs 6.4% "in line". The backend values (ABOVE MODEL etc.) are unchanged, so older app builds still work.

## Health Check
- **Spread score:** shown as "How spread out is your money?", with a label of "Mostly in a few stocks" / "Fairly spread out" / "Well spread out". Correlation numbers are now words ("often move together").
- **Market card:** shows one stat, "When the Nifty 50 moves 1%, you move 1.1%". Beta, R² and correlation are removed.
- **Gains vs the bumps:** "Gain in the year", "Typical yearly swing", "Gain for the bumps (higher is better)" and "Biggest drop from a high". Sharpe is explained in one sentence.
- **Frontier card:** "Could the same stocks have done better?", with plain axis labels and "Missed gain, same bumps".
- **Other cards:** "Stocks that move as one", "Industries" and "Easy to sell?" are worded plainly.
- **"What stands out" (backend `observations`):**
  - rewritten in plain sentences, e.g. "18 of every 100 you have invested", "went up and down almost together";
  - Sharpe is described as earning more or less than the Nifty "for the ups and downs";
  - stock names are shown without ".NS".
  - They are still descriptive only; the no-advice test still passes.

## Emergent: please verify
1. Merge `main`, restart the backend and Expo, and run `pytest backend/tests/test_portfolio_health.py backend/tests/test_portfolio_optimizer.py backend/tests/test_portfolio_api.py`.
2. In the preview, add 4 or more Indian stocks:
   - Run the comparison with each of the three mix types.
   - Run the Health Check.
   - Take screenshots of both at phone width (390 px).
   - Confirm that no "HRP", "Sharpe ratio" row label, "Beta", "R²", "efficient frontier" or "model" wording remains on screen, and that every line fits without awkward wrapping.
3. Make no other changes. Never print tokens or secrets.
