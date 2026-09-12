// Derives a per-ticker "Fear & Greed" score (0-100) from the analysis the desk
// already produced — no extra data source. It blends the committee verdict +
// confidence, the balance of bullish/bearish agent signals, and 1-day momentum.

import type { Analysis } from "@/src/api";
import { colors, accents } from "@/src/theme";

export type FearGreed = {
  score: number; // 0-100
  label: string;
  color: string;
  blurb: string;
};

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

export function computeFearGreed(analysis: Analysis | null | undefined): FearGreed {
  const messages = analysis?.messages || [];
  let bull = 0;
  let bear = 0;
  messages.forEach((m) => {
    if (m.sentiment === "bullish") bull += 1;
    else if (m.sentiment === "bearish") bear += 1;
  });
  const total = bull + bear;
  const sentimentScore = total > 0 ? (bull - bear) / total : 0; // [-1, 1]

  const verdict = analysis?.verdict;
  let verdictScore = 0; // [-1, 1]
  if (verdict) {
    const dir = verdict.decision === "BUY" ? 1 : verdict.decision === "SELL" ? -1 : 0;
    verdictScore = dir * (verdict.confidence / 100);
  }

  const chg = analysis?.quote?.changePercent;
  const momentumScore = chg == null ? 0 : clamp(chg / 5, -1, 1); // [-1, 1]

  const composite = 0.5 * verdictScore + 0.3 * sentimentScore + 0.2 * momentumScore; // [-1, 1]
  const score = Math.round(((composite + 1) / 2) * 100); // [0, 100]

  return { score, ...band(score) };
}

function band(score: number): { label: string; color: string; blurb: string } {
  if (score < 25) return { label: "EXTREME FEAR", color: colors.error, blurb: "The desk sees heavy downside risk." };
  if (score < 45) return { label: "FEAR", color: accents.orange, blurb: "Caution dominates the committee." };
  if (score <= 55) return { label: "NEUTRAL", color: accents.amber, blurb: "The team is split down the middle." };
  if (score <= 74) return { label: "GREED", color: accents.lime, blurb: "Momentum and conviction lean bullish." };
  return { label: "EXTREME GREED", color: colors.success, blurb: "Strong conviction to the upside." };
}
