// Static metadata about the agent desk + analysis pipeline (used by the Agents tab
// and for phase grouping across the app).

export type PhaseKey = "analysis" | "debate" | "trade" | "risk" | "decision";

export const PHASE_LABEL: Record<PhaseKey, string> = {
  analysis: "ANALYST TEAM",
  debate: "RESEARCH DEBATE",
  trade: "TRADE DESK",
  risk: "RISK REVIEW",
  decision: "FINAL DECISION",
};

export const PHASE_ORDER: PhaseKey[] = ["analysis", "debate", "trade", "risk", "decision"];

export type AgentDef = {
  tag: string;
  name: string;
  team: string;
  blurb: string;
  icon: string; // phosphor icon name
};

export const AGENT_ROSTER: AgentDef[] = [
  { tag: "TECHNICAL_ANALYST", name: "Technical Analyst", team: "ANALYST TEAM", blurb: "Reads price action, trend, MACD & RSI to time the move.", icon: "ChartLineUp" },
  { tag: "FUNDAMENTALS_ANALYST", name: "Fundamentals Analyst", team: "ANALYST TEAM", blurb: "Weighs valuation, growth, margins and balance-sheet strength.", icon: "Scales" },
  { tag: "SENTIMENT_ANALYST", name: "Sentiment Analyst", team: "ANALYST TEAM", blurb: "Gauges crowd mood from social chatter and retail flow.", icon: "ChatCircle" },
  { tag: "NEWS_ANALYST", name: "News Analyst", team: "ANALYST TEAM", blurb: "Tracks headlines, catalysts and macro conditions.", icon: "Newspaper" },
  { tag: "BULL_RESEARCHER", name: "Bull Researcher", team: "RESEARCH TEAM", blurb: "Builds the strongest case to BUY and rebuts the bear.", icon: "TrendUp" },
  { tag: "BEAR_RESEARCHER", name: "Bear Researcher", team: "RESEARCH TEAM", blurb: "Builds the strongest case to SELL and rebuts the bull.", icon: "TrendDown" },
  { tag: "RESEARCH_MANAGER", name: "Research Manager", team: "RESEARCH TEAM", blurb: "Judges the debate and sets the recommended stance.", icon: "Gavel" },
  { tag: "TRADER", name: "Trader", team: "EXECUTION", blurb: "Turns research into an entry, target and stop-loss plan.", icon: "Lightning" },
  { tag: "RISK_MANAGER", name: "Risk Manager", team: "EXECUTION", blurb: "Stress-tests volatility, liquidity and position sizing.", icon: "ShieldWarning" },
  { tag: "PORTFOLIO_MANAGER", name: "Portfolio Manager", team: "EXECUTION", blurb: "Makes the final BUY / SELL / HOLD call for the book.", icon: "Briefcase" },
];

export const PIPELINE_STEPS = [
  "4 analysts brief the desk in parallel",
  "Bull & Bear debate across 2 rounds",
  "Research Manager rules on the winner",
  "Trader drafts the trade plan",
  "Risk Manager stress-tests it",
  "Portfolio Manager issues the verdict",
];

export function tagLabel(tag: string): string {
  return tag.replace(/_/g, " ");
}
