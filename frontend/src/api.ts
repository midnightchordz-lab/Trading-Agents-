// API client for the TradingAgents backend.
const BASE = process.env.EXPO_PUBLIC_BACKEND_URL;
const API = `${BASE}/api`;

export type SearchResult = {
  symbol: string;
  name: string;
  exchange?: string;
  type?: string;
};

export type Quote = {
  symbol: string;
  name: string;
  price: number | null;
  previousClose: number | null;
  change: number | null;
  changePercent: number | null;
  currency?: string;
  exchange?: string;
  dayHigh?: number;
  dayLow?: number;
  fiftyTwoWeekHigh?: number;
  fiftyTwoWeekLow?: number;
  sparkline: number[];
};

export type AgentMessageT = {
  id: string;
  agent: string;
  tag: string;
  phase: "analysis" | "debate" | "trade" | "risk" | "decision";
  content: string;
  sentiment: "bullish" | "bearish" | "neutral" | null;
  ts: string;
};

export type Verdict = {
  decision: "BUY" | "SELL" | "HOLD";
  confidence: number;
  target_price: number | null;
  stop_loss: number | null;
  time_horizon: string;
  summary: string;
  key_risks: string[];
};

export type Debate = {
  bull: string;
  bear: string;
  fundamentals: string;
  agreements: string[];
  disagreements: string[];
  recommendation: string;
};

export type ChartData = {
  symbol: string;
  range: string;
  points: number[];
  last: number | null;
  change: number | null;
  changePercent: number | null;
  currency?: string;
};

export type Analysis = {
  id: string;
  symbol: string;
  name: string;
  status: "running" | "completed" | "error";
  messages: AgentMessageT[];
  quote: Quote | null;
  verdict: Verdict | null;
  debate: Debate | null;
  current_step: number;
  total_steps: number;
  error: string | null;
  created_at: string;
  updated_at: string;
};

async function j<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body?.detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  search: (q: string) => j<{ results: SearchResult[] }>(`/search?q=${encodeURIComponent(q)}`),
  quote: (symbol: string) => j<Quote>(`/quote/${encodeURIComponent(symbol)}`),
  chart: (symbol: string, range: string) =>
    j<ChartData>(`/chart/${encodeURIComponent(symbol)}?range=${encodeURIComponent(range)}`),
  trending: () => j<{ results: Quote[] }>(`/trending`),
  markets: (category: string) => j<{ results: Quote[] }>(`/markets/${category}`),
  analyze: (symbol: string, name?: string) =>
    j<Analysis>(`/analyze`, { method: "POST", body: JSON.stringify({ symbol, name }) }),
  getAnalysis: (id: string) => j<Analysis>(`/analysis/${id}`),
  history: () => j<{ results: Analysis[] }>(`/history`),
  remove: (id: string) => j<{ ok: boolean }>(`/analysis/${id}`, { method: "DELETE" }),
};
